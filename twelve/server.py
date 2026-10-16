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

    def __init__(self, case, judge, log_dir):
        self.case = case
        self.judge = judge
        self.trial = Trial(case)
        self.seen = time.time()
        self.cache: dict = {}            # 草稿 -> Judgment；预览过的原句直接说出口时不用再等
        self.say_lock = threading.Lock()
        self.log_dir = log_dir

    def judged(self, text: str):
        jd = self.cache.get(text)
        if jd is None:
            jd = self.judge.judge(text)
            if len(self.cache) > 50:
                self.cache.clear()
            self.cache[text] = jd
        return jd

    def log(self, entry):
        if not self.log_dir:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        with open(os.path.join(self.log_dir, "twelve.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **entry}, ensure_ascii=False) + "\n")


def public_case(case: dict) -> dict:
    # 只给玩家看得见的东西：陪审员在意什么、讨厌什么是要靠观察去猜的
    return {"title": case["title"], "brief": case["brief"], "dossier": case["dossier"],
            "time_limit": case["config"]["time_limit"], "win_votes": case["config"]["win_votes"],
            "max_chars": case["config"]["max_chars"], "debounce": case["config"]["preview_debounce_ms"],
            "jurors": [{"id": j["id"], "name": j["name"], "role": j["role"], "persona": j["persona"]}
                       for j in case["jurors"]]}


class App:
    def __init__(self, args):
        self.case = load_case(args.case)
        self.client = make_client(args.jev, self.case)
        self.judge = Judge(self.case, self.client)
        self.log_dir = args.log_dir
        self.max_sessions = args.max_sessions
        self.courts: dict = {}
        self.lock = threading.Lock()

    def court(self, sid, create=False):
        now = time.time()
        with self.lock:
            for k in [k for k, c in self.courts.items() if now - c.seen > IDLE_SECONDS]:
                del self.courts[k]
            if sid in self.courts and not create:
                self.courts[sid].seen = now
                return sid, self.courts[sid]
            if not create:
                return sid, None
            if sid not in self.courts and len(self.courts) >= self.max_sessions:
                return sid, None
            sid = sid if sid in self.courts else secrets.token_hex(16)
            self.courts[sid] = Court(self.case, self.judge, self.log_dir)
            return sid, self.courts[sid]

    # ---- 接口 ----
    def api(self, path, body, sid):
        if path == "/api/new":
            sid, c = self.court(sid, create=True)
            if c is None:
                return 503, {"error": "同时开庭的人太多了，请稍后再试。"}, sid
            return 200, {"case": public_case(self.case), "backend": self.client.name,
                         "leans": c.trial.leans, "votes": c.trial.votes()}, sid
        sid, c = self.court(sid)
        if c is None:
            return 409, {"error": "这一庭已经结束或过期，请重新开庭。", "expired": True}, sid
        t = c.trial
        if path == "/api/begin":
            t.begin()
            return 200, {"time_left": t.time_left()}, sid
        if path == "/api/hint":
            if t.over:
                return 200, {"over": True, "verdict": t.verdict()}, sid
            h = t.hint()
            c.log({"event": "hint", **h})
            return 200, {**h, "time_left": t.time_left()}, sid
        if path == "/api/verdict":
            t.rested = True
            v = t.verdict()
            c.log({"event": "verdict", **{k: v[k] for k in ("acquit", "votes")}, "leans": t.leans})
            return 200, {"verdict": v}, sid
        text = str(body.get("text", "")).strip()
        if t.over:
            return 200, {"over": True, "verdict": t.verdict()}, sid
        if len(text) > self.case["config"]["max_chars"]:
            return 200, {"error": self.case["texts"]["too_long"].format(n=self.case["config"]["max_chars"])}, sid
        if path == "/api/preview":
            if len(text) < 4:
                return 200, {"faces": {j["id"]: 0 for j in self.case["jurors"]}, "ms": 0, "text": text}, sid
            jd = c.judged(text)
            return 200, {"faces": t.preview(jd), "ms": round(jd.ms), "text": text,
                         "time_left": t.time_left()}, sid
        if path == "/api/say":
            if not text:
                return 200, {"error": "你还什么都没说。"}, sid
            if not c.say_lock.acquire(blocking=False):
                return 429, {"error": "上一句还没说完。"}, sid
            try:
                t.begin()
                jd = c.judged(text)
                if t.over:   # 判断期间时间到了：这句不算
                    return 200, {"over": True, "verdict": t.verdict()}, sid
                sp = t.speak(text, jd)
                c.log({"event": "say", **t.history[-1], "ms": round(jd.ms)})
                return 200, {"faces": t.faces(sp.deltas), "reactions": sp.reactions, "notes": sp.notes,
                             "votes": sp.votes, "need": self.case["config"]["win_votes"], "leans": t.leans, "ms": round(jd.ms),
                             "time_left": t.time_left()}, sid
            finally:
                c.say_lock.release()
        return 404, {"error": "not found"}, sid


def make_handler(app: App):
    page = (HERE / "page.html").read_bytes()

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"   # 浏览器到这里也复用连接

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
            if self.path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
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
                code, obj, sid = app.api(self.path, body, self._sid())
            except JevError as e:
                code, obj, sid = 502, {"error": f"Jev 暂时没有响应：{e}"}, self._sid()
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", sid)

        def log_message(self, fmt, *a):
            if os.environ.get("TWELVE_QUIET"):
                return
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    return H


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="十二人 —— 网页版")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--jev", choices=["auto", "mock", "real"], default="auto")
    ap.add_argument("--case", default="case1")
    ap.add_argument("--max-sessions", type=int, default=30)
    ap.add_argument("--log-dir", default="twelve/logs")
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
