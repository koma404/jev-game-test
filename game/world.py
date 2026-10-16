"""世界状态、规则筛选、效果执行。不含任何 Jev 相关逻辑。"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

LEVEL_DIR = Path(__file__).parent / "levels"
DEFAULT_CONFIG = {
    "low_conf_threshold": 0.6,
    "noul_threshold": 0.5,
    "attitude_levels": ["冒犯", "中性", "友善", "明确表达敬意"],
    "score_to_trust": [-1, 0, 1, 2],
    "trust_min": -3,
    "trust_max": 5,
    "code_regex": r"(?<!\d)\d{4}(?!\d)",
}


@dataclass(frozen=True)
class State:
    room: str
    flags: frozenset = frozenset()
    items: frozenset = frozenset()
    trust: tuple = ()  # 排序后的 ((npc, 信任值), ...)，保持可哈希

    def get_trust(self, npc: str) -> int:
        return dict(self.trust).get(npc, 0)

    def with_trust(self, npc: str, value: int) -> "State":
        d = dict(self.trust)
        d[npc] = value
        return replace(self, trust=tuple(sorted(d.items())))


def load_level(path: str | Path | None = None) -> dict:
    path = Path(path) if path else LEVEL_DIR / "level1.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class World:
    def __init__(self, level: dict | None = None):
        self.level = level or load_level()
        self.cfg = {**DEFAULT_CONFIG, **self.level.get("config", {})}
        self.rules = self.level["rules"]
        self.rule_by_id = {r["id"]: r for r in self.rules}
        self.npcs = self.level["npcs"]
        self.rooms = self.level["rooms"]
        self.goal_flag = self.level["goal_flag"]
        self.known_codes = {r["needs"]["has_code"] for r in self.rules if "has_code" in r["needs"]}
        self._code_re = re.compile(self.cfg["code_regex"])

    # ---- 状态 ----
    def initial(self) -> State:
        s = self.level["start"]
        return State(s["room"], frozenset(s.get("flags", [])), frozenset(s.get("items", [])))

    def is_won(self, state: State) -> bool:
        return self.goal_flag in state.flags

    def extract_code(self, text: str) -> str | None:
        """密码用正则从玩家输入里取，不交给 Jev。"""
        m = self._code_re.search(text)
        return m.group(0) if m else None

    # ---- 规则筛选 ----
    def needs_ok(self, state: State, needs: dict, code: str | None = None) -> bool:
        if "room" in needs and state.room != needs["room"]:
            return False
        if "room_in" in needs and state.room not in needs["room_in"]:
            return False
        if not set(needs.get("flags", [])) <= state.flags:
            return False
        if set(needs.get("flags_absent", [])) & state.flags:
            return False
        if not set(needs.get("items", [])) <= state.items:
            return False
        if set(needs.get("items_absent", [])) & state.items:
            return False
        if "has_code" in needs and code != needs["has_code"]:
            return False
        if needs.get("code_wrong") and (code is None or code in self.known_codes):
            return False
        return True

    def legal_rules(self, state: State, code: str | None = None) -> list[dict]:
        return [r for r in self.rules if self.needs_ok(state, r["needs"], code)]

    def apply(self, state: State, rule: dict) -> State:
        return self._apply_effects(state, rule.get("effects", {}))

    def _apply_effects(self, state: State, fx: dict) -> State:
        room = fx.get("set_room", state.room)
        flags = (state.flags | set(fx.get("set_flags", []))) - set(fx.get("clear_flags", []))
        items = (state.items | set(fx.get("add_items", []))) - set(fx.get("remove_items", []))
        return replace(state, room=room, flags=frozenset(flags), items=frozenset(items))

    # ---- NPC ----
    def npc_here(self, state: State) -> str | None:
        for nid, npc in self.npcs.items():
            if npc["room"] == state.room:
                return nid
        return None

    def score_level(self, score: float) -> int:
        """score 是概率加权值，可落在两档之间；四舍五入后取档（规则集中在此，可改）。"""
        n = len(self.cfg["attitude_levels"])
        return max(0, min(n - 1, math.floor(score + 0.5)))

    def trust_delta(self, level: int) -> int:
        return self.cfg["score_to_trust"][level]

    def adjust_trust(self, state: State, npc: str, delta: int) -> State:
        v = state.get_trust(npc) + delta
        v = max(self.cfg["trust_min"], min(self.cfg["trust_max"], v))
        return state.with_trust(npc, v)

    def behaviors(self, npc: str) -> list[dict]:
        return self.npcs[npc]["behaviors"]

    def allowed_behaviors(self, npc: str, trust: int) -> list[dict]:
        return [b for b in self.behaviors(npc) if b.get("min_trust", -99) <= trust]

    def apply_behavior(self, state: State, behavior: dict) -> State:
        return self._apply_effects(state, behavior.get("effects", {}))

    # ---- 展示 ----
    def item_name(self, item: str) -> str:
        return self.level.get("item_names", {}).get(item, item)

    def elsewhere_rules(self, state: State, code: str | None = None) -> list[dict]:
        """只差"人不在对的房间"的规则：玩家想做这件事，但这里做不了（只用来给出明确的指路，不改状态）。"""
        out = []
        for r in self.rules:
            if not r.get("elsewhere") or self.needs_ok(state, r["needs"], code):
                continue
            rest = {k: v for k, v in r["needs"].items() if k not in ("room", "room_in")}
            if self.needs_ok(state, rest, code):
                out.append(r)
        return out

    def active_spoilers(self, state: State) -> list[str]:
        """剧透词按进度生效：玩家已经知道的东西不再算剧透。"""
        out = []
        for e in self.level.get("flavor", {}).get("spoilers", []):
            if isinstance(e, str):
                out.append(e)
            elif not (set(e.get("unless_flags", [])) & state.flags or set(e.get("unless_items", [])) & state.items):
                out.append(e["word"])
        return out

    def hint_stage(self, state: State) -> tuple[int, list[str]]:
        """按当前状态找第一个匹配的提示阶段，返回 (阶段序号, 递进提示)。"""
        for i, h in enumerate(self.level.get("hints", [])):
            if self.needs_ok(state, h["when"]):
                return i, h["levels"]
        return -1, ["（暂时没有可提示的内容。）"]

    def describe(self, state: State) -> str:
        room = self.rooms[state.room]
        parts = [f"【{room['name']}】{room['desc']}"]
        parts += [e["text"] for e in room.get("extras", []) if e["flag"] in state.flags]
        return "\n".join(parts)
