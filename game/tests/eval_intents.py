"""意图映射准确率评测：python -m game.tests.eval_intents [--jev mock|real]

expect 取值：规则 id / none（通用拒绝）/ clarify（低置信度反问）。
MockJev 与 RealJev 跑同一套样例，输出准确率和错例，便于对比。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..engine import Engine
from ..jev_client import MockJev, RealJev
from ..world import State, World

SAMPLES = Path(__file__).with_name("intents.jsonl")


def load_samples(path=SAMPLES):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def outcome(plan) -> str:
    if plan.kind == "clarify":
        return "clarify"
    if plan.kind == "refuse":
        return "none"
    return plan.rule["id"]


def evaluate(jev, world: World | None = None, samples=None):
    world = world or World()
    results = []
    for s in samples or load_samples():
        st = s["state"]
        state = State(st["room"], frozenset(st["flags"]), frozenset(st["items"]))
        got = outcome(Engine(world, jev, state=state).plan(s["text"]))
        results.append((s, got))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jev", choices=["mock", "real"], default="mock")
    args = ap.parse_args(argv)
    jev = MockJev() if args.jev == "mock" else RealJev()
    res = evaluate(jev)
    wrong = [(s, g) for s, g in res if s["expect"] != g]
    print(f"[{jev.name}] 准确率 {len(res) - len(wrong)}/{len(res)} = {(len(res) - len(wrong)) / len(res):.0%}")
    for s, g in wrong:
        print(f"  ✗ {s['text']!r} 期望 {s['expect']} 实得 {g}")


if __name__ == "__main__":
    main()
