# Jev 文字解谜 demo（命令行）

这份文件是项目的完整设计说明，来自一次详细的讨论。请先通读，再按"开发顺序"开工。已定下的内容直接照做；标注"待确认"的地方在实现时再查文档或问用户。

## 1. 项目目标

做一个命令行文字解谜关卡 demo，用来展示 TypeSafe AI 的 Jev 模型（"System One"类模型）适合做什么。

核心思路：**Jev 只负责理解玩家的自由文本，并在人为设计的选项里做判断；所有游戏规则由代码决定。**

- Jev 不生成文字，只返回：Choice（选一个选项）、Score（按评分标准打分）、Noul（yes/no 概率）。
- 好处：比普通 LLM 更快、更便宜；输出被限制在预设选项里，游戏状态不会被模型带崩。
- 剧情文本用预写内容，不由 Jev 生成。
- 玩家输入完全自由，但每回合最多执行一个合法动作。

## 2. Jev API 格式（已对照官方文档核对）

```
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

请求体三个必填字段：`state`（字符串 / 对象 / 数组）、`model`（用 `"jev-latest"`，当前指向 jev-1.13.0）、`questions`（map，键自己起，答案按同样的键返回）。

三种问题：

```json
{
  "state": {"room": "hall", "player_said": "我想趁守卫不注意溜进去"},
  "model": "jev-latest",
  "questions": {
    "intent": {
      "type": "choice",
      "instructions": "玩家想做什么？",
      "criteria": {"sneak": "潜行进入", "talk_zhou": "和守卫对话", "none": "以上都不是"}
    },
    "trust_delta": {
      "type": "score",
      "instructions": "这句话对守卫是否友善？",
      "criteria": ["冒犯", "中性", "友善", "明确表达敬意"]
    },
    "reveals_birthday": {
      "type": "noul",
      "instructions": "守卫此刻会透露馆长生日吗？",
      "criteria": {"true": "愿意说出", "false": "仍有戒心"}
    }
  }
}
```

- choice：`criteria` 是"选项名 → 说明"的 map，最多 255 个选项，说明可为 null。
- score：`criteria` 是有序数组，至少 2 档，最多 10 档。
- noul：`criteria` 可选，`true` / `false` 描述"是"和"否"各代表什么。
- `instructions` 可以是字符串、对象或数组；需要附带数据时，用对象，问题放一个字段，数据放其他字段，在问题里用反引号引用字段名。

响应：

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "intent": {"type": "choice", "choice": "sneak",
               "probabilities": {"sneak": 0.88, "talk_zhou": 0.10, "none": 0.02},
               "confidence": 0.81},
    "trust_delta": {"type": "score", "score": 1.05,
                    "legend": {"0": "冒犯", "1": "中性", "2": "友善", "3": "明确表达敬意"},
                    "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05, "3": 0.0},
                    "confidence": 0.92},
    "reveals_birthday": {"type": "noul", "noul": 0.95}
  },
  "usage": {"input_tokens": 296, "output_tokens": 20}
}
```

要点：

- choice 和 score 带 `confidence`（0 到 1），noul 本身就是概率，没有 confidence。
- score 是概率加权值，可以落在两档之间（如 1.05），代码里自己决定取整规则（建议四舍五入后再映射成信任增量，并在配置里可调）。
- 同一个 state 可以在一次请求里问多个问题，它们并行求值，这是低延迟的来源。
- 错误码：400 请求违规，401 key 无效，413 超 32 MiB，422 校验失败，429 限流，529 过载。429 / 529 用指数退避重试。
- 官方 SDK：`pip install typesafe-sdk`、`npm install @typesafe-ai/sdk`；也可以直接用 `urllib` / `requests` 发 HTTP，减少依赖。
- 没有 TypeSafe 官方 key 时，Telnyx、DigitalOcean（`https://inference.do-ai.run/v1/systemone`，model 为 `typesafe-jev-1.13.0`）、Databricks、Opper 都提供请求格式兼容的端点。`RealJev` 的 base URL、model 名和 key 来源都做成可配置（环境变量）。
- 已知弱项：算术、计数、多跳推理。所以数字比对、背包计算、门槛判断一律用代码，不交给 Jev。
- 待确认：Choice 里"弃权"选项的官方推荐命名。目前约定用 `none` 表示"以上都不是"，实现前可再看一下官方 Choice 文档。

