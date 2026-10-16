"""Jev 客户端：RealJev（HTTP）与 MockJev（关键词规则），同一接口。

接口：client.ask(state, questions) -> {"model", "answers", "usage"}
answers 的结构与官方 /v1/systemone 响应一致（choice / score / noul）。
"""
from __future__ import annotations

import json
import os
import random
import re
import stat
import time
import http.client
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


class JevError(Exception):
    pass


class JevClient:
    name = "base"

    def ask(self, state, questions: dict) -> dict:
        raise NotImplementedError


# --------------------------------------------------------------------------- Real
def _read_key_file(path: str) -> str:
    # Windows 没有 POSIX 权限位，st_mode 恒为 0o666，跳过检查
    if os.name != "nt" and os.stat(path).st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise JevError(f"key 文件 {path} 权限过宽，请 chmod 600")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


class RealJev(JevClient):
    """配置全走环境变量，切换端点只改配置：
    TYPESAFE_API_KEY / TYPESAFE_KEY_FILE、TYPESAFE_BASE_URL、TYPESAFE_MODEL、
    TYPESAFE_TIMEOUT、TYPESAFE_MAX_RETRIES。"""

    name = "real"
    RETRY_STATUS = {429, 500, 502, 503, 504, 529}

    def __init__(self, api_key=None, base_url=None, model=None, timeout=None,
                 max_retries=None, backoff=1.0, sleep=time.sleep, on_retry=None, proxy=None):
        env = os.environ
        key = api_key or env.get("TYPESAFE_API_KEY")
        if not key and env.get("TYPESAFE_KEY_FILE"):
            key = _read_key_file(env["TYPESAFE_KEY_FILE"])
        if not key:
            raise JevError("未配置 key：请设置 TYPESAFE_API_KEY 或 TYPESAFE_KEY_FILE")
        self._key = key
        self.base_url = base_url or env.get("TYPESAFE_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or env.get("TYPESAFE_MODEL", DEFAULT_MODEL)
        self.timeout = float(timeout or env.get("TYPESAFE_TIMEOUT", 15))
        self.max_retries = int(max_retries if max_retries is not None else env.get("TYPESAFE_MAX_RETRIES", 2))
        self.backoff = backoff
        self._sleep = sleep
        # TYPESAFE_PROXY: 不设=沿用系统代理；direct=强制直连；或填 http://主机:端口
        proxy = proxy or env.get("TYPESAFE_PROXY", "")
        if proxy.lower() in ("direct", "none", "off"):
            handler = urllib.request.ProxyHandler({})
        elif proxy:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        else:
            handler = urllib.request.ProxyHandler()
        self._opener = urllib.request.build_opener(handler)
        self.on_retry = on_retry  # (第几次重试, 总重试数, 错误) 回调，CLI 用来提示用户

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFE_KEY_FILE"))

    def ask(self, state, questions):
        body = json.dumps({"state": state, "model": self.model, "questions": questions},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        })
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                last = JevError(f"HTTP {e.code}: {detail}")
                if e.code not in self.RETRY_STATUS:  # 400/401/413/422：重试没有意义
                    raise last from None
            except (OSError, http.client.HTTPException, json.JSONDecodeError) as e:
                # OSError 涵盖 URLError / ConnectionError / ssl 错误 / socket.timeout
                # （3.8 里 socket.timeout 不是 TimeoutError，必须按 OSError 捕获）
                last = JevError(f"请求失败: {type(e).__name__}: {e}")
            if attempt < self.max_retries:
                if self.on_retry:
                    self.on_retry(attempt + 1, self.max_retries, last)
                self._sleep(self.backoff * 2 ** attempt + random.uniform(0, 0.25))
        raise last


