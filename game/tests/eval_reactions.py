"""NPC 反应评测：python -m game.tests.eval_reactions [--jev mock|real]

reactions.jsonl 里每条样例从指定房间、指定初始信任出发跑一回合，
检查是否解锁线索（expect_unlock）。用来观察 NPC 描述和反应说明是否过于保守/宽松。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..engine import Engine
from ..jev_client import MockJev, RealJev
from ..world import State, World

SAMPLES = Path(__file__).with_name("reactions.jsonl")


def load_samples(path=SAMPLES):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def evaluate(jev, world: World | None = None, samples=None):
    world = world or World()
    results = []
    for s in samples or load_samples():
        state = State(s["room"]).with_trust(s["npc"], s["trust"])
        r = Engine(world, jev, state=state).turn(s["text"])
        results.append((s, bool(r.clue), r))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jev", choices=["mock", "real"], default="mock")
    args = ap.parse_args(argv)
    jev = MockJev() if args.jev == "mock" else RealJev()
    res = evaluate(jev)
    wrong = [(s, got, r) for s, got, r in res if s["expect_unlock"] != got]
    print(f"[{jev.name}] 反应准确率 {len(res) - len(wrong)}/{len(res)}")
    for s, got, r in wrong:
        print(f"  ✗ {s['npc']} trust{s['trust']} {s['text']!r} 期望解锁={s['expect_unlock']} 实得={got} ({r.info.get('reaction') or r.kind})")


if __name__ == "__main__":
    main()
