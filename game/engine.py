"""回合循环：筛选合法动作 → 一次请求问 Jev → 门槛检查 → 执行。
原则：Jev 给概率排序，代码里的门槛和规则做最终决定。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .jev_client import JevClient, JevError
from .world import State, World

NONE = "none"
NONE_HINT = "以上都不是：玩家说的话与上面任何一个动作都对不上，或者是没有对应规则的动作"


@dataclass
class Plan:
    """一回合里 Jev 返回之后、执行之前的决策。"""
    text: str
    code: str | None
    legal: list
    questions: dict
    answers: dict
    kind: str                      # act | clarify | refuse
    rule: dict | None = None
    candidates: list = field(default_factory=list)
    intent_top: float = 0.0
    intent_conf: float | None = None


@dataclass
class TurnResult:
    kind: str                      # act | clarify | refuse | confirm | jev_down
    text: str
    rule_id: str | None = None
    clue: str | None = None
    done: bool = False
    info: dict = field(default_factory=dict)


class TurnLogger:
    """turns.jsonl 记录每回合；low_conf.jsonl 汇总低置信度与落到 none 的输入，用来回头补规则和选项说明。"""

    def __init__(self, log_dir: str | Path | None):
        self.dir = Path(log_dir) if log_dir else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _write(self, name, entry):
        if self.dir:
            with open(self.dir / name, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def turn(self, entry):
        entry = {"ts": round(time.time(), 3), **entry}
        self._write("turns.jsonl", entry)
        if entry["kind"] in ("clarify", "refuse"):
            self._write("low_conf.jsonl", entry)


class Engine:
    def __init__(self, world: World, jev: JevClient, state: State | None = None,
                 threshold: float | None = None, logger: TurnLogger | None = None,
                 on_exchange=None, refuse_writer=None):
        self.world = world
        self.jev = jev
        self.state = state or world.initial()
        self.threshold = threshold if threshold is not None else world.cfg["low_conf_threshold"]
        self.logger = logger or TurnLogger(None)
        self.on_exchange = on_exchange  # (请求, 响应) 回调，--debug 用
        self.refuse_writer = refuse_writer  # (玩家原话, 场景, 预写文本) -> 文本；可选旁白钩子
        self.hints_used: dict[int, int] = {}
        self.behavior_uses: dict[str, int] = {}
        self.clues: list[str] = []
        self.turn_no = 0
        self.texts = world.level["texts"]

    # ------------------------------------------------------------ 打包问题
    def build_request(self, text: str, code: str | None, legal: list):
        w, st = self.world, self.state
        room = w.rooms[st.room]["name"]
        payload = {"room": room, "player_said": text}  # 只放本回合需要的信息
        questions = {}

        criteria = {r["id"]: r["hint_for_jev"] for r in legal}
        criteria[NONE] = NONE_HINT
        questions["intent"] = {
            "type": "choice",
            "instructions": "`player_said` 是玩家在当前房间 `room` 里说的话。玩家想做的是哪个动作？"
                            "如果玩家一次提了多个动作，只选他最先要做的那一个。",
            "criteria": criteria,
        }

        npc_id = w.npc_here(st)
        if npc_id:
            npc = w.npcs[npc_id]
            payload["npc"] = npc["desc"]
            questions["attitude"] = {
                "type": "score",
                "instructions": "`player_said` 这句话对 `npc`（在场的 NPC）的态度如何？",
                "criteria": w.cfg["attitude_levels"],
            }
            questions["reaction"] = {
                "type": "choice",
                "instructions": "如果玩家 `player_said` 是在对 `npc` 说话，`npc` 此刻最可能怎样反应？",
                "criteria": {b["id"]: b["hint_for_jev"] for b in w.behaviors(npc_id)},
            }
        for r in w.elsewhere_rules(st, code):   # 想做但此地做不了：只用来指路
            criteria["at_" + r["id"]] = (f"玩家想做的是：{r['hint_for_jev']}。但这件事在玩家当前所在的房间做不了，"
                                         f"玩家提到的对象或地点在别的房间")
        for r in legal:
            if r.get("confirm"):
                questions["confirm"] = {
                    "type": "noul",
                    "instructions": r["confirm"] + " 参考 `player_said`。",
                    "criteria": {"true": "玩家明确表示要这样做", "false": "玩家只是随口一提、询问或没有表态"},
                }
        return payload, questions

    # ------------------------------------------------------------ 决策（不改状态）
    def plan(self, text: str) -> Plan:
        code = self.world.extract_code(text)
        legal = self.world.legal_rules(self.state, code)
        payload, questions = self.build_request(text, code, legal)
        resp = self.jev.ask(payload, questions)
        if self.on_exchange:
            self.on_exchange({"state": payload, "questions": questions}, resp)
        answers = resp["answers"]
        return self.decide_intent(text, code, legal, questions, answers)

    def decide_intent(self, text, code, legal, questions, answers) -> Plan:
        ia = answers["intent"]
        by_id = {r["id"]: r for r in legal}
        elsewhere = {k[3:]: k for k in questions["intent"]["criteria"] if k.startswith("at_")}
        away = {k: next(r for r in self.world.rules if r["id"] == rid) for rid, k in elsewhere.items()}
        probs = {k: v for k, v in ia.get("probabilities", {}).items() if k in by_id or k in away or k == NONE}
        choice = ia.get("choice")
        if choice not in by_id and choice not in away and choice != NONE:   # 越出合法集合：当作 none 处理
            choice = NONE
        top_p = probs.get(choice, 0.0) if probs else 1.0
        conf = ia.get("confidence")
        ranked = sorted((k for k in probs if k in by_id), key=lambda k: -probs[k])
        cands = [by_id[k] for k in ranked if probs[k] >= 0.1][:2]
        # 指路没有副作用，选错代价为零，阈值比执行动作低一些
        thr = self.world.cfg.get("elsewhere_threshold", 0.45) if choice in away else self.threshold
        low = top_p < thr or (conf is not None and conf < thr)
        plan = Plan(text, code, legal, questions, answers, "act", intent_top=top_p, intent_conf=conf,
                    candidates=cands)
        if low:
            plan.kind = "clarify"
        elif choice == NONE:
            plan.kind = "refuse"
        elif choice in away:
            plan.kind, plan.rule = "elsewhere", away[choice]
        else:
            plan.rule = by_id[choice]
        return plan

    # ------------------------------------------------------------ 一个回合
    def turn(self, text: str) -> TurnResult:
        text = text.strip()
        try:
            plan = self.plan(text)
        except JevError as e:
            return TurnResult("jev_down", self.texts["jev_down"], info={"error": str(e)})
        self.turn_no += 1
        res = self._execute(plan)
        self.logger.turn({
            "turn": self.turn_no, "input": text, "room": self.state.room, "kind": res.kind,
            "rule": res.rule_id, "intent_top": plan.intent_top, "intent_conf": plan.intent_conf,
            "intent_probs": plan.answers["intent"].get("probabilities"),
            "candidates": [c["id"] for c in plan.candidates], "info": res.info,
        })
        return res

    def hint(self) -> str:
        """递进式提示：同一阶段每问一次深入一层，最多到最直白的一层；阶段变了从头开始。"""
        idx, levels = self.world.hint_stage(self.state)
        n = self.hints_used.get(idx, 0)
        self.hints_used[idx] = min(n + 1, len(levels))
        return f"（提示 {min(n + 1, len(levels))}/{len(levels)}）{levels[min(n, len(levels) - 1)]}"

    def _execute(self, plan: Plan) -> TurnResult:
        w, t = self.world, self.texts
        if plan.kind == "clarify":
            c = plan.candidates
            if len(c) >= 2:
                msg = t["clarify_two"].format(a=c[0]["label"], b=c[1]["label"])
            elif c:
                msg = t["clarify_one"].format(a=c[0]["label"])
            else:
                msg = t["clarify_none"]
            return TurnResult("clarify", msg, info={"top": plan.intent_top})
        if plan.kind == "refuse":
            text = w.rooms[self.state.room].get("refuse", t["refuse_default"])
            info = {"top": plan.intent_top}
            if self.refuse_writer:
                scene = w.describe(self.state).replace("\n", " ")
                written = self.refuse_writer(plan.text, scene, text)
                info["flavor"] = written != text
                text = written
            return TurnResult("refuse", text, info=info)

        rule = plan.rule
        if plan.kind == "elsewhere":
            return TurnResult("elsewhere", rule["elsewhere"], rule_id=rule["id"], info={"top": plan.intent_top})
        if rule.get("builtin") == "hint":
            return TurnResult("act", self.hint(), rule_id=rule["id"])
        if rule.get("builtin") == "look":
            return TurnResult("act", w.describe(self.state), rule_id=rule["id"])
        if rule.get("builtin") == "inv":
            items = ", ".join(w.item_name(i) for i in sorted(self.state.items)) or "无"
            clues = "; ".join(self.clues) or "无"
            return TurnResult("act", f"物品: {items}\n线索: {clues}", rule_id=rule["id"])
        if rule.get("confirm"):   # 通关前最后确认：noul 低于阈值则不执行
            p = plan.answers.get("confirm", {}).get("noul", 0.0)
            if p < w.cfg["noul_threshold"]:
                return TurnResult("confirm", t["confirm"], rule_id=rule["id"], info={"noul": p})

        if rule.get("npc"):
            return self._talk(plan, rule)

        self.state = w.apply(self.state, rule)
        return TurnResult("act", rule["text"], rule_id=rule["id"], done=w.is_won(self.state))

    def _talk(self, plan: Plan, rule: dict) -> TurnResult:
        w = self.world
        npc = rule["npc"]
        att = plan.answers["attitude"]
        level = w.score_level(att["score"])
        before = self.state.get_trust(npc)
        self.state = w.adjust_trust(self.state, npc, w.trust_delta(level))
        trust = self.state.get_trust(npc)

        probs = plan.answers["reaction"].get("probabilities", {})
        behaviors = {b["id"]: b for b in w.behaviors(npc)}
        ranked = sorted((k for k in behaviors), key=lambda k: -probs.get(k, 0.0))
        picked, blocked = None, []
        for bid in ranked:   # 门槛拦下就改走次高项
            if behaviors[bid].get("min_trust", -99) <= trust:
                picked = behaviors[bid]
                break
            blocked.append(bid)
        if picked is None:
            picked = min(behaviors.values(), key=lambda b: b.get("min_trust", -99))
        self.state = w.apply_behavior(self.state, picked)
        clue = picked.get("clue")
        if clue and clue not in self.clues:
            self.clues.append(clue)
        else:
            clue = None
        variants = picked.get("texts")
        text = picked["text"]
        if variants:   # 同一种反应反复出现时轮换说法，避免玩家以为卡住了
            n = self.behavior_uses.get(picked["id"], 0)
            text = variants[n % len(variants)]
            self.behavior_uses[picked["id"]] = n + 1
        return TurnResult("act", text, rule_id=rule["id"], clue=clue, info={
            "npc": npc, "attitude_score": att["score"], "attitude": w.cfg["attitude_levels"][level],
            "trust": f"{before}->{trust}", "reaction": picked["id"], "blocked": blocked,
            "reaction_probs": probs})
