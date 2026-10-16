"""庭审规则：全部是代码，Jev 的判断只作为输入。

每位陪审员有一个倾向值 lean（负=倾向有罪，正=倾向无罪），裁决时 lean > 0 投无罪。
一句陈词对陪审员 j 的影响：
    质量系数 qf  = quality_floor + quality_step * quality          （0.4 … 1.0）
    基础影响     = base_weight * (quality - 2)                      （所有人都听得到的好坏）
    戳中加成     = P(hit_j) * hit_weight * qf * 专注系数 * 重复衰减
    雷区惩罚     = P(dislike_j) * dislike_weight
    delta_j      = clamp(基础 + 戳中 - 雷区, ±max_delta)
    专注系数：一句话戳中超过 focus_limit 人时按比例稀释（"什么都说等于什么都没说"）
    重复衰减：同一论点每多讲一次，乘一次 repeat_decay；讲到第 repeat_annoy+1 次起，所有人再 -repeat_penalty（听烦了）
    念案卷：戳中加成再乘 (1 - recite_cut * P(recite))——事实要说出它意味着什么才打动人
    操纵：P(manip) > manip_threshold 时这句话作废，所有人 -manip_backlash
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Effect:
    deltas: dict
    manip: bool = False
    diluted: bool = False
    repeated: bool = False
    annoyed: bool = False
    recited: bool = False
    hit_ids: list = field(default_factory=list)


@dataclass
class Spoken:
    text: str
    deltas: dict
    reactions: list
    notes: list
    votes: int


class Trial:
    def __init__(self, case: dict, clock=time.time):
        self.case = case
        self.cfg = case["config"]
        self.jurors = case["jurors"]
        self.clock = clock
        self.leans = {j["id"]: float(j["start"]) for j in self.jurors}
        self.arg_uses: dict = {}
        self.history: list = []
        self.manip_strikes = 0
        self.started_at: float | None = None
        self.rested = False
        self.penalty = 0.0          # 用提示等扣掉的秒数
        self.touched: set = set()   # 已经被说出口的话戳中过的陪审员
        self.hinted: dict = {}      # 陪审员 -> 已经提示到第几层（1=在意点，2=雷区）
        self.stall = 0              # 连续几句说出口后票数没涨（卡住了就送一次提示）
        self.last_votes = self.votes()

    # ---- 时间 ----
    def begin(self):
        if self.started_at is None:
            self.started_at = self.clock()

    def time_left(self) -> float:
        if self.started_at is None:
            return float(self.cfg["time_limit"])
        return max(0.0, self.cfg["time_limit"] - self.penalty - (self.clock() - self.started_at))

    @property
    def over(self) -> bool:
        return self.rested or (self.started_at is not None and self.time_left() <= 0)

    # ---- 计算 ----
    def effect(self, jd) -> Effect:
        c = self.cfg
        if jd.manip > c["manip_threshold"]:
            return Effect({j["id"]: -c["manip_backlash"] for j in self.jurors}, manip=True)
        qf = c["quality_floor"] + c["quality_step"] * jd.quality
        base = c["base_weight"] * (jd.quality - c.get("quality_center", 2.0))
        hit_ids = [k for k, p in jd.hits.items() if p > c["hit_threshold"]]
        focus = 1.0
        if len(hit_ids) > c["focus_limit"]:
            focus = c["focus_limit"] / len(hit_ids)
        uses = self.arg_uses.get(jd.argument, 0) if jd.argument != "none" else 0
        rep = c["repeat_decay"] ** uses
        annoyed = uses >= c.get("repeat_annoy", 99)
        # 只罚明确在念案卷的：P 在 recite_from 以下不罚，到 recite_full 罚满（中间那段是 Jev 拿不准的，不冤枉人）
        lo, hi = c.get("recite_from", 0.0), c.get("recite_full", 1.0)
        ramp = min(1.0, max(0.0, (jd.recite - lo) / (hi - lo))) if hi > lo else float(jd.recite >= lo)
        recite = 1.0 - c.get("recite_cut", 0.0) * ramp
        if annoyed:
            base = min(base, 0.0) - c.get("repeat_penalty", 0.0)   # 听烦了：讲得再好也不加分
        deltas = {}
        for j in self.jurors:
            k = j["id"]
            d = base + jd.hits.get(k, 0.0) * c["hit_weight"] * qf * focus * rep * recite \
                - jd.dislikes.get(k, 0.0) * c["dislike_weight"]
            deltas[k] = max(-c["max_delta"], min(c["max_delta"], d))
        return Effect(deltas, diluted=focus < 1.0, repeated=uses > 0, annoyed=annoyed,
                      recited=jd.recite > 0.5, hit_ids=hit_ids)

    def faces(self, deltas: dict) -> dict:
        t = self.cfg["face_threshold"]
        return {k: (1 if d > t else -1 if d < -t else 0) for k, d in deltas.items()}

    def preview(self, jd) -> dict:
        """打字时的预览：只给三档表情，不改任何状态。"""
        return self.faces(self.effect(jd).deltas)

    # ---- 说出口 ----
    def speak(self, text: str, jd) -> Spoken:
        c = self.cfg
        eff = self.effect(jd)
        for k, d in eff.deltas.items():
            self.leans[k] = max(c["lean_min"], min(c["lean_max"], self.leans[k] + d))
        notes = []
        if eff.manip:
            self.manip_strikes += 1
            notes.append(self.case["texts"]["manip"])
        else:
            if jd.argument != "none":
                self.arg_uses[jd.argument] = self.arg_uses.get(jd.argument, 0) + 1
            self.touched.update(eff.hit_ids)
            if eff.diluted:
                notes.append(self.case["texts"]["diluted"])
            if eff.annoyed:
                notes.append(self.case["texts"]["annoyed"])
            elif eff.repeated:
                notes.append(self.case["texts"]["repeated"])
            if eff.recited:
                notes.append(self.case["texts"]["recited"])
        reactions = []
        for j in ([] if eff.manip else self.jurors):   # 操纵时全体皱眉，脸上已经看得到，文字只留法官那句
            d = eff.deltas[j["id"]]
            if d >= 0.5:
                reactions.append({"juror": j["id"], "dir": 1, "text": j["up"]})
            elif d <= -0.5:
                reactions.append({"juror": j["id"], "dir": -1, "text": j["down"]})
        if not reactions and not eff.manip:
            notes.append(self.case["texts"]["silent"])
        votes = self.votes()
        self.stall = self.stall + 1 if votes <= self.last_votes else 0
        self.last_votes = votes
        sp = Spoken(text, eff.deltas, reactions, notes, votes)
        self.history.append({"text": text, "deltas": eff.deltas, "quality": jd.quality,
                             "argument": jd.argument, "manip": jd.manip, "recite": round(jd.recite, 3),
                             "hits": {k: round(v, 3) for k, v in jd.hits.items()},
                             "dislikes": {k: round(v, 3) for k, v in jd.dislikes.items()}, "votes": votes})
        return sp

    # ---- 提示（代码给，花时间换）----
    def hint(self, free: bool = False) -> dict:
        """挑一位还倾向有罪、但离翻转最近的陪审员，揭示 TA 在意什么；再要一次就揭示 TA 的雷区。
        free：卡住时系统主动送的提示，不扣时间。"""
        t = self.case["texts"]
        if not free:
            self.penalty += self.cfg.get("hint_cost", 10)
        guilty = sorted((j for j in self.jurors if self.leans[j["id"]] <= 0),
                        key=lambda j: -self.leans[j["id"]])
        for j in guilty:
            k, lvl = j["id"], self.hinted.get(j["id"], 0)
            if lvl == 0 and k not in self.touched:
                self.hinted[k] = 1
                return {"juror": k, "text": t["hint_concern"].format(**j)}
            if lvl < 2:
                self.hinted[k] = 2
                return {"juror": k, "text": t["hint_dislike"].format(**j)}
        if guilty:
            j = guilty[0]
            return {"juror": j["id"], "text": t["hint_concern"].format(**j)}
        return {"juror": None, "text": t["hint_none"]}

    # ---- 裁决 ----
    def votes(self) -> int:
        return sum(1 for v in self.leans.values() if v > 0)

    def verdict(self) -> dict:
        acquit = self.votes() >= self.cfg["win_votes"]
        return {
            "acquit": acquit,
            "votes": self.votes(),
            "text": self.case["texts"]["win" if acquit else "lose"],
            "jurors": [{"id": j["id"], "name": j["name"], "acquit": self.leans[j["id"]] > 0,
                        "line": j["acquit"] if self.leans[j["id"]] > 0 else j["guilty"]}
                       for j in self.jurors],
        }