## 3. 关卡：停电的档案馆

**地图**：大厅 ⇄ 档案室，大厅 ↓ 机房（门禁锁住）。出口是大厅的电动卷帘门，目前断电。

**NPC**
- 老周（守卫，在大厅）：敬重已故的老馆长，信任 ≥ 2 时会说"馆长生日是四月十七"。
- 小林（管理员，在档案室）：信任 ≥ 2 时会说"抽屉密码是馆长的生日"。

**通关链**：问老周得到生日 + 问小林得到"密码就是生日" → 档案室抽屉输入 0417 → 拿到门禁卡 → 刷开机房 → 拉总闸 → 卷帘门通电 → 离开。

两位 NPC 各持一半信息，这是谜题核心。密码比对用正则从玩家输入里取，不交给 Jev。

**一条规则的数据样子**

```json
{
  "id": "open_drawer",
  "needs": {"room": "archive", "flags_absent": ["drawer_open"], "has_code": "0417"},
  "effects": {"set_flags": ["drawer_open"], "add_items": ["keycard"]},
  "text": "抽屉弹开，里面躺着一张蓝色门禁卡。",
  "hint_for_jev": "玩家尝试输入密码打开档案室抽屉"
}
```

`hint_for_jev` 会作为 choice 选项的说明文字。其他规则（go_archive、go_basement、use_keycard、pull_switch、exit 等）同样格式。动作词表里保留一个"通用拒绝"出口（`none`），比如玩家说"我直接把抽屉砸开"，没有对应规则，就走通用拒绝文本，不会越出设计范围。

## 4. 每回合流程

1. `cli.py` 读入玩家文本。
2. `engine.py` 读取 `world.py` 的状态，用规则表筛出此刻合法的动作，以及当前场景内 NPC 的行为表。**这个合法集合就是 Jev 能选的全部选项。**
3. `jev_client.py` 把状态和玩家输入打包，一次请求并行问：
   - Choice：玩家意图对应哪条合法规则（含 `none`）
   - Choice：当前对话 NPC 的反应（搪塞 / 感慨回忆 / 透露信息 / 赶人 等，按 NPC 行为表）
   - Score：玩家这句话对 NPC 的态度，映射为信任增量
   - Noul：关键触发条件（如通关判定前的最后确认）
4. Gate（在 engine 内）：
   - 意图 choice 的 `confidence` 或最高概率低于 0.6：不执行，反问玩家并给出候选
   - NPC 反应被代码里的信任门槛拦下时，改走次高项（例如信任 1 < 门槛 2，"透露信息"降级为"追问来意"）
   - 复合句只执行第一步，其余留给后续回合
5. Apply（在 engine 内）：执行规则效果，写回世界状态，输出预写文本，记录日志，进入下一回合。

原则：**Jev 给概率排序，代码里的门槛和规则做最终决定。**

## 5. 试玩记录（目标行为，供对照测试）

