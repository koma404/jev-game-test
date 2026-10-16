"""局域网网页版：python -m game.web [--host 0.0.0.0] [--port 8000] [--jev auto|mock|real]

只用标准库（Win7 + Python 3.8 可跑）。游戏逻辑、key、日志都留在这台机器上，
浏览器只收发文字；每个浏览器（按 cookie）各开一局，互不影响。"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .cli import make_jev
from .engine import Engine, TurnLogger
from .flavor import FlavorWriter
from .jev_client import JevError, RealJev
from .world import World, load_level

MAX_INPUT = 300          # 单句最长字数
IDLE_SECONDS = 2 * 3600  # 闲置多久回收一局


class Session:
    def __init__(self, world: World, jev, logger, flavor_on: bool):
        self.lock = threading.Lock()   # 同一局一次只处理一句，防止连点
        self.seen = time.time()
        self.world = world
        self.writer = FlavorWriter.from_jev(jev) if flavor_on and isinstance(jev, RealJev) else None
        self.flavor_fails = 0
        self.eng = Engine(world, jev, logger=logger,
                          refuse_writer=self._flavor if self.writer else None)
        self.done = False

    def _flavor(self, said, scene, fallback):
        if self.flavor_fails >= 3:
            return fallback
        self.writer.spoilers = tuple(self.world.active_spoilers(self.eng.state))
        text = self.writer.write(said, scene, fallback)
        self.flavor_fails = self.flavor_fails + 1 if self.writer.last_error else 0
        return text

    def opening(self) -> list:
        lv = self.world.level
        return [{"kind": "title", "text": lv["title"]}, {"kind": "story", "text": lv["intro"]},
                {"kind": "story", "text": self.world.describe(self.eng.state)},
                {"kind": "meta", "text": "直接输入你想做的事。也可以用 /look /inv /clues /hint"}]

    def say(self, line: str) -> list:
        w, eng = self.world, self.eng
        if self.done:
            return [{"kind": "meta", "text": "你已经通关了。刷新页面可以重新开始。"}]
        if line == "/look":
            return [{"kind": "story", "text": w.describe(eng.state)}]
        if line == "/inv":
            return [{"kind": "meta", "text": "物品: " + (", ".join(w.item_name(i) for i in sorted(eng.state.items)) or "无")}]
        if line == "/clues":
            return [{"kind": "meta", "text": "线索: " + ("; ".join(eng.clues) or "无")}]
        if line == "/hint":
            return [{"kind": "hint", "text": eng.hint()}]
        if line.startswith("/"):
            return [{"kind": "meta", "text": "不认识这个命令。可用：/look /inv /clues /hint"}]
        t0 = time.time()
        res = eng.turn(line)
        out = [{"kind": "story", "text": res.text}]
        if res.clue:
            out.append({"kind": "clue", "text": f"获得线索 [{res.clue}]"})
        if res.info.get("nudge"):
            out.append({"kind": "meta", "text": "好像卡住了？输入 /hint，或直接说「给我提示」。"})
        if res.kind == "jev_down":
            out.append({"kind": "meta", "text": "原因: " + str(res.info.get("error"))})
        if res.done:
            self.done = True
        out.append({"kind": "time", "text": f"{time.time() - t0:.1f} 秒"})
        return out


class Game:
    def __init__(self, args):
        self.world_data = load_level(args.level)
        self.jev = make_jev(args.jev)
        self.logger = TurnLogger(args.log_dir or None)
        self.flavor_on = not args.no_flavor
        self.max_sessions = args.max_sessions
        self.sessions: dict = {}
        self.lock = threading.Lock()

    def get(self, sid: str | None):
        now = time.time()
        with self.lock:
            for k in [k for k, s in self.sessions.items() if now - s.seen > IDLE_SECONDS]:
                del self.sessions[k]
            if sid and sid in self.sessions:
                s = self.sessions[sid]
                s.seen = now
                return sid, s, False
            if len(self.sessions) >= self.max_sessions:
                return None, None, False
            sid = secrets.token_hex(16)
            s = Session(World(self.world_data), self.jev, self.logger, self.flavor_on)
            self.sessions[sid] = s
            return sid, s, True

    def reset(self, sid):
        with self.lock:
            self.sessions.pop(sid, None)


PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>停电的档案馆</title>
<style>
:root{--bg:#f6f4ef;--fg:#222;--dim:#777;--card:#fff;--line:#ddd;--accent:#1f6f8b;--clue:#8a5a00;--hint:#5b4b8a}
@media (prefers-color-scheme: dark){:root{--bg:#16181b;--fg:#e6e6e6;--dim:#8b8f96;--card:#1f2226;--line:#30343a;--accent:#6cc3e0;--clue:#e0b45c;--hint:#b3a3e6}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 "PingFang SC","Microsoft YaHei",sans-serif}
#wrap{max-width:760px;margin:0 auto;padding:16px;display:flex;flex-direction:column;height:100vh}
#log{flex:1;overflow-y:auto;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px}
.l{margin:6px 0;white-space:pre-wrap;word-break:break-word}
.title{font-size:20px;font-weight:bold;color:var(--accent)}
.you{color:var(--accent)} .you:before{content:"> "}
.meta,.time{color:var(--dim);font-size:14px} .time{text-align:right}
.clue{color:var(--clue);font-weight:bold} .hint{color:var(--hint)}
form{display:flex;gap:8px;margin-top:12px}
input{flex:1;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
button{font:inherit;padding:10px 16px;border:0;border-radius:6px;background:var(--accent);color:#fff;cursor:pointer}
button:disabled{opacity:.5}
#bar{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
#bar button{padding:4px 10px;font-size:14px;background:transparent;color:var(--accent);border:1px solid var(--line)}
</style></head>
<body><div id="wrap">
<div id="log"></div>
<form id="f"><input id="in" maxlength="300" autocomplete="off" placeholder="直接输入你想做的事……"><button id="go">发送</button></form>
<div id="bar"><button data-c="/look">看看周围</button><button data-c="/inv">物品</button><button data-c="/clues">线索</button><button data-c="/hint">提示</button><button data-c="__reset">重新开始</button></div>
</div>
<script>
(function(){
  var log=document.getElementById('log'),inp=document.getElementById('in'),go=document.getElementById('go'),busy=false;
  function add(kind,text){var d=document.createElement('div');d.className='l '+kind;d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;return d;}
  function post(path,body,cb){
    var x=new XMLHttpRequest();x.open('POST',path,true);x.setRequestHeader('Content-Type','application/json');
    x.onreadystatechange=function(){if(x.readyState!==4)return;var r=null;try{r=JSON.parse(x.responseText);}catch(e){}cb(x.status,r);};
    x.send(JSON.stringify(body||{}));
  }
  function show(r){if(!r||!r.lines)return;for(var i=0;i<r.lines.length;i++)add(r.lines[i].kind,r.lines[i].text);}
  function send(text){
    if(busy||!text)return;busy=true;go.disabled=true;
    if(text==='__reset'){log.innerHTML='';post('/api/reset',{},function(s,r){busy=false;go.disabled=false;show(r);inp.focus();});return;}
    add('you',text);var wait=add('meta','（等待 Jev 回应……）');
    post('/api/say',{text:text},function(s,r){
      log.removeChild(wait);busy=false;go.disabled=false;
      if(s!==200){add('meta',(r&&r.error)||('出错了（'+s+'），请重试'));}else show(r);inp.focus();
    });
  }
  document.getElementById('f').onsubmit=function(e){e.preventDefault();var t=inp.value.replace(/^\s+|\s+$/g,'');inp.value='';send(t);return false;};
  var bs=document.getElementById('bar').getElementsByTagName('button');
  for(var i=0;i<bs.length;i++){bs[i].onclick=function(){send(this.getAttribute('data-c'));};}
  post('/api/start',{},function(s,r){if(s!==200)add('meta',(r&&r.error)||'无法开始游戏');else show(r);inp.focus();});
})();
</script></body></html>"""


