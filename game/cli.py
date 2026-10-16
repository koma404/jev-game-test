"""命令行界面。python -m game.cli [--jev auto|mock|real] [--debug]"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .engine import Engine, TurnLogger
from .flavor import FlavorWriter
from .jev_client import JevError, MockJev, RealJev
from .world import World, load_level

HELP = "直接输入你想做的事。/look 看周围  /inv 物品  /clues 线索  /hint 提示  /quit 退出"


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
    ap.add_argument("--no-flavor", action="store_true",
                    help="关闭旁白（默认在 OpenRouter 后端自动开启：LLM 把通用拒绝改写成调侃文字）")
    ap.add_argument("--threshold", type=float, help="低置信度阈值，默认取关卡配置(0.6)")
    ap.add_argument("--log-dir", default="game/logs", help="日志目录，传空字符串关闭")
    args = ap.parse_args(argv)

    world = World(load_level(args.level))
    try:
        jev = make_jev(args.jev)
    except JevError as e:
        print(f"无法启用真实 Jev: {e}", file=sys.stderr)
        return 2

    if isinstance(jev, RealJev):
        jev.on_retry = lambda n, total, err: print(f"  （Jev 连接失败，{n}/{total} 次重试中：{err}）", flush=True)

    def show(req, resp):
        print(_dim("--- Jev 请求 ---\n" + json.dumps(req, ensure_ascii=False, indent=2) +
                   "\n--- Jev 返回 ---\n" + json.dumps(resp, ensure_ascii=False, indent=2)))

    writer = None
    if isinstance(jev, RealJev) and not args.no_flavor:
        writer = FlavorWriter.from_jev(jev)

    fails = {"n": 0}

    def flavor(said, scene, fallback):
        if fails["n"] >= 3:   # 连续失败就别再让玩家白等了
            return fallback
        writer.spoilers = tuple(world.active_spoilers(eng.state))
        text = writer.write(said, scene, fallback)
        if writer.last_error:
            fails["n"] += 1
            print(_dim(f"  （旁白暂不可用，已用预写文本：{writer.last_error}）"))
            if fails["n"] >= 3:
                print(_dim("  （旁白连续失败 3 次，本次游戏不再尝试。可用 --no-flavor 关闭，或运行 netcheck.bat 排查）"))
        else:
            fails["n"] = 0
        return text

    eng = Engine(world, jev, threshold=args.threshold, logger=TurnLogger(args.log_dir or None),
                 on_exchange=show if args.debug else None, refuse_writer=flavor if writer else None)
    print(f"== {world.level['title']} ==  (后端: {jev.name}，旁白: {'开' if writer else '关'})")
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
            print("物品: " + (", ".join(world.item_name(i) for i in sorted(eng.state.items)) or "无"))
        elif line == "/hint":
            print(eng.hint())
        elif line == "/clues":
            print("线索: " + ("; ".join(eng.clues) or "无"))
        elif line.startswith("/"):
            print("不认识这个命令。可用：/look /inv /clues /hint /quit")
        else:
            if isinstance(jev, RealJev):
                print("  （等待 Jev 回应……）", flush=True)
            t0 = time.time()
            res = eng.turn(line)
            elapsed = time.time() - t0
            print(res.text)
            if isinstance(jev, RealJev):
                print(_dim(f"  （Jev 用时 {elapsed:.1f} 秒）"))
            if res.info.get("nudge"):
                print(_dim("  （好像卡住了？输入 /hint，或直接说「给我提示」，会给你一点方向。）"))
            if res.kind == "jev_down":
                print(_dim(f"原因: {res.info.get('error')}"))
            if res.clue:
                print(f"获得线索 [{res.clue}]")
            if args.debug:
                print(_dim(f"[{res.kind}] rule={res.rule_id} {json.dumps(res.info, ensure_ascii=False)}"))
            if res.done:
                return 0


if __name__ == "__main__":
    sys.exit(main())