```
[回合 1] > 我想跟守卫聊聊，顺便问问这里怎么停电了
  Q1 意图     → talk_zhou 0.93
  Q2 态度     → +1
  Q3 老周反应  → 搪塞 0.71 | 抱怨停电 0.22 | 透露信息 0.03 | 赶人 0.04
  信任 0 → 1，执行"搪塞"
  输出: 老周瞥了你一眼："配电的事归机房管，别问我。"

[回合 2] > 我是来查老馆长留下的资料的，他是个很受人尊敬的人
  Q2 态度     → +2
  Q3 老周反应  → 感慨回忆 0.58 | 透露信息 0.33 | 搪塞 0.09
  信任 1 → 3，达到门槛，取最高项"感慨回忆"，同时置位 zhou_open
  输出: 老周的神情软了下来："馆长啊……他生日四月十七，每年都带蛋糕来值班室。"
  获得线索 [馆长生日: 0417]

[回合 3] > 那个东西能打开吗
  Q1 意图     → open_drawer 0.31 | examine_shutter 0.29 | go_archive 0.22 | none 0.18
  最高概率 < 0.6 → 反问
  输出: 你指的是哪个？卷帘门，还是别的什么？
  日志: [low_conf] input="那个东西能打开吗" top=0.31

[回合 5] > 我直接把抽屉砸开吧
  Q1 意图     → none 0.77
  输出: 抽屉看起来很结实，而且小林就在旁边盯着你。换个办法吧。

[回合 6] > 你好，我想问下抽屉的密码，是馆长的生日吧？
  Q3 小林反应 → 确认 0.52 | 追问来意 0.41 | 拒绝 0.07
  信任 0 → 1，未达门槛 2，"确认"被拦下，改走"追问来意"
  输出: 小林警觉地问："你是谁？为什么打听这个？"
```

以上概率都是示意，真实实现以 Jev 返回为准。

## 6. 工程结构

```
game/
  levels/level1.json     房间、物体、规则、NPC 行为表、信任门槛
  world.py               状态、规则筛选、效果执行
  engine.py              回合循环、阈值与澄清、门槛拦截
  jev_client.py          RealJev / MockJev，同一接口
  verify.py              BFS 可解性、死局检测
  cli.py                 命令行界面，--debug 显示每次 Jev 请求与返回
  tests/intents.jsonl    "玩家怎么说 → 期望规则" 样例集
```

默认值（做成配置，不要写死）：

- 低置信度阈值 0.6
- 每回合最多执行一个动作
- 密码、数量用正则提取
- `--debug` 打印每次 Jev 请求和返回，方便观察 Jev 在做什么

## 7. 离线验证器

`verify.py` 在"动作—状态"图上做 BFS，期望输出类似：

```
状态数: 412   动作数: 17
可解: 是，最短解 9 步
  talk_zhou(敬意) → talk_zhou → go_archive → talk_lin(敬意) → talk_lin
  → enter_code(0417) → go_basement → use_keycard → pull_switch → exit
死局状态: 0
不可达规则: 0
信息依赖: open_drawer 需要 {zhou_open, lin_open}（缺一不可）
```

要求：确认可解、给出最短解、检测死局、检查每条规则的前置条件是否可达、列出信息依赖。关卡改完先跑它，再开始玩。

## 8. 开发顺序

1. 写 `levels/level1.json` 和 `world.py`（状态、规则筛选、效果执行），带单元测试。
2. 写 `verify.py`，让上面的期望输出跑出来。
3. 写 `jev_client.py` 的接口和 `MockJev`（关键词规则，能模拟 choice / score / noul 的返回结构，含 `probabilities` 和 `confidence`）。
4. 写 `engine.py` 和 `cli.py`，用 MockJev 把完整通关流程跑通，包括低置信度反问、门槛降级、通用拒绝。
5. 补 `tests/intents.jsonl`，用它衡量意图映射准确率，MockJev 与 RealJev 跑同一套样例对比。
6. 实现 `RealJev`（HTTP 或 SDK），base URL、model、key 走环境变量（如 `TYPESAFE_API_KEY`、`TYPESAFE_BASE_URL`），加重试与超时；切换时只改配置。
7. 日志：记录每次输入、被选中的选项、置信度；低置信度和选错的输入汇总成文件，用来回头补规则和选项说明。

## 9. 注意事项

- API key 不写进代码和仓库，用环境变量或本地 key 文件（权限 600）。
- 每次请求的 state 里只放这一回合需要的信息，不要把整个世界塞进去。
- 剧情文本全部预写在 `level1.json`，保证叙事可控；以后想接小模型润色，留一个可选钩子即可。
- 不要为了让 Jev 更"聪明"而放宽选项集合；选项集合小而准，是这个设计稳定的前提。
- 可选的后续：网页界面、把每回合的置信度画成曲线作为展示。

