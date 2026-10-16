"""命令行界面。python -m game.cli [--jev auto|mock|real] [--debug]"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .engine import Engine, TurnLogger
from .jev_client import JevError, MockJev, RealJev
from .world import World, load_level

HELP = "直接输入你想做的事。/look 看周围  /inv 物品  /clues 线索  /quit 退出"


def make_jev(kind: str):
    if kind == "mock" or (kind == "auto" and not RealJev.available()):
        return MockJev()
    return RealJev()


def _dim(text: str) -> str:
    """灰色输出；Win7 的 cmd 不认 ANSI 转义，直接原样输出。"""
    return text if os.name == "nt" else f"\033[2m{text}\033[0m"


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # 控制台编码装不下的字符用 ? 代替，不崩溃
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="停电的档案馆 —— Jev 文字解谜 demo")
    ap.add_argument("--jev", choices=["auto", "mock", "real"], default="auto",
                    help="auto: 有 TYPESAFE_API_KEY 用真实 Jev，否则 Mock")
    ap.add_argument("--level", help="关卡 json，默认 level1")
    ap.add_argument("--debug", action="store_true", help="打印每次 Jev 请求与返回")
    ap.add_argument("--threshold", type=float, help="低置信度阈值，默认取关卡配置(0.6)")
    ap.add_argument("--log-dir", default="game/logs", help="日志目录，传空字符串关闭")
    args = ap.parse_args(argv)

    world = World(load_level(args.level))
    try:
        jev = make_jev(args.jev)
    except JevError as e:
        print(f"无法启用真实 Jev: {e}", file=sys.stderr)
        return 2

    def show(req, resp):
        print(_dim("--- Jev 请求 ---\n" + json.dumps(req, ensure_ascii=False, indent=2) +
                   "\n--- Jev 返回 ---\n" + json.dumps(resp, ensure_ascii=False, indent=2)))

    eng = Engine(world, jev, threshold=args.threshold, logger=TurnLogger(args.log_dir or None),
                 on_exchange=show if args.debug else None)
    print(f"== {world.level['title']} ==  (后端: {jev.name})")
    print(world.level["intro"])
    print(world.describe(eng.state))
    print(HELP)

    while True:
        try:
            line = input("> ")
        except EOFError:
            print()
            return 0
        line = line.strip()
        if not line:
            continue
        if line in ("/quit", "/exit", "退出"):
            return 0
        if line == "/help":
            print(HELP)
        elif line == "/look":
            print(world.describe(eng.state))
        elif line == "/inv":
            print("物品: " + (", ".join(sorted(eng.state.items)) or "无"))
        elif line == "/clues":
            print("线索: " + ("; ".join(eng.clues) or "无"))
        else:
            res = eng.turn(line)
            print(res.text)
            if res.clue:
                print(f"获得线索 [{res.clue}]")
            if args.debug:
                print(_dim(f"[{res.kind}] rule={res.rule_id} {json.dumps(res.info, ensure_ascii=False)}"))
            if res.done:
                return 0


if __name__ == "__main__":
    sys.exit(main())
