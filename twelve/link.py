"""复用连接的 Jev 客户端。

《十二人》每次停顿都要问一次 Jev，延迟决定了"读空气"成不成立。
urllib 每次请求都重新做 TCP + TLS 握手；网络远的时候光握手就要好几个往返。
这里维护一个小连接池，同一条 HTTPS 连接反复用，只在第一次付握手的代价。

配置沿用 RealJev 的环境变量（TYPESAFE_API_KEY / KEY_FILE / BASE_URL / MODEL / PROXY / TIMEOUT）。"""
from __future__ import annotations

import http.client
import json
import os
import queue
import ssl
import time
import urllib.request
from urllib.parse import urlparse

from game.jev_client import JevError, RealJev


def _resolve_proxy(host: str, setting: str | None):
    """返回 (proxy_host, proxy_port) 或 None。TYPESAFE_PROXY: 不设=系统代理；direct=直连；或 http://主机:端口。"""
    setting = (setting or "").strip()
    if setting.lower() in ("direct", "none", "off"):
        return None
    if not setting:
        if urllib.request.proxy_bypass(host):
            return None
        setting = urllib.request.getproxies().get("https", "")
        if not setting:
            return None
    p = urlparse(setting if "://" in setting else "http://" + setting)
    # Windows 注册表里常见 https://127.0.0.1:7890 这种写法，本地代理其实都是普通 HTTP 代理
    return p.hostname, p.port or 8080


class KeepAliveJev(RealJev):
    name = "real"

    def __init__(self, pool_size: int = 4, **kw):
        super().__init__(**kw)
        u = urlparse(self.base_url)
        self._https = u.scheme == "https"
        self._host, self._port = u.hostname, u.port or (443 if self._https else 80)
        self._path = u.path or "/"
        self._proxy = _resolve_proxy(self._host, kw.get("proxy") or os.environ.get("TYPESAFE_PROXY"))
        self._ctx = ssl.create_default_context() if self._https else None
        self._pool: queue.LifoQueue = queue.LifoQueue(maxsize=pool_size)
        self.last_ms = 0.0

    def _new_conn(self):
        if self._proxy:
            ph, pp = self._proxy
            if self._https:
                c = http.client.HTTPSConnection(ph, pp, timeout=self.timeout, context=self._ctx)
                c.set_tunnel(self._host, self._port)
            else:
                c = http.client.HTTPConnection(ph, pp, timeout=self.timeout)
            return c
        if self._https:
            return http.client.HTTPSConnection(self._host, self._port, timeout=self.timeout, context=self._ctx)
        return http.client.HTTPConnection(self._host, self._port, timeout=self.timeout)

    def _get(self):
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            return self._new_conn()

    def _put(self, c):
        try:
            self._pool.put_nowait(c)
        except queue.Full:
            c.close()

    def warm(self):
        """提前建好一条连接（开局时调用），第一句话就不用付握手的代价。"""
        c = self._get()
        try:
            c.connect()
            self._put(c)
        except OSError:
            c.close()

    def ask(self, state, questions):
        body = json.dumps({"state": state, "model": self.model, "questions": questions},
                          ensure_ascii=False).encode("utf-8")
        target = self._path if not (self._proxy and not self._https) else self.base_url
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                   "Connection": "keep-alive"}
        last = None
        for attempt in range(self.max_retries + 2):   # 多一次：池里的旧连接可能已被服务端关掉
            c = self._get()
            t0 = time.time()
            try:
                c.request("POST", target, body=body, headers=headers)
                r = c.getresponse()
                data = r.read()
                self.last_ms = (time.time() - t0) * 1000
                if r.status == 200:
                    self._put(c)
                    return json.loads(data.decode("utf-8"))
                c.close()
                last = JevError(f"HTTP {r.status}: {data.decode('utf-8', 'replace')[:300]}")
                if r.status not in self.RETRY_STATUS:
                    raise last
            except (OSError, http.client.HTTPException, json.JSONDecodeError) as e:
                c.close()
                last = JevError(f"请求失败: {type(e).__name__}: {e}")
                if attempt == 0:      # 复用的连接失效很常见，立刻换新连接重试，不退避
                    continue
            if attempt < self.max_retries:
                self._sleep(self.backoff * 2 ** attempt)
        raise last