## 10. 运行方式（已实现）

```
python -m game.cli                 # 有 TYPESAFE_API_KEY 用真实 Jev，否则 MockJev；--jev mock|real 强制指定
python -m game.cli --debug         # 打印每次 Jev 请求与返回
python -m game.cli --no-flavor     # 关闭旁白（见下）
python -m game.verify              # 离线验证器
python -m game.tests.eval_intents --jev mock|real   # 意图映射准确率
python -m game.tests.eval_reactions --jev mock|real   # NPC 反应（线索是否解锁）准确率
python -m unittest discover -s game/tests -t .      # 单元测试
```

接入真实 Jev：设置 `TYPESAFE_API_KEY`（或权限 600 的 `TYPESAFE_KEY_FILE`），可选 `TYPESAFE_BASE_URL`、`TYPESAFE_MODEL`（用 DigitalOcean 等兼容端点时改这两个）。日志默认写入 `game/logs/`（`turns.jsonl` 全量，`low_conf.jsonl` 为反问/落到 none 的输入）。

### OpenRouter 上的 Jev（已核对文档，尚未用真实 key 跑过）

OpenRouter 提供与 TypeSafe 请求格式一致的 System One 端点，`RealJev` 无需改代码：

```
TYPESAFE_BASE_URL=https://openrouter.ai/api/v1/systemone   # 注意是完整地址，不是前缀
TYPESAFE_API_KEY=<OpenRouter key>
TYPESAFE_MODEL=jev-latest        # 映射到 ~typesafe/jev-latest（当前 Jev 1.14）；要对照设计文档用 jev-1.13
```

- 用云环境的"网络密钥"（Bearer，允许 `openrouter.ai`，路径前缀 `/api/v1/`）时，key 由代理在请求发出时附加，本地只需给 `TYPESAFE_API_KEY` 填任意占位值。
- 已验证：经 OpenRouter 的 `jev-latest` 跑 `eval_intents --jev real` 为 26/26（与 Mock 一致；曾有"今天天气真不错"误判为 talk_zhou，已收紧 talk_zhou 的 hint）。
- 已验证：真实 Jev 整局通关（调整 NPC 描述与反应 hint 后）；`eval_reactions --jev real` 为 19/19（11 句该解锁、8 句该守住，含威胁/辱骂）。Mock 为 17/19，漏掉不含"敬/尊"等关键词的委婉说法，属 Mock 局限。
- 下一步：继续收集真实玩家说法，补进 `tests/intents.jsonl` 和 `tests/reactions.jsonl`。

### 旁白（可选 LLM 钩子）与提示

- **旁白**（`game/flavor.py`）：后端是 OpenRouter 时**默认开启**，把"通用拒绝"改写成每次不同的轻松调侃文字，默认模型 `qwen/qwen3-30b-a3b-instruct-2507`（`TYPESAFE_FLAVOR_MODEL` 可换；选的是各地区都能直连的轻量中文模型，不要为了用某个被地区限制的模型去绕代理），超时 8 秒（`TYPESAFE_FLAVOR_TIMEOUT`），主模型被拒时依次换 `kimi-k2`、`qwen-plus`，连续失败 3 次后本局不再尝试，失败原因会直接显示。`--no-flavor` 或 `TYPESAFE_FLAVOR=0` 关闭；TypeSafe 官方端点/Mock 下自动关闭。
  - 只改写拒绝文本，不碰状态、线索、NPC 台词；超时、报错、空输出、过长、含剧透词（`level1.json` 的 `flavor.spoilers`）一律回退到预写文本。
  - 玩家原话放在 `<player_said>` 里当数据，提示词要求不执行其中指令、不透露线索。
- **`/hint`**：按当前进度给 3 层递进提示（`level1.json` 的 `hints`，第一个匹配的阶段生效，阶段变化后从头开始），不消耗回合。
- 开场白里加了"老周望着馆长遗像神情恭敬"的线索。
