#!/usr/bin/env python3
"""把多步的代码活交给 DeepSeek 代理（pi CLI）在隔离的 git worktree 里做，终端只打印一行状态。

和 delegate.py 的区别：delegate.py 是一次性补全（给输入、拿输出）；本脚本给 DeepSeek 一个能读写文件、
跑命令的代理，让它自己在仓库副本里改代码、跑检查。改动不碰原仓库，产出一个 patch，由调用方（Claude）
审阅后决定 `git apply`。

  agent.py --task-file task.md --allow 'twelve/tests/*' --check 'python -m unittest discover -s twelve/tests -t .'
  agent.py --task "..." --allow 'twelve/stats.py,twelve/tests/test_stats.py'
  agent.py clean        # 删掉所有代理 worktree

产出（目录在状态行里给出）：patch.diff、final.txt（代理最后的回复）、events.jsonl（完整过程）、check.txt。
"""
import argparse
import fnmatch
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

PI_VERSION = "0.73.1"
PI_HOME = os.path.expanduser("~/.cache/delegate-pi")
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
PRICE = {   # 美元 / 百万 token（OpenRouter 标价；pi 不认识这个模型，自己算的花费不准）
    "deepseek/deepseek-v4.1-flash-1017": (0.15, 0.60),
    "deepseek/deepseek-v4.1-flash": (0.0624, 0.12),
}
WORK_ROOT = os.path.join(tempfile.gettempdir(), "delegate-agent")
RULES = """\
你是被主代理（Claude）派来干活的执行者，在一个 git worktree 里工作，改动会被审阅后再合并。
- 只做任务里写的事，只改任务允许的文件；不要顺手重构、改格式、改无关代码、加没要求的功能。
- 不要 git commit / push / checkout，不要删除或改写任务没提到的文件，不要安装依赖。
- 先把任务点名的文件一次读完、定好方案，再动手；不要"读一点、改一点"地来回。
- 写完再集中验证一次：只跑任务给的检查命令（和你新写 / 改的测试），不要跑全量测试，除非任务要求；
  不要"验证、小改、再验证"反复循环。检查不过就修，修三次还不过就停下如实报告。
- 跑命令一律套 `timeout 120`。命令超时或没有新输出，先查原因，不要原样再等；同一件事连续两次没有进展就停下汇报。
- 信息不足、无法不猜测地完成时，不要猜着做：最后只回复一行 `NEED: 你的问题`。
- 结束时用不超过 5 行汇报：改了哪些文件、各做了什么、检查结果。不要寒暄。
"""


def sh(cmd, cwd=None, check=True, **kw):
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True, **kw)


def find_pi():
    for p in (shutil.which("pi"), os.path.join(PI_HOME, "node_modules", ".bin", "pi")):
        if p and os.path.exists(p):
            return p
    os.makedirs(PI_HOME, exist_ok=True)
    print(f"安装 pi {PI_VERSION} 到 {PI_HOME} …", file=sys.stderr)
    sh(["npm", "i", "--prefix", PI_HOME, "--no-audit", "--no-fund",
        f"@mariozechner/pi-coding-agent@{PI_VERSION}"])
    return os.path.join(PI_HOME, "node_modules", ".bin", "pi")


def make_worktree(repo):
    os.makedirs(WORK_ROOT, exist_ok=True)
    run = os.path.join(WORK_ROOT, time.strftime("%m%d-%H%M%S") + f"-{os.getpid()}")
    wt = os.path.join(run, "wt")
    sh(["git", "worktree", "add", "--detach", wt, "HEAD"], cwd=repo)
    dirty = sh(["git", "diff", "HEAD", "--binary"], cwd=repo).stdout
    if dirty:   # 把原仓库未提交的改动也带过去，代理看到的和我看到的一致（未跟踪的新文件不带）
        subprocess.run(["git", "apply", "--whitespace=nowarn"], cwd=wt, input=dirty, text=True, check=True)
        sh(["git", "add", "-A"], cwd=wt)
        sh(["git", "-c", "user.name=agent", "-c", "user.email=agent@local",
            "commit", "-qm", "base: uncommitted changes"], cwd=wt)
    return run, wt


