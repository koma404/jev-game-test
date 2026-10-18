#!/usr/bin/env python3
"""把机械活分给便宜模型（默认 DeepSeek），结果写文件，终端只打印一行状态。

目的：让调用方（Claude）的上下文里只出现命令和一行摘要，而不是模型的完整输出。

后端选择：
  - 设置了 DEEPSEEK_API_KEY  -> 直连 api.deepseek.com（模型默认 deepseek-chat）
  - 否则                     -> OpenRouter（凭据由代理注入，模型默认 deepseek/deepseek-v4.1-flash）

单任务：
  delegate.py --task "把下面内容翻译成英文" --input a.md --out out/a.en.md
  delegate.py --task-file prompt.txt --input a.md --out out.md

批量（jsonl，每行 {"id": "...", "prompt": "..."}，可选 "input" 为要附带的文件路径）：
  delegate.py --batch jobs.jsonl --out results.jsonl --workers 8
  结果每行 {"id", "ok", "output"|"error"}

校验（可选，失败则非 0 退出且不丢结果）：
  --json            要求输出是合法 JSON
  --max-chars N     输出超过 N 字符则视为失败
"""
import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
TIMEOUT_OVERRIDE = [None]
TIMEOUT = 60   # 单次等待不超过 60 秒；超时按失败重试，不要把等待越拉越长（--timeout 可改）
DEFAULT_SYSTEM = (
    "你是一个高效的执行助手。严格按指令完成任务，只输出结果本身，不要寒暄、不要解释、不要夸奖。\n"
    "只交付指令要求的最小成品：不扩写、不顺手改进、不加指令没要的内容；可逆的小选择（措辞、格式细节）选一个就定，不要列备选。\n"
    "如果信息不足、无法在不猜测的情况下完成，只输出一行 `NEED: 你要问的一个简短问题`，不要猜着做。"
)


def backend(model):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return DEEPSEEK_URL, {"Authorization": f"Bearer {key}"}, model or "deepseek-chat"
    return OPENROUTER_URL, {}, model or "deepseek/deepseek-v4.1-flash"


def call(prompt, system, model, max_tokens, think=False, retries=3):
    url, extra, model = backend(model)
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if url == OPENROUTER_URL and not think:
        payload["reasoning"] = {"enabled": False}   # 推理模型默认先想一大段，推理 token 照样计费；机械活不需要
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", **extra}
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            r = json.load(urllib.request.urlopen(req, timeout=TIMEOUT_OVERRIDE[0] or TIMEOUT))
            text = r["choices"][0]["message"]["content"]
            if text is None:   # 推理把 max_tokens 用光、正文为空
                raise RuntimeError("模型没有返回正文（可能推理用光了 max_tokens）")
            u = r.get("usage", {})
            return text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), u.get("cost", 0.0)
        except (urllib.error.URLError, KeyError, TimeoutError, json.JSONDecodeError, RuntimeError) as e:
            last = e
            time.sleep(2 ** (i + 1))
    raise RuntimeError(f"调用失败: {last}")


def build_prompt(task, input_path):
    if not input_path:
        return task
    with open(input_path, encoding="utf-8") as f:
        return f"{task}\n\n=== 输入（{input_path}）===\n{f.read()}"


def strip_fence(text):
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def check(text, args):
    if text.strip().startswith("NEED:"):
        return "模型需要澄清（没有硬做）：" + text.strip()[5:].strip()[:200]
    if args.json:
        try:
            json.loads(strip_fence(text))
        except json.JSONDecodeError as e:
            return f"不是合法 JSON: {e}"
    if args.max_chars and len(text) > args.max_chars:
        return f"输出 {len(text)} 字符，超过上限 {args.max_chars}"
    return None


def run_single(args):
    task = open(args.task_file, encoding="utf-8").read() if args.task_file else args.task
    if not task:
        sys.exit("需要 --task 或 --task-file")
    text, tin, tout, cost = call(build_prompt(task, args.input), args.system, args.model, args.max_tokens, args.think)
    if args.json:
        text = strip_fence(text)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    problem = check(text, args)
    status = "FAIL" if problem else "OK"
    preview = text[:120].replace("\n", " ")
    print(f"{status} out={args.out} chars={len(text)} tokens={tin}+{tout} cost=${cost:.5f}"
          + (f" problem={problem}" if problem else "") + f" preview={preview!r}")
    return 1 if problem else 0


def run_batch(args):
    jobs = [json.loads(l) for l in open(args.batch, encoding="utf-8") if l.strip()]
    base = args.task or ""

    def one(job):
        try:
            prompt = build_prompt((job.get("prompt") or base), job.get("input"))
            text, tin, tout, cost = call(prompt, args.system, args.model, args.max_tokens, args.think)
            if args.json:
                text = strip_fence(text)
            problem = check(text, args)
            rec = {"id": job.get("id"), "ok": problem is None, "output": text}
            if problem:
                rec["error"] = problem
            return rec, tin, tout, cost
        except Exception as e:  # 单条失败不影响其余
            return {"id": job.get("id"), "ok": False, "error": str(e)}, 0, 0, 0.0

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(one, jobs))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for rec, *_ in results:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ok = sum(1 for r, *_ in results if r["ok"])
    tin = sum(x[1] for x in results)
    tout = sum(x[2] for x in results)
    cost = sum(x[3] for x in results)
    failed = [r["id"] for r, *_ in results if not r["ok"]][:10]
    print(f"{'OK' if ok == len(jobs) else 'PARTIAL'} out={args.out} done={ok}/{len(jobs)} "
          f"tokens={tin}+{tout} cost=${cost:.5f}" + (f" failed_ids={failed}" if failed else ""))
    return 0 if ok == len(jobs) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--task", help="任务指令")
    p.add_argument("--task-file", help="从文件读取任务指令")
    p.add_argument("--input", help="附带给模型的输入文件")
    p.add_argument("--batch", help="批量任务 jsonl")
    p.add_argument("--out", required=True, help="输出文件")
    p.add_argument("--model", help="覆盖默认模型")
    p.add_argument("--system", default=DEFAULT_SYSTEM)
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--timeout", type=int, default=TIMEOUT, help="单次请求超时秒数")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--think", action="store_true", help="允许模型推理（默认关闭，更便宜更快）")
    p.add_argument("--json", action="store_true", help="要求输出为合法 JSON")
    p.add_argument("--max-chars", type=int, help="输出字符数上限")
    args = p.parse_args()
    TIMEOUT_OVERRIDE[0] = args.timeout
    sys.exit(run_batch(args) if args.batch else run_single(args))


if __name__ == "__main__":
    main()
