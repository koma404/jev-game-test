"""网络测速：python -m game.netcheck
分步计时 DNS / TCP 连接 / TLS 握手 / 一次完整 Jev 请求，找出慢在哪一步。"""
from __future__ import annotations

import os
import socket
import ssl
import time
import urllib.request
from urllib.parse import urlparse

from .flavor import FlavorWriter
from .jev_client import DEFAULT_BASE_URL, RealJev


def step(name, fn):
    t = time.time()
    try:
        out = fn()
        print(f"  {name:<22}{time.time() - t:6.2f} 秒")
        return out
    except Exception as e:  # noqa: BLE001
        print(f"  {name:<22}失败 ({time.time() - t:.2f} 秒): {type(e).__name__}: {e}")
        return None


def main():
    url = os.environ.get("TYPESAFE_BASE_URL", DEFAULT_BASE_URL)
    host = urlparse(url).hostname
    port = urlparse(url).port or (443 if url.startswith("https") else 80)
    print(f"目标: {url}")
    proxies = {k: v for k, v in urllib.request.getproxies().items() if k in ("http", "https")}
    print(f"系统代理: {proxies or '无'}")
    if proxies:
        print("  （有代理时，下面的 DNS/TCP/TLS 是直连测试，可能与实际路径不同，以最后一项为准）")
    infos = step("DNS 解析", lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
    if infos:
        print("    地址:", ", ".join(sorted({i[4][0] for i in infos}))[:150])
        sock = step("TCP 连接", lambda: socket.create_connection((host, port), timeout=15))
        if sock and url.startswith("https"):
            step("TLS 握手", lambda: ssl.create_default_context().wrap_socket(sock, server_hostname=host))
    if not RealJev.available():
        print("未配置 key，跳过 Jev 请求测试")
        return
    q = {"x": {"type": "choice", "instructions": "选一个", "criteria": {"a": "A", "b": "B"}}}
    modes = [("当前设置", None)]
    if proxies:
        modes.append(("强制直连", "direct"))
    for label, proxy in modes:
        jev = RealJev(proxy=proxy, max_retries=0)
        for i in (1, 2, 3):
            step(f"Jev 请求 #{i}（{label}）", lambda: jev.ask({"t": "hi"}, q))
        w = FlavorWriter.from_jev(jev)
        if w:
            def chat():
                out = w.write("把墙砸了", "大厅", "(回退)")
                if w.last_error:
                    raise RuntimeError(w.last_error)
                return out
            step(f"旁白请求（{label}）", chat)


if __name__ == "__main__":
    main()