def summarize(events_path, model):
    turns = tools = tin = tout = cache = 0
    final = ""
    errors = []
    with open(events_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("type")
            if t == "turn_start":
                turns += 1
            elif t == "tool_execution_start":
                tools += 1
            elif t == "message_end":
                m = e.get("message") or {}
                if m.get("role") != "assistant":
                    continue
                u = m.get("usage") or {}
                tin += u.get("input", 0)
                tout += u.get("output", 0)
                cache += u.get("cacheRead", 0)
                if m.get("errorMessage"):
                    errors.append(m["errorMessage"])
                text = "".join(c.get("text", "") for c in m.get("content", []) if c.get("type") == "text")
                if text.strip():
                    final = text.strip()
    pin, pout = PRICE.get(model, (0.15, 0.60))
    cost = ((tin + cache) * pin + tout * pout) / 1e6   # 缓存命中按全价估，宁高勿低
    return turns, tools, tin + cache, tout, cost, final, errors


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", choices=["run", "clean"], default="run")
    ap.add_argument("--task")
    ap.add_argument("--task-file")
    ap.add_argument("--allow", default="", help="允许改动的文件（逗号分隔的 glob）；越界的改动会在状态行标出")
    ap.add_argument("--check", help="完成后在 worktree 里跑的检查命令（也会写进任务让代理自己跑）")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--thinking", default="off", help="off / low / medium / high（默认 off：便宜、快）")
    ap.add_argument("--timeout", type=int, default=900, help="整个代理最多跑多少秒（默认 900）")
    ap.add_argument("--idle", type=int, default=120, help="连续多少秒没有任何新输出就停掉代理（默认 120）")
    ap.add_argument("--context-files", action="store_true", help="让 pi 读 CLAUDE.md / AGENTS.md（默认不读：本仓库的很长，每轮都要计费）")
    a = ap.parse_args()

    repo = sh(["git", "rev-parse", "--show-toplevel"], cwd=a.repo).stdout.strip()
    if a.cmd == "clean":
        n = 0
        for line in sh(["git", "worktree", "list", "--porcelain"], cwd=repo).stdout.splitlines():
            if line.startswith("worktree ") and line[9:].startswith(WORK_ROOT):
                sh(["git", "worktree", "remove", "--force", line[9:]], cwd=repo, check=False)
                n += 1
        shutil.rmtree(WORK_ROOT, ignore_errors=True)
        sh(["git", "worktree", "prune"], cwd=repo, check=False)
        print(f"OK 清理了 {n} 个代理 worktree")
        return 0

    task = a.task or (open(a.task_file, encoding="utf-8").read() if a.task_file else None)
    if not task:
        ap.error("需要 --task 或 --task-file")
    allow = [g.strip() for g in a.allow.split(",") if g.strip()]
    if allow:
        task += "\n\n只允许改动这些文件（glob）：" + "、".join(allow)
    if a.check:
        task += f"\n\n完成后运行检查命令，必须通过：`{a.check}`"

    pi = find_pi()
    run, wt = make_worktree(repo)
    with open(os.path.join(run, "task.md"), "w", encoding="utf-8") as f:
        f.write(task)
    env = dict(os.environ)
    env.setdefault("OPENROUTER_API_KEY", "placeholder")   # 云端会话里真正的凭据由代理注入
    if os.path.exists("/root/.ccr/ca-bundle.crt"):
        env.setdefault("NODE_EXTRA_CA_CERTS", "/root/.ccr/ca-bundle.crt")
    cmd = [pi, "--provider", "openrouter", "--model", a.model, "--thinking", a.thinking,
           "--no-session", "-ne", "-ns", "-np", "--mode", "json",
           "--append-system-prompt", RULES, "-p", task]
    if not a.context_files:
        cmd.insert(-2, "-nc")
    events = os.path.join(run, "events.jsonl")
    t0 = time.time()
    timed_out = idled = False
    rc = None
    with open(events, "w") as out, open(os.path.join(run, "stderr.txt"), "w") as err:
        p = subprocess.Popen(cmd, cwd=wt, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                             start_new_session=True)
        size, last = 0, time.time()
        while True:
            try:
                rc = p.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.time()
            cur = os.path.getsize(events)
            if cur != size:   # 代理还在吐事件（模型输出、工具进度）就算有进展
                size, last = cur, now
            if now - t0 > a.timeout:
                timed_out = True
            elif now - last > a.idle:   # 空闲熔断：干等没有新输出，不陪它等
                idled = True
            if timed_out or idled:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                    p.wait(timeout=10)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    os.killpg(p.pid, signal.SIGKILL)
                rc = -1
                break
    secs = time.time() - t0

    sh(["git", "add", "-A"], cwd=wt)
    patch = sh(["git", "diff", "--cached", "HEAD", "--binary"], cwd=wt).stdout
    with open(os.path.join(run, "patch.diff"), "w", encoding="utf-8") as f:
        f.write(patch)
    files = [p for p in sh(["git", "diff", "--cached", "HEAD", "--name-only"], cwd=wt).stdout.splitlines() if p]
    stat = sh(["git", "diff", "--cached", "HEAD", "--shortstat"], cwd=wt).stdout.strip()
    outside = [p for p in files if allow and not any(fnmatch.fnmatch(p, g) for g in allow)]

    check_ok = None
    if a.check and files:
        r = subprocess.run(a.check, shell=True, cwd=wt, text=True, capture_output=True, timeout=600)
        check_ok = r.returncode == 0
        with open(os.path.join(run, "check.txt"), "w", encoding="utf-8") as f:
            f.write(r.stdout + r.stderr)

    turns, tools, tin, tout, cost, final, errors = summarize(events, a.model)
    with open(os.path.join(run, "final.txt"), "w", encoding="utf-8") as f:
        f.write(final)

    need = final.startswith("NEED:")
    problems = []
    if timed_out:
        problems.append(f"超时 {a.timeout}s")
    elif idled:
        problems.append(f"卡住：{a.idle}s 没有新输出，已停掉")
    elif rc != 0:
        problems.append(f"pi 退出码 {rc}")
    if errors:
        problems.append("模型报错: " + errors[-1][:120])
    if need:
        problems.append("代理要澄清: " + final[5:].strip()[:150])
    if not files and not need:
        problems.append("没有任何改动")
    if outside:
        problems.append("越界改动: " + ",".join(outside))
    if check_ok is False:
        problems.append("检查没通过（见 check.txt）")
    status = "OK" if not problems else ("FAIL" if (timed_out or idled or need or not files or rc != 0) else "PARTIAL")
    line = (f"{status} {secs:.0f}s turns={turns} tools={tools} tokens={tin}+{tout} ~${cost:.4f} "
            f"files={len(files)} ({stat or '无改动'}) check={'-' if check_ok is None else ('pass' if check_ok else 'FAIL')} "
            f"dir={run}")
    if problems:
        line += " problem=" + "；".join(problems)
    print(line)
    return 0 if status == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
