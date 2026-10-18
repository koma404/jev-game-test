---
name: delegate
description: 把批量、自包含的机械活（批量翻译/总结/分类/打标签、格式转换、样板代码或文档初稿）交给便宜的 DeepSeek 完成，结果写文件、只回一行状态，以省钱省主会话 Token。用户说"分给 DeepSeek""省 Token""批量处理"，或任务是大量相似的小活、且输出可快速核对时使用。调试、重构、跨文件改动、需要仓库上下文或严谨推理的任务不要用。
---

# 把便宜的活分给 DeepSeek

脚本 `delegate.py` 与本文件在同一目录（Skill 加载时给出的 Base directory）。本 Skill 可能来自附加的另一个仓库，不在当前工作目录，所以先定位脚本：
```
DELEGATE=$(find / -path '*/skills/delegate/delegate.py' -not -path '*/node_modules/*' 2>/dev/null | head -1)
```
下面的命令都用 `$DELEGATE` 代替路径。结果写文件，终端只打印一行状态，模型的完整输出不进入主上下文。

## 先判断值不值得分

分：批量、自包含、一条指令能说清、输出能快速核对、出错代价低。
不分：需要仓库上下文或多步工具调用、需要严谨推理或长记忆、安全相关判断，以及活太小（写 prompt 加读结果的开销比直接做更大）。

拿不准就自己做。

## 用法

单任务：
```
python3 $DELEGATE --task "指令" --input in.md --out out/result.md
```

批量（jsonl，每行 `{"id": "...", "prompt": "..."}`，可选 `"input"` 指向要附带的文件；也可用 `--task` 给统一指令）：
```
python3 $DELEGATE --batch jobs.jsonl --out out/results.jsonl --workers 8
```
结果每行 `{"id", "ok", "output"|"error"}`。

常用参数：`--think`（允许推理，默认关）、`--json`（要求并清理为合法 JSON）、`--max-chars N`（输出过长视为失败）、`--model`、`--max-tokens`、`--task-file`。

## 读结果的纪律

- 状态行以 `OK` 开头才算成功；`FAIL` 或 `PARTIAL` 要看 `problem=` / `failed_ids=`，对失败的条目自己重做或重试。
- 只读需要的部分（`head`、`grep`、`jq`、抽样几条），不要把整个结果文件 cat 回上下文，否则等于白分。
- **交付前必须核对。** DeepSeek 有迎合、自造术语、丢前文、数错数的倾向。批量结果至少抽样检查格式和内容，能写校验就写（行数对不对、JSON 能不能解析、必填字段在不在）。

## 后端

- 设了 `DEEPSEEK_API_KEY`：直连 api.deepseek.com（默认模型 `deepseek-chat`）。
- 否则走 OpenRouter（云端会话里凭据由代理注入，默认模型 `deepseek/deepseek-v4.1-flash`，带推理的模型，脚本默认给 OpenRouter 请求加 `reasoning: {enabled: false}` 关掉推理——否则推理 token 照样计费、还可能把 `max_tokens` 用光导致正文为空；需要推理时加 `--think`）。
- 直连需要环境网络策略放行 `api.deepseek.com`；不通就用 OpenRouter。

## 成本参考

实测 4 条短批量任务共 217 个 Token，约 0.0001 美元。分出去的活本身几乎不花钱，主要开销在我写指令和读摘要。
