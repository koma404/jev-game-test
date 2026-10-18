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
ART = HERE / "art"            # 开场镜头的像素画（python -m twelve.art.case1 生成）
AUDIO = HERE / "audio"        # 本地配乐：不进仓库（借用的曲子只在自己电脑上放），有就优先用
MUSIC = HERE / "music"        # 发布用的配乐：自己做的曲子，进仓库，线上版用这个
MUSIC_SLOTS = ("intro", "trial", "reversal", "gavel")   # gavel：木槌声（一下），没有就用页面合成的
AUDIO_TYPES = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".wav": "audio/wav"}
_art_cache: dict = {}


def load_art(name: str):
    if name not in _art_cache:
        p = ART / f"{name}.json"
        _art_cache[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return _art_cache[name]


def music_path(slot: str):
    """intro / trial / reversal 的音乐文件：先找本地的 audio/，再找仓库里的 music/；没有返回 None。"""
    for folder in (AUDIO, MUSIC):
        for ext in AUDIO_TYPES:
            f = folder / f"{slot}{ext}"
            if f.exists():
                return f
    return None


def music_files() -> dict:
    out = {}
    for slot in MUSIC_SLOTS:
        f = music_path(slot)
        if f:
            out[slot] = f"/audio/{slot}{f.suffix}?v={int(f.stat().st_mtime)}"   # 换了曲子浏览器不会用旧缓存
    return out


def load_case(name: str = "case1", folder=None) -> dict:
    p = Path(name) if name.endswith(".json") else Path(folder or HERE / "cases") / f"{name}.json"
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def beta_path(name: str = "case1", folder=None) -> Path:
    p = Path(name) if name.endswith(".json") else Path(folder or HERE / "cases") / f"{name}.json"
    return p.with_name(p.stem + ".beta.json")


def merge_case(case: dict, patch: dict) -> dict:
    """内测版 = 发布版 + 补丁：config / texts 按键覆盖，jurors 按 id 覆盖字段，其余键整体替换。"""
    out = json.loads(json.dumps(case))
    for k, v in patch.items():
        if k.startswith("_"):
            continue                                   # _note 之类的说明
        if k in ("config", "texts") and isinstance(v, dict):
            out[k].update(v)
        elif k == "jurors" and isinstance(v, list):
            by_id = {j["id"]: j for j in out["jurors"]}
            for pj in v:
                by_id[pj["id"]].update(pj)
        else:
            out[k] = v
    return out


def list_cases(only: str = "", folder=None) -> list:
    """cases/ 下的所有案子（不含 .beta.json 补丁），按 order 排；only 指定时只要那一个。"""
    if only and only != "all":
        return [only]
    names = [p.stem for p in Path(folder or HERE / "cases").glob("*.json") if not p.stem.endswith(".beta")]
    return sorted(names, key=lambda n: (load_case(n, folder).get("order", 99), n))


def make_client(kind: str, case: dict):
    """真实 Jev 所有案子共用一个（复用连接）；离线 mock 按案子各建一个（关键词在案子里）。"""
    from game.jev_client import RealJev
    if kind == "mock" or (kind == "auto" and not RealJev.available()):
        return None
    from .link import KeepAliveJev
    c = KeepAliveJev()
    threading.Thread(target=c.warm, daemon=True).start()   # 先把连接建好
    return c


class Court:
    """一个浏览器的一场庭审。"""

    def __init__(self, case, client, logbook=None, player="?"):
        # 每庭一份自己的案子：庭审中途的事件会改案卷、陪审员在意的点，不能影响别人
        self.case = json.loads(json.dumps(case))
        self.client = client
        self.judge = Judge(self.case, client)
        self.trial = Trial(self.case)
        self.fired: set = set()          # 已经触发过的事件
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
        self.channel = "release"         # release = 发布版；beta = 用管理口令进来的内测版

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
            self.logbook.write(event, cid=self.cid, player=self.player, channel=self.channel,
                               case=self.case.get("id"), **fields)

    def check_events(self, force: bool = False) -> list:
        """庭审中途的事件（检方出示新证据之类）：说了 after_says 句以后、或剩余时间 ≤ at_time_left 时触发，每个只触发一次。"""
        t = self.trial
        fired = []
        if t.started_at is None or t.over:
            return fired
        for ev in self.case.get("events", []):
            if ev["id"] in self.fired:
                continue
            due = force or ("after_says" in ev and len(t.history) >= ev["after_says"]) or \
                  ("at_time_left" in ev and t.time_left() <= ev["at_time_left"])
            if not due:
                continue
            self.fired.add(ev["id"])
            t.apply_event(ev)
            self.judge = Judge(self.case, self.client)   # 案卷 / 在意点变了，题目要重组
            self.cache.clear()
            fired.append({"id": ev["id"], "title": ev.get("title", "庭审中断"), "text": ev.get("text", ""),
                          "dossier": ev.get("dossier", []), "dossier_view": ev.get("dossier_view"),
                          "short": ev.get("short", ""), "time": ev.get("time", 0)})
            self.log("event", id=ev["id"], votes=t.votes(), leans={k: round(v, 2) for k, v in t.leans.items()})
        return fired

    def end(self, verdict: dict, reason: str):
        """裁决只记一次（主动结束、时间到、说话时时间到，都会走到这里）。"""
        if self.ended:
            return
        self.ended = True
        t = self.trial
        used = None if t.started_at is None else round(t.cfg["time_limit"] - t.time_left(), 1)
        self.log("verdict", reason=reason, mode=t.mode, acquit=verdict["acquit"], votes=verdict["votes"],
                 leans={k: round(v, 2) for k, v in t.leans.items()},
                 says=len(t.history), previews=self.previews, hints=self.hints, manip=t.manip_strikes,
                 used_s=used, cost=round(self.cost, 6),
                 latency={k: latency(v) for k, v in self.lat.items()})


def public_case(case: dict) -> dict:
    # 只给玩家看得见的东西：陪审员在意什么、讨厌什么是要靠观察去猜的
    return {"id": case.get("id"), "title": case["title"], "brief": case.get("brief_view") or case["brief"], "dossier": case["dossier"],
            "dossier_view": case.get("dossier_view"),
            "examples": case.get("examples", []), "stars": case.get("stars", 1),
            "intro": [{"text": s.get("text", ""), "pix": load_art(s["art"]) if s.get("art") else None}
                      for s in case.get("intro", [])],
            "time_limit": case["config"]["time_limit"], "win_votes": case["config"]["win_votes"],
            "modes": {k: {"label": v.get("label", k), "time_limit": v.get("time_limit", case["config"]["time_limit"]),
                          "max_says": v.get("max_says", 0), "blurb": v.get("blurb", "")} for k, v in case.get("modes", {}).items()},
            "max_chars": case["config"]["max_chars"], "quote": case["config"].get("quote_max", 0), "debounce": case["config"]["preview_debounce_ms"],
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

    def _recent(self, ip, now):
        hits = [t for t in self.opens.get(ip, []) if now - t < 3600]
        self.opens[ip] = hits
        return hits

    def can_open(self, ip) -> bool:
        """只查不记：这个 IP 这一小时还能不能再开一庭。拿不到真实 IP 时不限（免得所有玩家共用一个额度）。"""
        if ip is None or not self.per_ip:
            return True
        with self.lock:
            return len(self._recent(ip, time.time())) < self.per_ip

    def record_open(self, ip):
        """真正开始陈词时才记一次（打开页面、看开场、刷新都不算）。"""
        if ip is None or not self.per_ip:
            return
        with self.lock:
            self._recent(ip, time.time()).append(time.time())

    def wait_minutes(self, ip) -> int:
        with self.lock:
            hits = self._recent(ip, time.time())
            return max(1, int((3600 - (time.time() - min(hits))) // 60) + 1) if hits else 0

    def allow_open(self, ip) -> bool:
        """查并记一次（旧接口，测试和脚本用）。"""
        if not self.can_open(ip):
            return False
        self.record_open(ip)
        return True


class App:
    def __init__(self, args):
        # 所有案子；每个案子有发布版和内测版（发布版 + cases/<案子>.beta.json 补丁；没有补丁就一样）
        # beta_only 的案子只在内测版里出现：新关卡先给管理口令的人试玩
        self.cases: dict = {}
        folder = getattr(args, "cases_dir", None)
        for name in list_cases(getattr(args, "case", "") or "all", folder):
            case = load_case(name, folder)
            case.setdefault("id", name)
            bp = beta_path(name, folder)
            beta = merge_case(case, json.load(open(bp, encoding="utf-8"))) if bp.exists() else case
            self.cases[case["id"]] = {"release": case, "beta": beta}
        first = next(iter(self.cases.values()))["release"]
        self.case = first                                  # 默认案子（管理页显示名字等）
        self.client = make_client(args.jev, first)
        self.backend = self.client.name if self.client else "mock"
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
                           getattr(args, "daily_budget", 1.0), getattr(args, "max_courts_per_ip", 12), state)

    def client_for(self, case):
        return self.client or MockClient(case)

    def visible_cases(self, beta: bool) -> list:
        out = []
        for cid, v in self.cases.items():
            c = v["beta" if beta else "release"]
            if c.get("beta_only") and not beta:
                continue
            out.append({"id": cid, "title": c["title"], "stars": c.get("stars", 1), "blurb": c.get("blurb", ""),
                        "win_votes": c["config"]["win_votes"], "time_limit": c["config"]["time_limit"],
                        "beta_only": bool(c.get("beta_only")),
                        "thumb": load_art(c["intro"][0]["art"]) if c.get("intro") and c["intro"][0].get("art") else None,   # 选关卡片的缩略图：用开场第一个镜头
                        "modes": [{"label": m.get("label", k), "time_limit": m.get("time_limit", c["config"]["time_limit"]),
                                   "max_says": m.get("max_says", 0)} for k, m in c.get("modes", {}).items()]})
        return out

    @staticmethod
    def _finish_if_out(c):
        """简单模式说满句数：这句话的反应照常返回，同时附上裁决（页面看完反应再显示）。"""
        t = c.trial
        if t.out_of_says:
            v = t.verdict()
            c.end(v, "says")
            return v
        return None

    @staticmethod
    def _events(c, force=False) -> dict:
        fired = c.check_events(force)
        if not fired:
            return {}
        return {"events": fired, "leans": c.trial.leans, "votes": c.trial.votes(), "dossier": c.case["dossier"],
                "dossier_view": c.case.get("dossier_view")}

    def _pick_case(self, body, beta):
        visible = [c["id"] for c in self.visible_cases(beta)]
        want = str(body.get("case") or "")
        if want in visible:
            return want
        return visible[0] if len(visible) == 1 else None

    def court(self, sid, create=False, player="?", beta=False, case_id=None):
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
            case = self.cases[case_id]["beta" if beta else "release"]
            self.courts[sid] = Court(case, self.client_for(case), self.logbook, player)
            if beta:
                self.courts[sid].channel = "beta"
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
        everyone = {"jurors": [j for v in self.cases.values() for j in v["beta"]["jurors"]]}
        g = self.guard
        head = (f"进程启动于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.started))}；"
                f"内存中的庭 {len(self.courts)} 个（闲置 2 小时清理）；今日花费 ${g.spent:.4f} / "
                f"{'$%.2f' % g.budget if g.budget > 0 else '不限'}")
        key = quote((parse_qs(query).get("key") or [""])[0])
        return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                "<title>十二人 · 运行日志</title><body style='font:14px/1.5 monospace;margin:16px'>"
                f"<p>{html.escape(head)}</p><p><a href='/admin/log?key={key}'>下载完整日志（jsonl）</a></p>"
                f"<pre style='white-space:pre-wrap'>{html.escape(report(events, everyone))}</pre>")

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
            beta = bool(self.admin_code) and secrets.compare_digest(str(body.get("code") or ""), self.admin_code)
            if not beta and not self.guard.code_ok(body.get("code")):
                refused = "code" if body.get("code") else None     # 第一次进来没带口令不算被拦
                r = 401, {"need_code": True, "error": "请输入访问口令。" if not body.get("code") else "口令不对。"}, sid
            elif self.guard.over_budget():
                refused, r = "budget", (503, {"error": "今天的体验额度已经用完了，明天再来吧。"}, sid)
            elif not self._pick_case(body, beta):
                # 没选案子：给选关列表（只有一个能玩的案子时直接进）
                r = 200, {"cases": self.visible_cases(beta), "channel": "beta" if beta else "release",
                          "music": music_files()}, sid
            elif not beta and not self.guard.can_open(ip):      # 管理口令（自己测试）不限
                refused, r = "per_ip", (429, {"error": f"这一小时开庭太多次了，约 {self.guard.wait_minutes(ip)} 分钟后再来。"}, sid)
            else:
                sid, c = self.court(sid, create=True, player=player, beta=beta, case_id=self._pick_case(body, beta))
                if c is None:
                    refused, r = "full", (503, {"error": "同时开庭的人太多了，请稍后再试。"}, sid)
                else:
                    c.ip = ip
                    c.log("open", ua=str(ua)[:200], courts=len(self.courts))
                    r = 200, {"case": public_case(c.case), "backend": self.backend, "channel": c.channel,
                              "music": music_files(),
                              "cases": len(self.visible_cases(beta)),
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
                t.set_mode(str(body.get("mode") or ""))
                c.log("begin", mode=t.mode)
                if c.channel != "beta":
                    self.guard.record_open(getattr(c, "ip", None))
            t.begin()
            return 200, {"time_left": t.time_left(), "mode": t.mode, "max_says": t.cfg.get("max_says", 0)}, sid
        if path == "/api/hint":
            if t.over:
                return over()
            h = t.hint()
            c.hints += 1
            c.log("hint", juror=h["juror"], level=t.hinted.get(h["juror"]), time_left=round(t.time_left(), 1))
            return 200, {**h, **self._events(c), "time_left": t.time_left()}, sid
        if path == "/api/verdict":
            if not t.over:
                # 还有没发生的事件（检方的反转）：先让它发生，玩家面对之后再决定要不要结束——反转躲不掉
                pending = self._events(c, force=True)
                if pending:
                    return 200, {**pending, "time_left": t.time_left(), "continue": True}, sid
                t.rested = True
            v = t.verdict()
            c.end(v, "rested" if t.rested else "timeout")
            return 200, {"verdict": v}, sid
        text = str(body.get("text", "")).strip()
        if t.over:
            return over()
        if len(text) > c.case["config"]["max_chars"]:
            return 200, {"error": c.case["texts"]["too_long"].format(n=c.case["config"]["max_chars"])}, sid
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
            ev = self._events(c)                 # 按剩余时间触发的事件，在打字时也要能触发
            if len(text) < 4:
                return 200, {"faces": {j["id"]: 0 for j in c.case["jurors"]}, "ms": 0, "text": text, **ev}, sid
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
            return 200, {"faces": faces, "ms": round(jd.ms), "text": text, **ev,
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
                ev = self._events(c)                 # 说够几句后触发的事件（检方出示新证据之类）
                fin = self._finish_if_out(c)         # 简单模式说满句数就宣判
                auto = None
                cfg = c.case["config"]
                if (not ev and t.stall >= cfg.get("stall_hint", 0) > 0 and sp.votes < cfg["win_votes"]
                        and not jd.manip > cfg["manip_threshold"]):
                    auto = t.hint(free=True)        # 连着几句没涨票：主动送一次提示，不扣时间
                    t.stall = 0
                    c.hints += 1
                    c.log("hint", juror=auto["juror"], level=t.hinted.get(auto["juror"]), auto=True,
                          time_left=round(t.time_left(), 1))
                return 200, {"faces": t.faces(sp.deltas), "reactions": sp.reactions, "notes": sp.notes,
                             "votes": t.votes(), "need": c.case["config"]["win_votes"], "leans": t.leans, "ms": round(jd.ms),
                             "auto_hint": auto, **ev, "time_left": t.time_left(),
                             **({"verdict": fin} if fin else {})}, sid
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
            elif path.startswith("/audio/"):
                name = path[len("/audio/"):]
                slot, _, ext = name.partition(".")
                f = music_path(slot) if slot in MUSIC_SLOTS and "/" not in name else None
                if f is None or f.suffix != "." + ext:
                    return self._send(404, b"not found", "text/plain")
                data = f.read_bytes()
                start, end = 0, len(data) - 1
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):            # 有的浏览器（Safari）播放音频要分段取
                    a, _, b = rng[6:].split(",")[0].partition("-")
                    try:
                        if a:
                            start, end = int(a), min(int(b) if b else end, end)
                        else:
                            start = max(0, len(data) - int(b))
                    except ValueError:
                        start, end = 0, len(data) - 1
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", AUDIO_TYPES[f.suffix])
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                self.wfile.write(data[start:end + 1])
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
    ap.add_argument("--case", default="all", help="只开某一个案子（默认 all：cases/ 下全部，玩家选关）")
    ap.add_argument("--cases-dir", default=None, help="案子目录（默认 twelve/cases；调试用）")
    ap.add_argument("--max-sessions", type=int, default=30)
    ap.add_argument("--access-code", default="", help="访问口令；也可用环境变量 TWELVE_ACCESS_CODE。挂公网时务必设置")
    ap.add_argument("--daily-budget", type=float, default=1.0, help="每天最多花多少美元调用 Jev，超过当天停用（0 = 不限）")
    ap.add_argument("--max-courts-per-ip", type=int, default=12, help="同一 IP 每小时最多开始几局（点了「开始陈词」才算；管理口令不限；0 = 不限）")
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
    print(f"十二人 已开庭（Jev 后端: {app.backend}）；案子："
          + "、".join(v["release"]["title"] + ("（仅内测）" if v["release"].get("beta_only") else "") for v in app.cases.values()))
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
