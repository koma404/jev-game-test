---
name: delegate
description: 把繁重但边界清晰的活交给便宜快速的 DeepSeek 代工，省主会话 Token。两种方式：delegate.py 一次性补全（批量翻译/总结/分类/打标签、格式转换、文案初稿）；agent.py 让 DeepSeek 代理（pi CLI）在隔离的 git worktree 里自己读写文件、跑检查（补单元测试、按明确规格的多文件机械改动、样板代码、迁移），产出 patch 由我审阅后合并。用户说"分给 DeepSeek""省 Token""代工""批量处理"，或任务是边界清晰的繁重执行活时使用。设计、架构、复杂算法、调试、数值平衡、判断质量、安全相关的活我自己做。
---

# DeepSeek 代工

分工原则（来自一份"Opus 驱动 DeepSeek"的工作流，本仓库实测后采用）：**设计、架构、复杂算法我自己做；其余繁重的执行活交给 DeepSeek，代替子代理。任务说明要详细、边界清晰。** 我负责出规格和验收，DeepSeek 负责执行。

脚本和本文件在同一目录。先定位：
```
D=$(dirname "$(find / -path '*/skills/delegate/agent.py' -not -path '*/node_modules/*' 2>/dev/null | head -1)")
```

## 选哪种方式

| | `delegate.py`（补全） | `agent.py`（代理） |
|---|---|---|
| 适合 | 30 条以上相似的小活：翻译、总结、分类、打标签、台词 / 文案初稿、格式转换 | 要读仓库、改文件、跑命令的执行活：补单元测试、按明确规格改几个文件、样板代码、重命名 / 迁移 |
| DeepSeek 能做什么 | 只看我给的输入，回一段文字 | 在仓库副本里读写文件、跑 bash（pi 的 read / bash / edit / write） |
| 产出 | 结果文件 | `patch.diff`（我审阅后 `git apply`） |
| 实测 | 24 条陪审员标签 $0.0007；翻译 3 词 $0.00001 | 给 `twelve/stats.py` 写 9 个测试：23 秒、7 轮、约 $0.009，测试全过、没越界，直接合并 |

不分：设计和架构决策、复杂算法、调试（找不到原因的 bug）、调数值平衡、判断陈词 / 文案好坏、安全相关；活太小（写说明加验收比自己做还费事）；拿不准就自己做。

## agent.py：派一个代码活

```
python3 $D/agent.py --task-file task.md --allow 'twelve/tests/test_x.py' --check 'python -m unittest twelve.tests.test_x -q'
```
- 在 `/tmp/delegate-agent/<时间>/wt` 建 git worktree（从 HEAD，并带上原仓库未提交的改动；未跟踪的新文件不带），代理只在里面干活，**不碰原仓库**。
- 第一次运行会自动把 pi 装到 `~/.cache/delegate-pi`（约一分钟；云端会话是临时的，每个新会话装一次）。
- 终端只打印一行：`OK|PARTIAL|FAIL 秒数 turns tools tokens 花费 files check dir problem=…`。同目录有 `patch.diff`、`final.txt`（代理的汇报）、`check.txt`、`events.jsonl`（完整过程，排查时才看）、`task.md`。
- `--allow`：允许改的文件（逗号分隔 glob），越界的改动在状态行标出（PARTIAL）。`--check`：写进任务让代理自己跑，结束后脚本再在 worktree 里独立跑一遍。
- 默认不让 pi 读 CLAUDE.md（本仓库的很长，每轮都要计费）；任务需要的背景写进任务说明。真需要时加 `--context-files`。
- 其他：`--timeout`（默认 900 秒，到点强停）、`--thinking low|medium|high`（默认 off）、`--model`。用完 `python3 $D/agent.py clean` 删 worktree。

### 写任务说明（决定成败的部分）

一份好的说明包含：
1. **目标和成品**：新建 / 修改哪个文件，交付什么，什么算完成（"测试 5–10 个""函数签名不变"）。
2. **背景指路**：先读哪些文件、哪些字段 / 函数是关键，别让它凭空假设。
3. **边界**：明确不许改的文件；发现别处的问题只汇报、不动手。
4. **验收**：检查命令，必须跑通；需要断言具体行为（"检查具体数字出现在文本里"）。
5. **数量和范围上限**：防止堆砌。

脚本自动附加的规则：只做写明的事、不 commit / push / 装依赖、检查不过修三次还不过就停、信息不足回 `NEED: 问题`、结束时 ≤5 行汇报。

### 验收（我必须做，不能跳）

1. 状态行 `OK` 才往下看；`NEED:` → 补说明重派；`PARTIAL`（越界 / 检查失败）→ 看 problem 决定是改说明重派还是自己收尾。
2. 读 `final.txt` 和 `patch.diff`（diff 太长就先 `--stat`，再挑关键部分读）。看：是否只改了该改的、测试断言的是不是真实行为（不是把错误输出抄成期望值）、有没有偷偷放宽检查。
3. `git apply <dir>/patch.diff`，在原仓库跑完整测试，通过再提交。代理汇报里提到的"发现的问题"由我判断要不要修。
4. `agent.py clean`。

## delegate.py：一次性补全

单任务：`python3 $D/delegate.py --task "指令" --input in.md --out out/result.md`
批量（jsonl，每行 `{"id", "prompt"}`，可选 `"input"` 文件路径；也可用 `--task` 给统一指令）：
`python3 $D/delegate.py --batch jobs.jsonl --out out/results.jsonl --workers 8`，结果每行 `{"id", "ok", "output"|"error"}`。
常用参数：`--timeout`（默认 60）、`--think`（允许推理，默认关）、`--json`、`--max-chars N`、`--model`、`--max-tokens`、`--task-file`。

写指令：先定最小成品（条数、字数、格式、不要什么）；缺信息时模型只回一行 `NEED: 问题`，脚本标成 `FAIL`，补全指令再发，别硬重试；只传这件事要的字段（比如不要把陪审员的在意点 / 雷区发出去）；批量先用 2–3 条试，对了再放量。

读结果：状态行 `OK` 才算成功；只读需要的部分（`head` / `jq` / 抽样），不要整个 cat 回上下文。**交付前必须核对**：内容约六成能直接用（迎合、自造、丢信息、数错数），能写校验就写。

## 熔断（两种方式通用）

- 单次请求 / 整个代理都有超时，不要越调越长。
- 同一个活因为同一个原因连续失败两次，第三次必须换办法：改说明、拆小、或者自己做。
- 失败如实报告；超时、被取消、检查没过都不能当成功。

## 后端和模型

- 默认模型 `deepseek/deepseek-v4.1-flash-1017`（OpenRouter，2026-10-17 版；$0.15 / $0.60 每百万 token 输入 / 输出，约是无日期版的 2.5 / 5 倍，仍然很便宜）。旧版用 `--model deepseek/deepseek-v4.1-flash`。
- 云端会话里 OpenRouter 的凭据由代理注入，脚本里不放 key。设了 `DEEPSEEK_API_KEY` 时 `delegate.py` 改为直连 api.deepseek.com（需要网络放行）。
- 推理默认关：推理 token 照样计费，还可能把 `max_tokens` 用光导致正文为空。
- pi 不认识这个模型名会打印一行 warning（按自定义模型继续），无害；它自带的花费估算不准，状态行的花费由 `agent.py` 按 OpenRouter 标价计算（缓存命中按全价估，宁高勿低）。
- opencode 在云端沙箱里会卡住（启动 / 后台进程挂起），所以代理用 pi。
