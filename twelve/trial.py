"""庭审规则：全部是代码，Jev 的判断只作为输入。

每位陪审员有一个倾向值 lean（负=倾向有罪，正=倾向无罪），裁决时 lean > 0 投无罪。
一句陈词对陪审员 j 的影响：
    质量系数 qf  = quality_floor + quality_step * quality          （0.4 … 1.0）
    基础影响     = base_weight * (quality - quality_center)          （所有人都听得到的好坏；quality_center 默认 2）
    戳中加成     = P(hit_j) * hit_weight * qf * 专注系数 * 重复衰减
    雷区惩罚     = P(dislike_j) * dislike_weight
    delta_j      = clamp(基础 + 戳中 - 雷区, ±max_delta)
    专注系数：一句话戳中超过 focus_limit 人时按比例稀释（"什么都说等于什么都没说"）
    重复衰减：同一论点每多讲一次，乘一次 repeat_decay；讲到第 repeat_annoy+1 次起，所有人再 -repeat_penalty（听烦了）
    念案卷：戳中加成再乘 (1 - recite_cut * rr)，rr = P(recite) 从 recite_from 到 recite_full 的渐进——事实要说出它意味着什么才打动人
    照搬案卷原文：代码比对逐字重合的比例 copied，从 copy_from 到 copy_full 渐进，与 P(recite) 取大；
    念案卷时「人人都听得到的好坏」（基础分）也按 recite_base_cut 缩掉——照抄的话不该白送所有人好感
    话没说完：说出口时戳中加成再乘 (1 - incomplete_cut * ramp(P))，基础分不为正；打字预览不计较（边打边看脸是核心玩法）
    操纵：P(manip) > manip_threshold 时这句话作废，所有人 -manip_backlash
    连带：有 influence 的陪审员（硬骨头）被这句话**直接**说服（第一次）时，其余仍倾向有罪的人各 +influence
    （每人只触发一次；同一句里多位硬骨头松口取最大的一个，不叠加；连带只让人"动摇"——最多推到 0 的下方，不会直接翻成无罪：
    翻转必须靠玩家自己的话，否则一句话能连锁翻一片）
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
    cut_off: bool = False
    hit_ids: list = field(default_factory=list)


@dataclass
class Spoken:
    text: str
    deltas: dict
    reactions: list
    notes: list
    votes: int
    cascade: list = field(default_factory=list)   # [(被说服的硬骨头, [跟着动摇的人])]


class Trial:
    def __init__(self, case: dict, clock=time.time):
        self.case = case
        self.cfg = case["config"]
        self.jurors = case["jurors"]
        self.clock = clock
        self.leans = {j["id"]: float(j["start"]) for j in self.jurors}
        self.start = dict(self.leans)   # 开庭时的倾向（事件改陪审员字段也不影响"原本倾向有罪"的判断）
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
        self.moved_most: dict = {}  # 陪审员 -> (最大正向变化, 那句话)，结局页"被这句打动"用
        self.cascaded: set = set()  # 已经触发过连带的硬骨头
        self.events: list = []      # 已经发生的事件 id
        self.mode = "normal"

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
        return self.rested or (self.started_at is not None and self.time_left() <= 0) or self.out_of_says

    @property
    def out_of_says(self) -> bool:
        """简单模式限句数：说满 max_says 句就宣判。"""
        n = self.cfg.get("max_says", 0)
        return bool(n) and len(self.history) >= n

    def set_mode(self, mode: str) -> bool:
        """开庭前选模式（如简单模式：时间更长、限句数），把该模式的参数盖到这一庭的配置上。"""
        m = self.case.get("modes", {}).get(mode)
        if not m or self.started_at is not None:
            return False
        self.cfg.update({k: v for k, v in m.items() if k != "label"})
        self.mode = mode
        return True

    # ---- 计算 ----
    @staticmethod
    def _ramp(p, lo, hi):
        return min(1.0, max(0.0, (p - lo) / (hi - lo))) if hi > lo else float(p >= lo)

    def effect(self, jd, spoken: bool = True) -> Effect:
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
        rr = max(self._ramp(jd.recite, lo, hi),
                 self._ramp(getattr(jd, "copied", 0.0), c.get("copy_from", 2.0), c.get("copy_full", 3.0)))   # 默认不启用
        if getattr(jd, "quoted", 0):
            # 引用案卷：引的部分不用自己打，但引了就得有自己的话——自己写的字太少按"念案卷"算（案卷原文 + 一句空话的老后门）
            own = self._ramp(jd.own_chars, c.get("quote_own_min", 0), c.get("quote_own_full", 1))
            rr = max(rr, 1.0 - own)
        recite = 1.0 - c.get("recite_cut", 0.0) * rr
        if base > 0 and rr > 0:
            base *= 1.0 - c.get("recite_base_cut", 0.0) * rr
        cut = self._ramp(jd.incomplete, c.get("incomplete_from", 0.6), c.get("incomplete_full", 0.85)) if spoken else 0.0
        if cut > 0:
            recite *= 1.0 - c.get("incomplete_cut", 0.0) * cut
            base = min(base, 0.0)
        if annoyed:
            base = min(base, 0.0) - c.get("repeat_penalty", 0.0)   # 听烦了：讲得再好也不加分
        deltas = {}
        for j in self.jurors:
            k = j["id"]
            d = base + jd.hits.get(k, 0.0) * c["hit_weight"] * qf * focus * rep * recite \
                - jd.dislikes.get(k, 0.0) * c["dislike_weight"]
            deltas[k] = max(-c["max_delta"], min(c["max_delta"], d))
        return Effect(deltas, diluted=focus < 1.0, repeated=uses > 0, annoyed=annoyed,
                      recited=rr >= 0.3 if hi < 1.0 else rr > 0.5, cut_off=cut >= 0.5, hit_ids=hit_ids)

    def faces(self, deltas: dict) -> dict:
        t = self.cfg["face_threshold"]
        return {k: (1 if d > t else -1 if d < -t else 0) for k, d in deltas.items()}

    def preview(self, jd) -> dict:
        """打字时的预览：只给三档表情，不改任何状态。"""
        return self.faces(self.effect(jd, spoken=False).deltas)

    # ---- 说出口 ----
    def speak(self, text: str, jd) -> Spoken:
        c = self.cfg
        eff = self.effect(jd)
        before = dict(self.leans)
        for k, d in eff.deltas.items():
            self.leans[k] = max(c["lean_min"], min(c["lean_max"], self.leans[k] + d))
            if not eff.manip and d >= 0.3 and d > self.moved_most.get(k, (0, ""))[0]:
                self.moved_most[k] = (d, text)
        # 连带：硬骨头第一次松口，其余仍倾向有罪的人跟着动摇（陪审团是会互相影响的）
        cascade = []
        direct = dict(self.leans)                     # 只看这句话直接造成的结果，连带的翻转不再引发连带
        flipped = [(j["id"], j["influence"]) for j in self.jurors
                   if j.get("influence") and j["id"] not in self.cascaded and before[j["id"]] <= 0 < direct[j["id"]]]
        if flipped:
            inf = max(i for _, i in flipped)
            for k, _ in flipped:
                self.cascaded.add(k)
            for k, _ in flipped:
                moved = [o["id"] for o in self.jurors if o["id"] != k and direct[o["id"]] <= 0]
                cascade.append((k, moved))
            moved_all = set()
            for o in {o for _, m in cascade for o in m}:
                new = min(self.leans[o] + inf, -0.05) if self.leans[o] <= -0.05 else self.leans[o]
                if new - self.leans[o] > 1e-9:
                    eff.deltas[o] += new - self.leans[o]
                    self.leans[o] = new
                    moved_all.add(o)
            cascade = [(k, [o for o in m if o in moved_all]) for k, m in cascade]
        notes = []
        names = {j["id"]: j["name"] for j in self.jurors}
        for k, moved in cascade:
            notes.append(self.case["texts"].get("cascade", "{name}松口了，其他人也开始动摇。").format(
                name=names[k], n=len(moved)))
        if eff.manip:
            self.manip_strikes += 1
            notes.append(self.case["texts"]["manip"])
        elif not eff.cut_off:    # 话没说完的不算讲过这个论点
            if jd.argument != "none":
                self.arg_uses[jd.argument] = self.arg_uses.get(jd.argument, 0) + 1
            self.touched.update(eff.hit_ids)
        if not eff.manip:
            if eff.diluted:
                notes.append(self.case["texts"]["diluted"])
            if eff.annoyed:
                notes.append(self.case["texts"]["annoyed"])
            elif eff.repeated:
                notes.append(self.case["texts"]["repeated"])
            if eff.cut_off:
                notes.append(self.case["texts"]["cut_off"])
            elif eff.recited:
                notes.append(self.case["texts"]["recited"])
        reactions = []
        for j in ([] if eff.manip else self.jurors):   # 操纵时全体皱眉，脸上已经看得到，文字只留法官那句
            d = eff.deltas[j["id"]]
            if d >= 0.5:
                reactions.append({"juror": j["id"], "dir": 1, "text": j["up"]})
            elif d <= -0.5:
                reactions.append({"juror": j["id"], "dir": -1, "text": j["down"]})
        if not reactions and not eff.manip and not eff.cut_off:
            notes.append(self.case["texts"]["silent"])
        votes = self.votes()
        self.stall = self.stall + 1 if votes <= self.last_votes else 0
        self.last_votes = votes
        sp = Spoken(text, eff.deltas, reactions, notes, votes,
                    [{"juror": k, "moved": m} for k, m in cascade])
        self.history.append({"text": text, "deltas": eff.deltas, "quality": jd.quality,
                             "argument": jd.argument, "manip": jd.manip, "recite": round(jd.recite, 3), "copied": round(getattr(jd, "copied", 0.0), 3), "quoted": getattr(jd, "quoted", 0), "own_chars": getattr(jd, "own_chars", 0), "incomplete": round(jd.incomplete, 3),
                             "cascade": [k for k, _ in cascade],
                             "hits": {k: round(v, 3) for k, v in jd.hits.items()},
                             "dislikes": {k: round(v, 3) for k, v in jd.dislikes.items()}, "votes": votes})
        return sp

    # ---- 庭审中途的事件（反转）----
    def apply_event(self, ev: dict):
        """事件只改数据：加案卷、改倾向、改陪审员在意的点 / 雷区、加论点、补时间。何时触发由 server 判断。"""
        c = self.cfg
        self.case["dossier"].extend(ev.get("dossier", []))
        if self.case.get("dossier_view") is not None:      # 页面上显示的简版：和 dossier 一一对应
            self.case["dossier_view"] = list(self.case["dossier_view"]) + list(ev.get("dossier_view") or [])
        self.case["arguments"].update(ev.get("arguments", {}))
        by_id = {j["id"]: j for j in self.jurors}
        for patch in ev.get("jurors", []):
            by_id[patch["id"]].update({k: v for k, v in patch.items() if k != "id"})
        shift = dict(ev.get("leans", {}))
        for k in self.leans:
            d = shift.get(k, 0.0) + ev.get("leans_all", 0.0)
            if d:
                self.leans[k] = max(c["lean_min"], min(c["lean_max"], self.leans[k] + d))
        self.penalty -= ev.get("time", 0)     # 给读新证据的时间
        self.last_votes = self.votes()        # 事件造成的票数变化不算"卡住"
        self.events.append(ev["id"])

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
        swing = self.cfg.get("swing_band", 0)
        # 最打动人的一句：正向变化加起来最多的（操纵的不算）
        best = None
        for h in self.history:
            if h["manip"] > self.cfg["manip_threshold"]:
                continue
            gain = sum(d for d in h["deltas"].values() if d > 0)
            moved = [k for k, d in h["deltas"].items() if d >= 0.5]
            if gain > 0.3 and (best is None or gain > best[0]):
                best = (gain, h["text"], moved)
        names = {j["id"]: j["name"] for j in self.jurors}
        return {
            "acquit": acquit,
            "votes": self.votes(),
            "need": self.cfg["win_votes"],
            "says": len(self.history),
            "text": self.case["texts"]["win" if acquit else "lose"],
            "best": None if best is None else {"text": best[1], "moved": [names[k] for k in best[2]]},
            "jurors": [{"id": j["id"], "name": j["name"], "acquit": self.leans[j["id"]] > 0,
                        "swing": -swing < self.leans[j["id"]] <= 0,
                        # 原本倾向有罪、最后被说服的人：最打动 TA 的那句
                        "by": self.moved_most.get(j["id"], (0, None))[1]
                              if self.leans[j["id"]] > 0 and self.start[j["id"]] <= 0 else None,
                        "line": j["acquit"] if self.leans[j["id"]] > 0 else j["guilty"]}
                       for j in self.jurors],
        }
