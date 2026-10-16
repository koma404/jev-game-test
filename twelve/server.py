"""《十二人》网页版：python -m twelve.server [--port 8000] [--jev auto|mock|real]

只用标准库。浏览器每停顿一下就把草稿发来预览（只返回三档表情），按回车才算说出口。
游戏规则、计时、key 都在服务端；每个浏览器（cookie）各开一庭。"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from game.jev_client import JevError
from .judge import Judge, MockClient
from .logbook import Logbook, latency
from .trial import Trial

HERE = Path(__file__).parent
IDLE_SECONDS = 2 * 3600


def load_case(name: str = "case1") -> dict:
    p = Path(name) if name.endswith(".json") else HERE / "cases" / f"{name}.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def make_client(kind: str, case: dict):
    from game.jev_client import RealJev
    if kind == "mock" or (kind == "auto" and not RealJev.available()):
        return MockClient(case)
    from .link import KeepAliveJev
    c = KeepAliveJev()
    threading.Thread(target=c.warm, daemon=True).start()   # 先把连接建好
    return c


class Court:
    """一个浏览器的一场庭审。"""

    def __init__(self, case, judge, logbook=None, player="?"):
        self.case = case
        self.judge = judge
        self.trial = Trial(case)
        self.seen = time.time()
        self.cache: dict = {}            # 草稿 -> Judgment；预览过的原句直接说出口时不用再等
        self.say_lock = threading.Lock()
        self.logbook = logbook
        self.cid = secrets.token_hex(4)  # 日志里的庭号（cookie 本身不进日志）
        self.player = player
        self.opened = time.time()
        self.last_preview = 0.0
        self.on_cost = None
        self.cost = 0.0
        self.previews = 0
        self.hints = 0
        self.ended = False
        self.lat = {"jev": [], "server": [], "browser": []}   # 本局每次预览的耗时（毫秒）

    def judged(self, text: str):
        """返回 (判断, 是否命中缓存)。"""
        jd = self.cache.get(text)
        if jd is not None:
            return jd, True
        jd = self.judge.judge(text)
        self.cost += jd.cost
        if self.on_cost:
            self.on_cost(jd.cost)
        if len(self.cache) > 50:
            self.cache.clear()
        self.cache[text] = jd
        return jd, False

    def log(self, event, **fields):
        if self.logbook:
            self.logbook.write(event, cid=self.cid, player=self.player, **fields)

    def end(self, verdict: dict, reason: str):
        """裁决只记一次（主动结束、时间到、说话时时间到，都会走到这里）。"""
        if self.ended:
            return
        self.ended = True
        t = self.trial
        used = None if t.started_at is None else round(t.cfg["time_limit"] - t.time_left(), 1)
        self.log("verdict", reason=reason, acquit=verdict["acquit"], votes=verdict["votes"],
                 leans={k: round(v, 2) for k, v in t.leans.items()},
                 says=len(t.history), previews=self.previews, hints=self.hints, manip=t.manip_strikes,
                 used_s=used, cost=round(self.cost, 6),
                 latency={k: latency(v) for k, v in self.lat.items()})


def public_case(case: dict) -> dict:
    # 只给玩家看得见的东西：陪审员在意什么、讨厌什么是要靠观察去猜的
    return {"title": case["title"], "brief": case["brief"], "dossier": case["dossier"],
            "time_limit": case["config"]["time_limit"], "win_votes": case["config"]["win_votes"],
            "max_chars": case["config"]["max_chars"], "debounce": case["config"]["preview_debounce_ms"],
            "swing_band": case["config"].get("swing_band", 0), "hint_cost": case["config"].get("hint_cost", 10),
            "jurors": [{"id": j["id"], "name": j["name"], "role": j["role"], "persona": j["persona"]}
                       for j in case["jurors"]]}


class Guard:
    """挂到公网时的三道闸：访问口令、每日花费上限、按 IP 限制开庭次数。"""

    def __init__(self, access_code="", daily_budget=1.0, per_ip_hourly=6, state_file=None):
        self.code = access_code or ""
        self.budget = float(daily_budget)
        self.per_ip = int(per_ip_hourly)
        self.state_file = state_file
        self.lock = threading.Lock()
        self.day, self.spent = time.strftime("%Y-%m-%d"), 0.0
        self.opens: dict = {}
        if state_file and os.path.exists(state_file):   # 重启不清零
            try:
                d = json.load(open(state_file, encoding="utf-8"))
                if d.get("day") == self.day:
                    self.spent = float(d.get("spent", 0))
            except (OSError, ValueError):
                pass

    def _roll(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.spent = today, 0.0

    def over_budget(self) -> bool:
        with self.lock:
            self._roll()
            return self.budget > 0 and self.spent >= self.budget

    def charge(self, usd: float):
        if not usd:
            return
        with self.lock:
            self._roll()
            self.spent += usd
            if self.state_file:
                try:
                    with open(self.state_file, "w", encoding="utf-8") as f:
                        json.dump({"day": self.day, "spent": round(self.spent, 6)}, f)
                except OSError:
                    pass

    def code_ok(self, given) -> bool:
        return not self.code or secrets.compare_digest(str(given or ""), self.code)

    def allow_open(self, ip) -> bool:
        if ip is None:       # 拿不到真实 IP：不按 IP 限流，免得所有玩家共用一个额度
            return True
        now = time.time()
        with self.lock:
            hits = [t for t in self.opens.get(ip, []) if now - t < 3600]
            if self.per_ip and len(hits) >= self.per_ip:
                self.opens[ip] = hits
                return False
            hits.append(now)
            self.opens[ip] = hits
            return True


class App:
    def __init__(self, args):
        self.case = load_case(args.case)
        self.client = make_client(args.jev, self.case)
        self.judge = Judge(self.case, self.client)
        self.log_dir = args.log_dir
        self.admin_code = getattr(args, "admin_code", "") or os.environ.get("TWELVE_ADMIN_CODE", "")
        self.logbook = Logbook(args.log_dir, echo=getattr(args, "log_echo", True),
                               salt=os.environ.get("TWELVE_ACCESS_CODE", "") or "twelve")
        self.max_sessions = args.max_sessions
        self.courts: dict = {}
        self.lock = threading.Lock()
        self.started = time.time()
        state = os.path.join(args.log_dir, "budget.json") if args.log_dir else None
        if state:
            os.makedirs(args.log_dir, exist_ok=True)
        self.guard = Guard(getattr(args, "access_code", "") or os.environ.get("TWELVE_ACCESS_CODE", ""),
                           getattr(args, "daily_budget", 1.0), getattr(args, "max_courts_per_ip", 6), state)

    def court(self, sid, create=False, player="?"):
        now = time.time()
        with self.lock:
            for k in [k for k, c in self.courts.items() if now - c.seen > IDLE_SECONDS]:
                if not self.courts[k].ended:
                    self.courts[k].log("abandon", says=len(self.courts[k].trial.history),
                                       previews=self.courts[k].previews)
                del self.courts[k]
            if sid in self.courts and not create:
                self.courts[sid].seen = now
                return sid, self.courts[sid]
            if not create:
                return sid, None
            if sid not in self.courts and len(self.courts) >= self.max_sessions:
                return sid, None
            sid = sid if sid in self.courts else secrets.token_hex(16)
            self.courts[sid] = Court(self.case, self.judge, self.logbook, player)
            self.courts[sid].on_cost = self.guard.charge
            return sid, self.courts[sid]

    # ---- 管理页：/admin?key=管理口令 看统计，/admin/log?key=... 下载日志 ----
    def admin_ok(self, query: str) -> bool:
        from urllib.parse import parse_qs
        given = (parse_qs(query).get("key") or [""])[0]
        return bool(self.admin_code) and secrets.compare_digest(given, self.admin_code)

    def admin_page(self, query: str) -> str:
        import html
        from urllib.parse import parse_qs, quote
        from .stats import load, report
        events = load([self.logbook.path]) if self.logbook.path and os.path.exists(self.logbook.path) else []
        g = self.guard
        head = (f"进程启动于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.started))}；"
                f"内存中的庭 {len(self.courts)} 个（闲置 2 小时清理）；今日花费 ${g.spent:.4f} / "
                f"{'$%.2f' % g.budget if g.budget > 0 else '不限'}")
        key = quote((parse_qs(query).get("key") or [""])[0])
        return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                "<title>十二人 · 运行日志</title><body style='font:14px/1.5 monospace;margin:16px'>"
                f"<p>{html.escape(head)}</p><p><a href='/admin/log?key={key}'>下载完整日志（jsonl）</a></p>"
                f"<pre style='white-space:pre-wrap'>{html.escape(report(events, self.case))}</pre>")

    # ---- 接口 ----
    def api(self, path, body, sid, ip="?", ua=""):
        t0 = time.time()
        player = self.logbook.player(ip)
        if path == "/api/client_error":   # 浏览器端报错：打印出来，方便排查各家浏览器的问题
            msg = {k: str(body.get(k, ""))[:300] for k in ("msg", "line", "col", "ua")}
            self.logbook.write("client_error", player=player, **msg)
            return 200, {}, sid
        if path == "/api/new":
            refused, r = None, None
            if not self.guard.code_ok(body.get("code")):
                refused = "code" if body.get("code") else None     # 第一次进来没带口令不算被拦
                r = 401, {"need_code": True, "error": "请输入访问口令。" if not body.get("code") else "口令不对。"}, sid
            elif self.guard.over_budget():
                refused, r = "budget", (503, {"error": "今天的体验额度已经用完了，明天再来吧。"}, sid)
            elif not self.guard.allow_open(ip):
                refused, r = "per_ip", (429, {"error": "你开庭太频繁了，休息一会儿再来。"}, sid)
            else:
                sid, c = self.court(sid, create=True, player=player)
                if c is None:
                    refused, r = "full", (503, {"error": "同时开庭的人太多了，请稍后再试。"}, sid)
                else:
                    c.log("open", ua=str(ua)[:200], courts=len(self.courts))
                    r = 200, {"case": public_case(self.case), "backend": self.client.name,
                              "leans": c.trial.leans, "votes": c.trial.votes()}, sid
            if refused:
                self.logbook.write("refused", player=player, reason=refused)
            return r
        sid, c = self.court(sid)
        if c is None:
            return 409, {"error": "这一庭已经结束或过期，请重新开庭。", "expired": True}, sid
        t = c.trial

        def over():
            v = t.verdict()
            c.end(v, "rested" if t.rested else "timeout")
            return 200, {"over": True, "verdict": v}, sid

        if path == "/api/begin":
            if t.started_at is None:
                c.log("begin")
            t.begin()
            return 200, {"time_left": t.time_left()}, sid
        if path == "/api/hint":
            if t.over:
                return over()
            h = t.hint()
            c.hints += 1
            c.log("hint", juror=h["juror"], level=t.hinted.get(h["juror"]), time_left=round(t.time_left(), 1))
            return 200, {**h, "time_left": t.time_left()}, sid
        if path == "/api/verdict":
            if not t.over:
                t.rested = True
            v = t.verdict()
            c.end(v, "rested" if t.rested else "timeout")
            return 200, {"verdict": v}, sid
        text = str(body.get("text", "")).strip()
        if t.over:
            return over()
        if len(text) > self.case["config"]["max_chars"]:
            return 200, {"error": self.case["texts"]["too_long"].format(n=self.case["config"]["max_chars"])}, sid
        if path in ("/api/preview", "/api/say") and self.guard.over_budget():
            return 200, {"error": "今天的体验额度已经用完了。可以结束陈词看看裁决。"}, sid
        if path == "/api/preview":
            prev = body.get("prev")          # 浏览器测到的上一次预览的总耗时（发出到收到）
            if isinstance(prev, (int, float)) and 0 < prev < 60000:
                c.lat["browser"].append(float(prev))
            now = time.time()
            if now - c.last_preview < 0.15:      # 防脚本刷接口；正常打字一次往返远大于这个间隔
                return 200, {"skip": True}, sid
            c.last_preview = now
            if len(text) < 4:
                return 200, {"faces": {j["id"]: 0 for j in self.case["jurors"]}, "ms": 0, "text": text}, sid
            jd, cached = c.judged(text)
            faces = t.preview(jd)
            srv = (time.time() - t0) * 1000
            c.previews += 1
            if not cached:
                c.lat["jev"].append(jd.ms)
            c.lat["server"].append(srv)
            c.log("preview", text=text, cached=cached, jev_ms=None if cached else round(jd.ms),
                  server_ms=round(srv), browser_prev_ms=prev if isinstance(prev, (int, float)) else None,
                  up=sum(1 for v in faces.values() if v > 0), down=sum(1 for v in faces.values() if v < 0))
            return 200, {"faces": faces, "ms": round(jd.ms), "text": text,
                         "time_left": t.time_left()}, sid
        if path == "/api/say":
            if not text:
                return 200, {"error": "你还什么都没说。"}, sid
            if not c.say_lock.acquire(blocking=False):
                return 429, {"error": "上一句还没说完。"}, sid
            try:
                t.begin()
                jd, cached = c.judged(text)
                if t.over:   # 判断期间时间到了：这句不算
                    return over()
                sp = t.speak(text, jd)
                h = dict(t.history[-1], deltas={k: round(v, 3) for k, v in t.history[-1]["deltas"].items()})
                c.log("say", **h, cached=cached,
                      jev_ms=None if cached else round(jd.ms), server_ms=round((time.time() - t0) * 1000),
                      leans={k: round(v, 2) for k, v in t.leans.items()}, time_left=round(t.time_left(), 1))
                auto = None
                cfg = self.case["config"]
                if (t.stall >= cfg.get("stall_hint", 0) > 0 and sp.votes < cfg["win_votes"]
                        and not jd.manip > cfg["manip_threshold"]):
                    auto = t.hint(free=True)        # 连着几句没涨票：主动送一次提示，不扣时间
                    t.stall = 0
                    c.hints += 1
                    c.log("hint", juror=auto["juror"], level=t.hinted.get(auto["juror"]), auto=True,
                          time_left=round(t.time_left(), 1))
                return 200, {"faces": t.faces(sp.deltas), "reactions": sp.reactions, "notes": sp.notes,
                             "votes": sp.votes, "need": self.case["config"]["win_votes"], "leans": t.leans, "ms": round(jd.ms),
                             "auto_hint": auto, "time_left": t.time_left()}, sid
            finally:
                c.say_lock.release()
        return 404, {"error": "not found"}, sid


def make_handler(app: App):
    page = (HERE / "page.html").read_bytes()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"   # 浏览器到这里也复用连接
        app = None   # 下面赋值，测试里用来拿到 App

        def _ip(self):
            peer = self.client_address[0]
            fwd = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For")
            # 部署在 Render 等平台后面时（TWELVE_TRUST_PROXY=1），对端是平台的负载均衡，真实 IP 只在代理头里
            if os.environ.get("TWELVE_TRUST_PROXY") == "1":
                return fwd.split(",")[0].strip() if fwd else None
            # 经内网穿透/Cloudflare 隧道进来时，对端是本机，真实 IP 在代理头里；外部直连时不信这个头
            if peer in ("127.0.0.1", "::1"):
                if fwd:
                    return fwd.split(",")[0].strip()
                # 本机连进来、又没带真实 IP：可能是本机自己，也可能是不传 IP 的内网穿透。
                # 这时没法按 IP 区分玩家，返回 None 跳过按 IP 限流（口令和每日花费上限照常生效）
                return None
            return peer

        def _sid(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "tsid":
                    return v
            return None

        def _send(self, code, body: bytes, ctype, sid=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if sid:
                self.send_header("Set-Cookie", f"tsid={sid}; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html"):   # 分享链接会带 ?code=口令
                self._send(200, page, "text/html; charset=utf-8")
            elif path in ("/admin", "/admin/log") and app.admin_ok(query):
                if path == "/admin":
                    self._send(200, app.admin_page(query).encode("utf-8"), "text/html; charset=utf-8")
                else:
                    data = b""
                    if app.logbook.path and os.path.exists(app.logbook.path):
                        with open(app.logbook.path, "rb") as f:
                            data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="twelve-%s.jsonl"' % time.strftime("%Y%m%d-%H%M"))
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(min(n, 16384)) if n else b""
                body = json.loads(raw.decode("utf-8") or "{}") if raw else {}
                if not isinstance(body, dict):
                    raise ValueError
            except (ValueError, UnicodeDecodeError):
                return self._send(400, b'{"error":"bad request"}', "application/json")
            try:
                code, obj, sid = app.api(self.path, body, self._sid(), self._ip(), self.headers.get("User-Agent", ""))
            except JevError as e:
                app.logbook.write("jev_error", path=self.path, error=str(e)[:300])
                code, obj, sid = 502, {"error": f"Jev 暂时没有响应：{e}"}, self._sid()
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", sid)

        def log_message(self, fmt, *a):
            if os.environ.get("TWELVE_QUIET"):
                return
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    H.app = app
    return H


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="十二人 —— 网页版")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)),
                    help="默认 8000；部署平台（Render 等）通过环境变量 PORT 指定")
    ap.add_argument("--jev", choices=["auto", "mock", "real"], default="auto")
    ap.add_argument("--case", default="case1")
    ap.add_argument("--max-sessions", type=int, default=30)
    ap.add_argument("--access-code", default="", help="访问口令；也可用环境变量 TWELVE_ACCESS_CODE。挂公网时务必设置")
    ap.add_argument("--daily-budget", type=float, default=1.0, help="每天最多花多少美元调用 Jev，超过当天停用（0 = 不限）")
    ap.add_argument("--max-courts-per-ip", type=int, default=6, help="同一 IP 每小时最多开几庭（0 = 不限）")
    ap.add_argument("--log-dir", default="twelve/logs")
    ap.add_argument("--admin-code", default="", help="管理口令，开启 /admin?key=口令（统计与日志下载）；也可用环境变量 TWELVE_ADMIN_CODE")
    args = ap.parse_args(argv)
    try:
        app = App(args)
    except JevError as e:
        print(f"无法启用真实 Jev: {e}", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    srv.daemon_threads = True
    from game.web import _lan_ips
    print(f"{app.case['title']} 已开庭（Jev 后端: {app.client.name}）")
    for ip in _lan_ips():
        print(f"  局域网访问： http://{ip}:{args.port}")
    print(f"  本机访问：   http://127.0.0.1:{args.port}")
    g = app.guard
    print(f"  访问口令：{'已设置' if g.code else '未设置（挂公网前务必用 --access-code 设置）'}"
          f"；每日花费上限：{'$%.2f（今日已用 $%.4f）' % (g.budget, g.spent) if g.budget > 0 else '不限'}"
          f"；每 IP 每小时最多开庭：{g.per_ip or '不限'}")
    if app.admin_code:
        print(f"  运行日志：http://127.0.0.1:{args.port}/admin?key=管理口令（统计、下载日志）")
    print(f"  日志文件：{app.logbook.path or '（不写文件）'}；除打字预览外每个事件也打印成一行 LOG")
    print("  按 Ctrl+C 停止")
    os.environ.setdefault("TWELVE_QUIET", "1")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
