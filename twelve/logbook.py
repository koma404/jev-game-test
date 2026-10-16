"""运行日志：每个事件一行 JSON，写到 <log_dir>/twelve.jsonl。

除了打字预览（一局几百条），其余事件同时打印一行 `LOG {...}` 到标准输出：
部署在 Render 这类磁盘不保留的平台上时，平台的日志页面里也能看到、搜到、复制出来，
复制下来的文本可以直接交给 `python -m twelve.stats` 统计。

事件（字段见 server.py 里写日志的地方）：
  open      开庭（玩家 = IP 的哈希，不存原始 IP；浏览器 UA）
  refused   开庭被拦下（口令不对 / 额度用完 / 开庭太频繁 / 人满）
  begin     开始陈词
  preview   打字预览：草稿、Jev 往返、服务端耗时、浏览器测到的上一次预览总耗时
  say       说出口：判断、数值变化、票数
  hint      提示
  verdict   裁决：结果、每人倾向、本局句数/预览数/提示数/花费/用时、本局延迟统计
  jev_error Jev 出错
  client_error 浏览器脚本报错
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time

QUIET_EVENTS = {"preview"}


class Logbook:
    def __init__(self, log_dir: str = "", echo: bool = True, salt: str = ""):
        self.log_dir = log_dir
        self.echo = echo
        self.salt = salt
        self.lock = threading.Lock()
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    @property
    def path(self):
        return os.path.join(self.log_dir, "twelve.jsonl") if self.log_dir else None

    def player(self, ip) -> str:
        # 只存哈希：能数出有几个不同的玩家，看不出是谁
        if not ip:
            return "?"
        return hashlib.sha256((self.salt + "|" + ip).encode()).hexdigest()[:8]

    def write(self, event: str, **fields):
        entry = {"ts": round(time.time(), 3), "t": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **fields}
        line = json.dumps(entry, ensure_ascii=False)
        with self.lock:
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except OSError as e:
                    print(f"[日志写入失败] {e}", file=sys.stderr, flush=True)
            if self.echo and event not in QUIET_EVENTS:
                print("LOG " + line, flush=True)


def pct(values, p):
    """第 p 百分位（0–100），没有数据返回 None。"""
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def latency(values) -> dict:
    v = [x for x in values if x is not None]
    if not v:
        return {}
    return {"n": len(v), "p50": round(pct(v, 50)), "p90": round(pct(v, 90)), "max": round(max(v))}