def make_handler(game: Game):
    class H(BaseHTTPRequestHandler):
        def _sid(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "sid":
                    return v
            return None

        def _send(self, code, body: bytes, ctype, sid=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if sid:
                self.send_header("Set-Cookie", f"sid={sid}; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj, sid=None):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", sid)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(n, 8192)).decode("utf-8") or "{}") if n else {}
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": "请求格式不对"})
            sid = self._sid()
            if self.path == "/api/reset":
                game.reset(sid)
                sid = None
            if self.path in ("/api/start", "/api/reset", "/api/say"):
                sid, s, new = game.get(sid)
                if s is None:
                    return self._json(503, {"error": "同时在玩的人太多了，请稍后再试。"})
                if self.path != "/api/say" or new:
                    lines = s.opening()
                    if self.path == "/api/say":
                        lines.append({"kind": "meta", "text": "（上一局已过期，已为你重新开始。）"})
                    return self._json(200, {"lines": lines}, sid)
                text = str(body.get("text", "")).strip()[:MAX_INPUT]
                if not text:
                    return self._json(200, {"lines": []}, sid)
                if not s.lock.acquire(blocking=False):
                    return self._json(429, {"error": "上一句还在处理，稍等一下。"}, sid)
                try:
                    return self._json(200, {"lines": s.say(text)}, sid)
                finally:
                    s.lock.release()
            self._send(404, b"not found", "text/plain")

        def log_message(self, fmt, *a):   # 安静一点：只记请求行
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % a))

    return H


def _lan_ips():
    import socket
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # 不会真的发包，只为拿到出口网卡的地址
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips) or ["本机IP"]


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="停电的档案馆 —— 局域网网页版")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址，默认所有网卡（局域网可访问）")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--jev", choices=["auto", "mock", "real"], default="auto")
    ap.add_argument("--level")
    ap.add_argument("--no-flavor", action="store_true", help="关闭旁白")
    ap.add_argument("--max-sessions", type=int, default=30, help="同时进行的最多局数")
    ap.add_argument("--log-dir", default="game/logs")
    args = ap.parse_args(argv)
    try:
        game = Game(args)
    except JevError as e:
        print(f"无法启用真实 Jev: {e}", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(game))
    srv.daemon_threads = True
    print(f"停电的档案馆 网页版已启动（后端: {game.jev.name}）")
    for ip in _lan_ips():
        print(f"  局域网访问： http://{ip}:{args.port}")
    print(f"  本机访问：   http://127.0.0.1:{args.port}")
    print("  按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
