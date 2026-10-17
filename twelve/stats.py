"""把运行日志汇总成一页报告：python -m twelve.stats [日志文件 ...]

不给文件时读 twelve/logs/twelve.jsonl。也接受从 Render 日志页复制下来的文本：
每行找到第一个 `{` 当作 JSON 解析，解析不了的行跳过（复制的日志里没有打字预览，延迟只能看裁决里的每局统计）。
同一事件出现多次（文件和复制的平台日志混在一起）会按时间戳去重。"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict

from .logbook import latency, pct


def load(paths) -> list:
    seen, out = set(), []
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                i = line.find("{")
                if i < 0:
                    continue
                try:
                    e = json.loads(line[i:])
                except ValueError:
                    continue
                if not isinstance(e, dict) or "event" not in e:
                    continue
                key = (e.get("ts"), e.get("event"), e.get("cid"))
                if key in seen:
                    continue
                seen.add(key)
                out.append(e)
    out.sort(key=lambda e: e.get("ts", 0))
    return out


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _f(x, fmt="{:.1f}", none="—"):
    return none if x is None else fmt.format(x)


def _lat_line(name, d):
    if not d:
        return f"  {name:<14}（无数据）"
    return f"  {name:<14}中位 {d['p50']:>5} ms   90% {d['p90']:>5} ms   最慢 {d['max']:>5} ms   （{d['n']} 次）"


def report(events: list, case: dict | None = None) -> str:
    if not events:
        return "还没有日志。"
    by = defaultdict(list)
    for e in events:
        by[e["event"]].append(e)
    verdicts = by["verdict"]
    L = []
    first, last = events[0].get("t", ""), events[-1].get("t", "")
    players = {e.get("player") for e in by["open"] if e.get("player") not in (None, "?")}
    L.append(f"时间范围：{first} — {last}")
    L.append(f"开庭 {len(by['open'])} 次（{len(players)} 个不同 IP）；开始陈词 {len(by['begin'])}；"
             f"完成裁决 {len(verdicts)}；中途放弃 {len(by['abandon'])}")
    if by["refused"]:
        c = Counter(e.get("reason") for e in by["refused"])
        names = {"code": "口令错", "budget": "额度用完", "per_ip": "开庭太频繁", "full": "人满"}
        L.append("被拦下：" + "，".join(f"{names.get(k, k)} {v}" for k, v in c.most_common()))

    chans = Counter(v.get("channel", "release") for v in verdicts)
    if len(chans) > 1 or "beta" in chans:
        L.append("版本：" + "，".join(
            f"{'内测版' if ch == 'beta' else '发布版'} {n} 局 胜 {sum(1 for v in verdicts if v.get('channel', 'release') == ch and v.get('acquit'))}"
            for ch, n in chans.items()))
    # ---- 胜负 ----
    L.append("\n== 胜负 ==")
    if verdicts:
        wins = sum(1 for v in verdicts if v.get("acquit"))
        L.append(f"胜诉 {wins}/{len(verdicts)}（{wins / len(verdicts):.0%}）；平均 {_avg([v.get('votes') for v in verdicts]):.1f} 票无罪")
        dist = Counter(v.get("votes") for v in verdicts)
        L.append("票数分布：" + "  ".join(f"{k}票×{dist[k]}" for k in sorted(dist)))
        L.append(f"结束方式：主动结束 {sum(1 for v in verdicts if v.get('reason') == 'rested')}，"
                 f"时间到 {sum(1 for v in verdicts if v.get('reason') == 'timeout')}")
        L.append(f"每局平均：说出口 {_f(_avg([v.get('says') for v in verdicts]))} 句，"
                 f"预览 {_f(_avg([v.get('previews') for v in verdicts]), '{:.0f}')} 次，"
                 f"提示 {_f(_avg([v.get('hints') for v in verdicts]))} 次，"
                 f"用时 {_f(_avg([v.get('used_s') for v in verdicts]), '{:.0f}')} 秒，"
                 f"花费 ${_f(_avg([v.get('cost') for v in verdicts]), '{:.4f}')}")
        inj = sum(1 for v in verdicts if v.get("manip"))
        if inj:
            L.append(f"有 {inj} 局试过操纵/注入")
    else:
        L.append("还没有完成的对局。")

    # ---- 延迟 ----
    L.append("\n== 延迟（打字预览）==")
    pv = by["preview"]
    if pv:
        jev = [e.get("jev_ms") for e in pv if not e.get("cached")]
        srv = [e.get("server_ms") for e in pv]
        brw = [e.get("browser_prev_ms") for e in pv]
        L.append(_lat_line("Jev 往返", latency(jev)))
        L.append(_lat_line("服务端处理", latency(srv)))
        L.append(_lat_line("浏览器总耗时", latency(brw)))
        net = None if not latency(brw) or not latency(srv) else pct([x for x in brw if x], 50) - pct([x for x in srv if x], 50)
        if net is not None:
            L.append(f"  → 浏览器到服务器的网络往返约 {net:.0f} ms（浏览器总耗时中位 − 服务端中位）")
        hit = sum(1 for e in pv if e.get("cached"))
        L.append(f"  预览 {len(pv)} 次，其中命中缓存 {hit} 次")
    elif verdicts:
        # 只有平台日志（没有预览明细）：用每局裁决里的统计
        for k, name in (("jev", "Jev 往返"), ("server", "服务端处理"), ("browser", "浏览器总耗时")):
            p50 = [v.get("latency", {}).get(k, {}).get("p50") for v in verdicts]
            p90 = [v.get("latency", {}).get(k, {}).get("p90") for v in verdicts]
            L.append(f"  {name:<14}各局中位数的平均 {_f(_avg(p50), '{:.0f}')} ms，各局 90% 的平均 {_f(_avg(p90), '{:.0f}')} ms")
    else:
        L.append("  （无数据）")
    say_ms = [e.get("server_ms") for e in by["say"]]
    if say_ms:
        L.append(_lat_line("按回车到出结果", latency(say_ms)) + "（服务端，不含网络）")

    # ---- 陪审员 ----
    says = [e for e in by["say"] if e.get("manip", 0) <= 0.5]
    if verdicts or says:
        L.append("\n== 陪审员 ==")
        names = {j["id"]: j["name"] for j in (case or {}).get("jurors", [])}
        L.append("  陪审员        最终判无罪   平均最终倾向   被戳中率   踩雷率   （按说出口的句子算）")
        ids = []
        for e in verdicts + says:
            for k in (e.get("leans") or {}):
                if k not in ids:
                    ids.append(k)
        for k in ids:
            fl = [v["leans"][k] for v in verdicts if k in (v.get("leans") or {})]
            acq = sum(1 for x in fl if x > 0)
            hits = [e["hits"].get(k, 0) > 0.5 for e in says if k in (e.get("hits") or {})]
            dis = [e["dislikes"].get(k, 0) > 0.5 for e in says if k in (e.get("dislikes") or {})]
            label = f"{k} {names.get(k, '')}"
            L.append(f"  {label:<10} {(f'{acq}/{len(fl)}' if fl else '—'):>9}   {_f(_avg(fl), '{:+.2f}'):>11}   "
                     f"{_f(_avg(hits), '{:.0%}'):>8}   {_f(_avg(dis), '{:.0%}'):>6}")

    # ---- 论点 ----
    if by["say"]:
        L.append("\n== 说出口的句子 ==")
        args = Counter(e.get("argument") for e in by["say"])
        L.append("论点：" + "，".join(f"{k} {v}" for k, v in args.most_common()))
        L.append(f"质量平均 {_f(_avg([e.get('quality') for e in by['say']]), '{:.2f}')}（0–4）；"
                 f"判为操纵 {sum(1 for e in by['say'] if e.get('manip', 0) > 0.5)} 句；"
                 f"念案卷 {sum(1 for e in by['say'] if (e.get('recite') or 0) > 0.5)} 句")
        L.append("最近 10 句：")
        for e in by["say"][-10:]:
            L.append(f"  [{e.get('t', '')[11:]}] {e.get('cid', '')} {e.get('votes', '?')}票  {e.get('text', '')}")

    # ---- 出错 ----
    if by["jev_error"] or by["client_error"]:
        L.append("\n== 出错 ==")
        L.append(f"Jev 出错 {len(by['jev_error'])} 次；浏览器脚本出错 {len(by['client_error'])} 次")
        for e in (by["jev_error"] + by["client_error"])[-10:]:
            L.append(f"  [{e.get('t', '')}] {e['event']}: {e.get('error') or e.get('msg')} {e.get('ua', '')[:80]}")
    return "\n".join(L)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    paths = (argv if argv is not None else sys.argv[1:]) or ["twelve/logs/twelve.jsonl"]
    try:
        from .server import load_case
        print(report(load(paths), load_case()))
    except OSError as e:
        print(f"读不到日志：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
