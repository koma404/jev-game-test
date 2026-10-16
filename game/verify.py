"""离线验证器：在“动作—状态”图上做 BFS。用法：python -m game.verify [level.json]"""
from __future__ import annotations

import sys
from collections import deque

from .world import State, World, load_level


def sample_code(rule: dict) -> str | None:
    needs = rule["needs"]
    if "has_code" in needs:
        return needs["has_code"]
    if needs.get("code_wrong"):
        return "9999"
    return None


def successors(world: World, state: State, used: set | None = None):
    """产出 (动作标签, 动作键, 下一状态)。NPC 对话枚举“态度档位 × 门槛允许的反应”。"""
    levels = world.cfg["attitude_levels"]
    for rule in world.rules:
        code = sample_code(rule)
        if not world.needs_ok(state, rule["needs"], code):
            continue
        if used is not None:
            used.add(("rule", rule["id"]))
        if rule.get("npc"):
            npc = rule["npc"]
            for lvl in reversed(range(len(levels))):
                s2 = world.adjust_trust(state, npc, world.trust_delta(lvl))
                for b in world.allowed_behaviors(npc, s2.get_trust(npc)):
                    if used is not None:
                        used.add(("behavior", b["id"]))
                    s3 = world.apply_behavior(s2, b)
                    tag = f"{levels[lvl]}→{b['id']}" if b.get("effects") else levels[lvl]
                    yield f"{rule['id']}({tag})", f"{rule['id']}({levels[lvl]})", s3
        else:
            label = f"{rule['id']}({code})" if "has_code" in rule["needs"] else rule["id"]
            yield label, label, world.apply(state, rule)


def explore(world: World, prune=None):
    """返回 (parent, edges, used, action_keys)。parent: 状态 -> (上一状态, 标签)。"""
    start = world.initial()
    parent = {start: None}
    edges: dict[State, set] = {}
    used: set = set()
    actions: set = set()
    q = deque([start])
    while q:
        s = q.popleft()
        edges[s] = set()
        if world.is_won(s):
            continue
        for label, key, nxt in successors(world, s, used):
            if prune and prune(nxt):
                continue
            actions.add(key)
            if nxt == s:
                continue
            edges[s].add(nxt)
            if nxt not in parent:
                parent[nxt] = (s, label)
                q.append(nxt)
    return parent, edges, used, actions


def path_to(parent, target) -> list[str]:
    out = []
    while parent[target] is not None:
        target, label = parent[target]
        out.append(label)
    return out[::-1]


def analyze(world: World) -> dict:
    parent, edges, used, actions = explore(world)
    wins = [s for s in parent if world.is_won(s)]
    shortest = min((path_to(parent, w) for w in wins), key=len) if wins else None

    # 死局：可达，但永远到不了通关
    rev: dict[State, set] = {s: set() for s in parent}
    for s, outs in edges.items():
        for t in outs:
            rev[t].add(s)
    alive, q = set(wins), deque(wins)
    while q:
        for p in rev[q.popleft()]:
            if p not in alive:
                alive.add(p)
                q.append(p)
    dead = [s for s in parent if s not in alive]

    all_ids = {("rule", r["id"]) for r in world.rules}
    for n in world.npcs:
        all_ids |= {("behavior", b["id"]) for b in world.behaviors(n)}
    unreachable = sorted(i for _, i in all_ids - used)

    # 信息依赖：规则需要的每个 flag，去掉它是否就无解
    deps = []
    for r in world.rules:
        fl = r["needs"].get("flags", [])
        if not fl:
            continue
        critical = []
        for f in fl:
            p2, *_ = explore(world, prune=lambda s, f=f: f in s.flags)
            if not any(world.is_won(s) for s in p2):
                critical.append(f)
        deps.append((r["id"], fl, critical))

    return {"states": len(parent), "actions": len(actions), "solvable": bool(wins),
            "shortest": shortest, "dead": dead, "unreachable": unreachable, "deps": deps}


def report(world: World) -> int:
    a = analyze(world)
    print(f"状态数: {a['states']}   动作数: {a['actions']}")
    if a["solvable"]:
        print(f"可解: 是，最短解 {len(a['shortest'])} 步")
        print("  " + " → ".join(a["shortest"]))
    else:
        print("可解: 否")
    print(f"死局状态: {len(a['dead'])}")
    for s in a["dead"][:5]:
        print(f"  例: room={s.room} flags={sorted(s.flags)} items={sorted(s.items)} trust={dict(s.trust)}")
    print(f"不可达规则: {len(a['unreachable'])}" + (f"  {a['unreachable']}" if a["unreachable"] else ""))
    for rid, flags, critical in a["deps"]:
        need = "{" + ", ".join(flags) + "}"
        if len(flags) > 1 and len(critical) == len(flags):
            tail = "（缺一不可）"
        elif critical:
            tail = f"（关键: {', '.join(critical)}）"
        else:
            tail = "（非关键，可绕过）"
        print(f"信息依赖: {rid} 需要 {need}{tail}")
    return 0 if a["solvable"] and not a["dead"] and not a["unreachable"] else 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return report(World(load_level(argv[0] if argv else None)))


if __name__ == "__main__":
    sys.exit(main())
