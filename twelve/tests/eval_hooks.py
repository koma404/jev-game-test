"""Jev 判断准确率：python -m twelve.tests.eval_hooks [--jev mock|real]

hooks.jsonl 每条写明：应该戳中谁（hit）、不该戳中谁（not_hit）、踩到谁的雷（dislike）、是否操纵（manip）、是否在念案卷（recite）。
只写确定无疑的期望；模糊的不写。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from twelve.judge import Judge, MockClient
from twelve.server import load_case

SAMPLES = Path(__file__).with_name("hooks.jsonl")


def run(client, case):
    judge = Judge(case, client)
    th = case["config"]["hit_threshold"]
    ok = total = 0
    misses = []
    for line in open(SAMPLES, encoding="utf-8"):
        if not line.strip():
            continue
        s = json.loads(line)
        jd = judge.judge(s["text"])
        checks = [(f"戳中 {k}", jd.hits[k] > th) for k in s.get("hit", [])]
        checks += [(f"不该戳中 {k}", jd.hits[k] <= th) for k in s.get("not_hit", [])]
        checks += [(f"雷区 {k}", jd.dislikes[k] > th) for k in s.get("dislike", [])]
        if "manip" in s:
            checks.append(("操纵" if s["manip"] else "不是操纵", (jd.manip > case["config"]["manip_threshold"]) == s["manip"]))
        if "recite" in s:
            checks.append(("念案卷" if s["recite"] else "不是念案卷", (jd.recite > 0.5) == s["recite"]))
        for name, good in checks:
            total += 1
            ok += good
            if not good:
                misses.append(f"  ✗ {name}  ← {s['text']}")
    return ok, total, misses


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--jev", choices=["mock", "real"], default="mock")
    args = ap.parse_args(argv)
    case = load_case()
    if args.jev == "mock":
        client = MockClient(case)
    else:
        from twelve.link import KeepAliveJev
        client = KeepAliveJev()
    ok, total, misses = run(client, case)
    print(f"[{args.jev}] 判断准确率 {ok}/{total} = {ok / total:.0%}")
    print("\n".join(misses))


if __name__ == "__main__":
    main()