# --------------------------------------------------------------------------- Mock
STRONG = {
    # 意图
    "look_around": r"^(?!.*(遗像|相框|照片|铜牌|画像)).*(周围|四周|环顾|观察|看看|看一看|看一下|这里有什么|什么情况)",
    "examine_portrait": r"遗像|相框|照片|铜牌|画像",
    "ask_hint": r"提示|怎么办|卡关|卡住了|下一步|不知道怎么|去哪|做什么",
    "examine_drawer": r"^(?!.*(砸|撬|强行|暴力))(?=.*(抽屉|密码|开锁)).*(查看|看看|检查|打开|怎么开|输入|试试|试一下|开)",
    "check_items": r"背包|物品|线索|身上|携带|带了|我有什么",
    "go_archive": r"档案室|走廊|隔壁",
    "go_hall": r"大厅|返回|回到|回去|上楼|上去|离开|出去",
    "go_basement": r"机房|楼梯|地下|下楼|下去",
    "use_keycard": r"门禁卡|刷卡|刷开|刷一下|刷",
    "examine_shutter": r"卷帘|大门|铁门|门口",
    "talk_zhou": r"守卫|老周|周师傅|聊|问|你好|您|馆长|资料|尊敬|敬重|停电|cnm|傻|蠢|滚|混蛋|垃圾",
    "talk_lin": r"小林|管理员|聊|问|你好|您|馆长|密码|生日|cnm|傻|蠢|滚|混蛋|垃圾",
    "open_drawer": r"\d{4}|输入|试试|填",
    "wrong_code": r"\d{4}|输入|试试|填",
    "pull_switch": r"总闸|电闸|闸|拉|合上|电源|推上",
    "exit": r"离开|出去|走出|出门|逃|撤",
    # NPC 反应
    "zhou_reminisce": r"馆长|资料|尊敬|敬重|生日|蛋糕",
    "zhou_complain": r"倒霉|辛苦|烦|冷|黑|不容易",
    "zhou_drive_away": r"滚|闭嘴|废物|少废话",
    "zhou_deflect": r"停电|怎么|为什么|聊|问",
    "lin_confirm": r"密码|生日|抽屉|馆长",
    "lin_refuse": r"滚|闭嘴|砸|撬|强行",
}
PRIOR = {"none": 0.6, "zhou_deflect": 1.0, "lin_probe": 1.0}  # 其余选项默认 0.05
VAGUE = r"那个|这个|东西|它"
VAGUE_WEIGHT = 0.8
STRONG_WEIGHT = 3.0
RANK_DECAY = 1.5  # 一句话里出现多个动作时，后出现的动作权重递减（“只做第一步”）

ATTITUDE = [
    (3, r"尊敬|敬重|敬佩|尊重|敬意|德高望重"),
    (0, r"滚|闭嘴|废物|笨|该死|少废话|蠢"),
    (2, r"请|谢谢|麻烦|您|你好|拜托|辛苦"),
]
CONFIRM_YES = r"离开|出去|走出|出门|撤|逃|走吧|走了"


class MockJev(JevClient):
    """关键词规则版 Jev，返回结构与真实响应一致，含 probabilities / confidence。"""

    name = "mock"

    def ask(self, state, questions):
        said = str(state.get("player_said", "")) if isinstance(state, dict) else str(state)
        answers = {}
        for key, q in questions.items():
            kind = q["type"]
            if kind == "choice":
                answers[key] = self._choice(said, q["criteria"])
            elif kind == "score":
                answers[key] = self._score(said, q["criteria"])
            elif kind == "noul":
                answers[key] = self._noul(said)
            else:
                raise JevError(f"unknown question type: {kind}")
        return {"model": "mock-jev", "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}

    def _choice(self, said, criteria):
        vague = bool(re.search(VAGUE, said))
        first_pos = {}
        for opt in criteria:
            pat = STRONG.get(opt[3:] if opt.startswith("at_") else opt)
            m = re.search(pat, said) if pat else None
            if m:
                first_pos[opt] = m.start(m.lastindex) if m.lastindex else m.start()   # 带排除前瞻的模式，位置取关键词本身
        # 开抽屉/错误密码比"查看抽屉"更具体；非对话类的合法动作命中时，"想做但此地做不了"让位
        for general, specific in (("examine_drawer", ("open_drawer", "wrong_code")),):
            if general in first_pos and any(o in first_pos for o in specific):
                first_pos.pop(general)
        if any(not o.startswith(("at_", "talk_")) for o in first_pos):
            for o in [o for o in first_pos if o.startswith("at_") and not o.startswith("at_talk_")]:
                first_pos.pop(o)
        rank = {o: i for i, o in enumerate(sorted(first_pos, key=first_pos.get))}
        raw = {}
        for opt in criteria:
            s = PRIOR.get(opt, 0.005 if opt.startswith('at_') else 0.015)
            if opt in first_pos:
                s += STRONG_WEIGHT / (1 + RANK_DECAY * rank[opt]) 
            elif vague and not first_pos and opt != "none":  # 指代不明只在没有明确动作词时才摊薄概率
                s += VAGUE_WEIGHT
            raw[opt] = s
        total = sum(raw.values())
        probs = {o: round(v / total, 4) for o, v in raw.items()}
        top = max(probs, key=probs.get)
        return {"type": "choice", "choice": top, "probabilities": probs, "confidence": probs[top]}

    def _score(self, said, criteria):
        n = len(criteria)
        level = 1
        for lvl, pat in ATTITUDE:
            if lvl < n and re.search(pat, said):
                level = lvl
                break
        probs = {str(i): 0.0 for i in range(n)}
        probs[str(level)] = 0.85
        rest = [i for i in (level - 1, level + 1) if 0 <= i < n]
        for i in rest:
            probs[str(i)] = round(0.15 / len(rest), 4)
        score = sum(int(i) * p for i, p in probs.items())
        return {"type": "score", "score": round(score, 3),
                "legend": {str(i): c for i, c in enumerate(criteria)},
                "probabilities": probs, "confidence": 0.85}

    def _noul(self, said):
        return {"type": "noul", "noul": 0.95 if re.search(CONFIRM_YES, said) else 0.08}
