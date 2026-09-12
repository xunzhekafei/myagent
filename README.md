# myagent —— 你的第一个 Claude Agent

一个极简的 Python 智能体：把你的问题交给 Claude，它会在需要时调用
你定义的工具（加法、查时间），直到给出最终答案。

## 目录结构

| 文件                | 作用                                        |
| ------------------- | ------------------------------------------- |
| `agent.py`          | Agent 本体：系统提示词 + 工具定义 + 主循环   |
| `requirements.txt`  | 依赖清单（Anthropic SDK）                   |
| `skills/`           | 技能目录：每个子目录一个 `SKILL.md`（按需加载的领域知识） |

## 快速开始

1. **安装 Python 3.10+**（<https://www.python.org/downloads/>，安装时勾选 "Add to PATH"）

2. **安装依赖**

   ```bash
   pip install -r requirements.txt
   ```

3. **配置 API Key**（用 DeepSeek 的密钥即可，不需要 Anthropic 密钥）

   在终端窗口设置（每次新开窗口都要重新设置一次）：

   PowerShell：

   ```powershell
   $env:ANTHROPIC_API_KEY = "sk-你的DeepSeek密钥"
   ```

   CMD：

   ```cmd
   set ANTHROPIC_API_KEY=sk-你的DeepSeek密钥
   ```

   Git Bash：

   ```bash
   export ANTHROPIC_API_KEY="sk-你的DeepSeek密钥"
   ```

   > 想让密钥永久生效（不用每次重设）：在 PowerShell 里运行
   > `[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-你的DeepSeek密钥", "User")`，
   > 然后重新打开一个终端窗口。

4. **运行**

   ```bash
   python agent.py
   ```

   进入交互模式后直接输入问题（输入 `exit` 或 `quit` 退出）：

   ```
   你 > 123 + 456 等于多少？
   123 + 456 = **579**。
   你 > 现在几点？
   现在是 **2026年8月26日 22:19:06**。
   你 > exit
   再见！
   ```

   也可以单次提问、问完即止：

   ```bash
   python agent.py "123 + 456 等于多少？现在几点？"
   ```

## 用的什么接口

本脚本默认连接 **DeepSeek 的 Anthropic 兼容接口**（`https://api.deepseek.com/anthropic`），
所以你只需要一个 DeepSeek 的 API 密钥就能跑，不需要 Anthropic 密钥。

| 代码里的模型名 | 实际映射到 | 说明 |
| -------------- | ---------- | ---- |
| `claude-opus-5` | `deepseek-v4-pro` | 最强，本脚本默认 |
| `claude-haiku-4-5` / `claude-sonnet-5` | `deepseek-v4-flash` | 更快更便宜 |

> 注意：使用 DeepSeek 接口时，你的问题和代码会发送到 DeepSeek 的服务器。
> 如果以后想改用 Anthropic 官方接口，把 [agent.py](agent.py) 里的 `base_url` 那一行删掉，
> 再把密钥换成 `sk-ant-` 开头的 Anthropic 密钥即可，其他代码不用改。

## 它是怎么工作的

```
你的问题 ──> Claude 思考 ──> 需要工具？ ──是──> [PreToolUse hooks] ──> 执行工具 ──> [PostToolUse hooks] ──> 结果回传
     ^                                                                                                        |
     └──────────────────────── 循环，直到 Claude 不再需要工具 ──────────────────────────────────────────────┘
                                    |
                                    v（Stop hooks）
                              打印最终答案
```

- **系统提示词**（`SYSTEM_PROMPT`）：告诉 agent 是什么角色、什么时候该用工具。
- **工具**（`@beta_tool` 装饰的函数）：主 agent 目前有 29 个——基础 7 个 + `todo`/`load_skill`/`compact`/`task` + task 系统 6 个 + cron 3 个 + 团队 7 个 + `connect_mcp` + `run_workflow`。队友有自己的一套工具（11 个：工作区 5 个 + 任务 4 个 + `send_message`/`submit_plan`）。连接 MCP server 后，它的工具会动态加入主 agent 的工具池（不计入上面的固定数量）。
  加新工具 = 写一个 `@beta_tool` 函数 + 在 `TOOL_OBJECTS` 列表里加一行（schema 由函数签名自动生成）。
  注意：工具函数**必须返回字符串**（DeepSeek 兼容接口的要求，返回数字会报 400，用 `str()` 转换）。
  文件工具会把路径限制在项目目录内（`_safe_path`），防止 agent 读写工作目录之外的文件。
- **主循环**：手动循环——发请求 → 检查 `tool_use` → 逐个执行 → 结果回传，直到模型不再需要工具。
  循环本身不写任何扩展逻辑，只触发 hook 事件（见下）。
- **主循环**（`tool_runner`）：SDK 自动处理「请求 → 执行工具 → 回传结果」，不用自己写循环。

## Hooks（挂在循环上）

循环不直接调用任何检查函数，只触发事件，扩展全部挂在外面（参考 learn-claude-code s04）：

| 事件 | 触发时机 | 返回值的作用 |
| ---- | ---- | ---- |
| `UserPromptSubmit` | 用户输入后、进入 LLM 前 | 非 None 会注入提示词 |
| `PreToolUse` | 工具执行前 | 非 None 会阻止执行，并作为错误结果告诉模型 |
| `PostToolUse` | 工具执行后 | 不参与控制流（可做自动 git add 等副作用） |
| `Stop` | 循环即将退出时 | 非 None 会强制再跑一轮 |

想加扩展 = 写一个回调函数 + `register_hook("PreToolUse", 你的函数)` 一行。
当前注册的 hooks：`permission_hook`（权限三道闸门）、`log_hook`（打印 [HOOK] 日志）、`summary_hook`（退出前统计工具调用次数）。

## 任务清单（TodoWrite）

参考 learn-claude-code s05：复杂任务先列步骤再执行，防止 agent 做着做着偏离目标。

- `todo` 工具：整体替换式任务清单，状态 `pending`（[ ]）/ `in_progress`（[>]）/ `completed`（[x]），
  同时打印到终端让你看到计划。校验：最多 20 项、content 非空、同时只能有一项 in_progress。
- **Reminder**：连续 3 轮工具调用没用 `todo` 时，自动提醒模型更新任务清单（防跑偏）。
- todo 只增加「规划能力」，不增加「执行能力」——干活还是靠其他工具。

## 子任务委派（Subagent）

参考 learn-claude-code s06：大任务拆给子 agent，每个子 agent 用**全新的 `messages[]`** 独立运行，
只把最终文本返回父 agent——中间的工具调用和结果不进入父上下文，省上下文、互不污染。

- `task` 工具：父 agent 用它委派子任务（`prompt` 要足够明确：做什么、看哪些文件、输出什么）。
- **隔离的是消息，不是进程**：父子共享 `WORKDIR` 和同一套权限 hooks（子 agent 的危险操作同样要你批准）。
- **只允许一层委派**：子 agent 的工具表不含 `task`（`SUB_TOOLS`）。
- 子任务期间 `todo` 清单独立，结束自动还原；输出以 `[子任务]` 前缀和 `[子任务开始/结束]` 标记区分。

## 技能（Skill Loading）

参考 learn-claude-code s07：领域知识做成技能文件按需加载，不塞爆 system prompt。

| 内容 | 进入模型的位置 | 何时加入 |
| ---- | ---- | ---- |
| 技能名称和描述 | system prompt（「可用技能」列表） | 启动时扫描 `skills/*/SKILL.md` |
| 完整 `SKILL.md` | `load_skill` 的工具结果 | 模型判断任务匹配时调用 |

加技能 = 新建 `skills/<名称>/SKILL.md`，开头写 `--- name: ... / description: ... ---` frontmatter 即可。
`load_skill` 的 `name` 只在启动时建立的注册表里查询，绝不当作文件路径（安全）。
内置示例：`code-review`（代码审查清单）、`deepseek-api`（本项目接口注意事项）。

## 上下文压缩（Context Compact）

参考 learn-claude-code s08：每次调用模型前运行 `COMPACTOR.prepare()` 四步压缩管线，
按「信息损失从低到高、成本从低到高」的顺序，只在必要时动用最贵的模型摘要：

| 步骤 | 触发条件 | 做什么 | 成本 |
| ---- | ---- | ---- | ---- |
| 1. `tool_result_budget` | 本轮工具结果总大小 > 200K 字符 | 大结果（>30K）转存到 `.task_outputs/tool-results/`，只留路径 + 预览 | 0（落盘） |
| 2. `snip_compact` | 消息 > 50 条 | 历史归档到 `.transcripts/`，保留首尾 + 归档标记（保护 tool_use↔tool_result 配对） | 0 |
| 3. `micro_compact` | 仍超 50K 字符 | 更早的已读长结果替换成路径引用（保留最近 3 条） | 0 |
| 4. `compact_history` | 仍超限 | 让模型生成事实摘要，替换整段历史（只留目标/决定/剩余/约束） | 1 次模型调用 |

- `compact` 工具：模型完成大阶段后可主动请求压缩（本批工具先执行完，再总结已闭合的回合）。
- 补救：API 返回 `prompt_too_long` 时 `reactive_compact` 压缩一次再重试。
- 每个被替换/归档的块都留有**可恢复的文件路径**，需要时能用 read_file 找回。

## 跨会话记忆（Memory）

参考 learn-claude-code s09：让重要信息跨会话保留——s08 的压缩会丢细节，记忆负责把「以后还用得到」的留下来。

| 环节 | 做什么 | 时机 |
| ---- | ---- | ---- |
| 存储 | `.memory/<名称>.md`（frontmatter: name/description/type）+ `MEMORY.md` 索引 | 提取通过后落盘 |
| 召回 | 模型从记忆目录挑 ≤5 条相关 → 读正文（≤20K 字符）注入 system 作为**背景知识** | 每次用户请求（模型失败时降级关键词匹配） |
| 提取 | 模型从刚结束的对话里找「以后的新会话还用得到」的信息，过滤后保存 | 每回合结束（有廉价预筛：纯问句/闲聊不触发，省模型调用） |
| 整理 | 记忆 ≥10 条时模型合并重复/过期内容（替换前存快照，失败自动还原） | 自动 |

`type` 分四类：`user`（用户偏好）/ `feedback`（会反复适用的反馈）/ `project`（稳定项目事实）/ `reference`（外部资料）。
带「本次会话/暂时」等临时词、或 `scope=current_task` 的候选不会落盘；召回内容只是背景知识，与当前请求冲突时以当前请求为准。
召回和提取都有**廉价预筛**（中文二元组切词 + 关键词/长度判断），无关对话不会白付模型调用。
> 记忆是**选择性存储**，不是 transcript 备份，也不替代 s08 的上下文压缩。

## 任务系统（Task System）

参考 learn-claude-code s10：大目标拆成可协调的任务图并**持久化到 `.tasks/`**，程序退出也能恢复进度。
与 `todo`（s05 会话内执行清单）的区别：每个任务有独立 ID、`blockedBy` 依赖图、`owner` 分工。

| 工具 | 作用 |
| ---- | ---- |
| `create_task(subject, description)` | 创建任务（pending），返回运行时 ID |
| `update_task(task_id, addBlockedBy)` | 加依赖边（必须 pending 且无人认领；拒绝自依赖和环） |
| `list_tasks()` / `get_task(task_id)` | 列表摘要 / 完整 JSON（跨会话恢复用） |
| `claim_task(task_id, owner)` | 认领：pending → in_progress（依赖未全部 completed 则拒绝） |
| `complete_task(task_id, owner)` | 完成：in_progress → completed，并解锁下游任务 |

```
pending ──claim_task──> in_progress ──complete_task──> completed
```

两阶段构建：先 `create_task` 建好所有节点，再用返回的 ID 调 `update_task` 加依赖
（一次回复里的多个工具调用是并行的，无法引用彼此还没生成的 ID）。

## 后台任务（Background Tasks）

参考 learn-claude-code s11：耗时长命令（装依赖、跑完整测试）放后台线程执行，主循环不阻塞。

- `bash` 增加 `run_in_background` 参数（默认 False）——**模型显式请求才后台**，不按关键词猜。
- 后台启动立刻返回 `bg_id`（如 `bg_0001`），agent 继续处理其他工作。
- 每轮开始前 `_inject_background_results()` 收集已完成任务，以 `<task_notification>` 注入对话。
- 通知不复用原 `tool_use_id`（原调用已用占位结果回复；一个 tool_use 只对应一个 tool_result）。
- 后台默认超时放宽到 600 秒（`timeout` 参数可覆盖）。
- 注意：**后台结果不会主动唤醒 agent**——若它在你提问之间结束回合，结果会在你发下一条消息时送达。

## 定时任务（Cron Scheduler）

参考 learn-claude-code s12：让 agent 在指定时间自动跑一轮（本地时间），不必到点再手动发消息。

| 工具 | 作用 |
| ---- | ---- |
| `schedule_cron(cron, prompt, recurring, durable)` | 注册定时任务；5 段 cron（分 时 日 月 星期），支持 `*`/`*/N`/`N`/`N-M`/`N,M` |
| `list_crons()` | 列出所有定时任务 |
| `cancel_cron(cron_id)` | 取消定时任务 |

- **两条守护线程**（只在运行 CLI 时启动）：调度线程每秒查时间、到点入队（先持久化状态防重复投递）；队列处理线程在 agent 空闲时把 `[Scheduled] 任务` 作为用户消息送达。
- 定时回合与用户回合用 `agent_lock` 互斥；定时回合里需要交互确认的操作会被**自动拒绝**（不与主终端抢输入）。
- `durable=True` 写入 `.scheduled_tasks.json`（临时文件 + 原子替换），重启恢复任务定义，**不补跑**停机期间错过的时刻。
- 一次性任务（`recurring=False`）送达后自动删除；投递是「至少一次」语义。

## 团队协作（Agent Teams）

参考 learn-claude-code s13：一个 **Lead**（主 agent）+ 若干**持久队友**并行干活。

| 组件 | 机制 |
| ---- | ---- |
| 队友 | 独立消息历史 + 独立线程，`WORK ↔ IDLE` 循环；空闲时能**自动认领**任务板上的 ready task |
| 消息总线 | `.mailboxes/<名字>.jsonl` 文件收件箱（读取即删除），`Condition` 唤醒；通信不进入别人的上下文 |
| 事件送达 | 队友完成时发 `result` + `idle_notification` 两个事件；Lead 的收件箱由运行时消费后**自动唤醒新一轮**（不用反复轮询） |
| 任务板 | 复用 s10 的 `.tasks/`；认领在锁内原子完成（并发只有一个成功）；队友同时只能持有一个任务 |
| 工作目录 | 任务可绑定独立目录（`.worktrees/<名字>/`，教学简化：普通目录，非 git worktree、非沙箱）；队友的文件/命令工具都在自己的任务目录里执行；**没认领任务的队友不能用工作区工具** |
| 关机协议 | `request_shutdown` → 队友完成当前步骤 → `shutdown_response` → 退出（带 request_id 的类型化消息，防止误处理） |
| 计划审批 | `require_plan=True` 启动的队友必须先 `submit_plan`，获批前**不能**改文件/跑命令（计划与任务身份绑定，认领变化会使旧审批失效） |

Lead 的团队纪律写在系统提示词里：**先向用户提分工方案、等确认后才允许 `spawn_teammate`**。
队友回合里需要交互确认的操作会被自动放行（控制靠计划闸门 + 任务目录约束，硬拒绝规则仍然生效）。

## MCP 外部工具（Model Context Protocol）

参考 learn-claude-code s14：把「提供工具的服务」和「使用工具的 agent」解耦——连接 server、发现工具、加入工具池。

- `connect_mcp(name)`：连接 server（本章内置进程内模拟 server：`docs`、`deploy`）。
- 连接后，发现的工具以 **`mcp__{server}__{tool}`** 命名，**从下一轮起**出现在工具池里
  （名字经过规范化 + 冲突/64 字符长度检查，`docs.one/get.version` 不会和 `docs_one/get_version` 混淆）。
- 每轮请求前由 `_assemble_tool_pool()` 重新组装：基础工具 + 所有已连接 MCP 工具。
- **权限由宿主侧策略 `MCP_HOST_POLICY` 决定**（`allow`/`confirm`）——server 自己声称的
  `readOnlyHint` 不作为授权依据；未配置的外部工具默认要用户确认。
- 参数错误留在工具边界内（返回 `MCP 错误：...` 的 tool_result，让模型下一轮修正，不打断循环）。
- 说明：本章的 server 是进程内模拟（展示 tools/list 与 tools/call 的协议边界），
  真实实现会换成 stdio/HTTP transport；连接状态不跨进程持久。

## 集成运行时（所有机制在一个循环里）

参考 learn-claude-code s15：不引入新机制，而是把上面所有章节接到**同一个 `while True`** 上。
每轮请求的顺序：

```
用户输入 → UserPromptSubmit hooks
  → cron 到点任务注入 / 后台任务完成通知注入
  → 上下文压缩管线（大结果落盘 → 剪消息 → 旧结果替换 → 必要时摘要）
  → system prompt 组装（身份 + 技能目录 + 已连接 MCP server + 相关记忆）
  → 调模型（429/529 由 SDK 自动退避重试；被长度截断则提高 max_tokens 续写；
             上下文超限则 reactive compact 后重试）
  → 有 tool_use？
      否 → Stop hooks（统计/审计）→ 返回
      是 → PreToolUse hooks（权限三道闸门）→ 工具池分发（内置 / MCP / 后台）
           → PostToolUse hooks → tool_result 回 messages → 下一轮
```

| 位置 | 组件 | 作用 |
| ---- | ---- | ---- |
| 输入后 | `UserPromptSubmit` hooks | 记录/注入（当前未注册示例） |
| 请求前 | cron 队列 / 后台通知 | 到点任务与完成结果注入 messages |
| 请求前 | 压缩管线 | 控制上下文预算，被替换的内容都有可恢复路径 |
| 请求前 | system prompt 组装 | 技能目录按需加载 + MCP 状态可见 + 相关记忆作背景 |
| 调模型 | 恢复层 | `max_tokens` 截断升级续写；`prompt_too_long` 补救压缩 |
| 工具前 | `PreToolUse` + 权限 | 硬拒绝 / 规则询问 / MCP 宿主策略 |
| 工具分发 | `_assemble_tool_pool` | 内置工具 + 动态 MCP 工具 |
| 工具执行 | 后台调度 | `run_in_background=true` 的 bash 走 daemon 线程 |
| 工具后 | `PostToolUse` hooks | 日志等后处理 |
| 无 tool_use | `Stop` hooks | 收尾统计 |

Lead、一次性 subagent、队友的工具调用**都先经过 `PreToolUse`**（权限在同一个挂点上）。
只有前台用户轮次能弹交互确认；定时回合和队友回合会直接拒绝/放行（见对应章节）。

## 工作流运行时（Workflow Runtime）

参考 learn-claude-code s16：有些任务重复固定流程（代码审查=多维度审计→逐条验证→汇总），
编排早已知道，不必靠模型一轮轮现凑——**计划写在代码里，一次 tool_use 跑完整套编排**。

- **编排 = 宿主注册的可信脚本**（`WORKFLOWS` registry）；模型只能给 `name` / `args` / `resume_from_run_id`，
  **不能提交代码**。未知名字或参数错误只返回错误工具结果，不打断主循环。
- **编排原语**：`agent(prompt, schema, label, phase)` 派子 agent（独立上下文、无工具）；
  `parallel(thunks)` 等齐屏障；`pipeline(items, *stages)` 每个 item 独立走完各阶段（不等齐）；
  `phase()`/`log()` 进度。子 agent 带 `schema` 时强制结构化输出：解析 + 校验，不合法重试一次，
  仍不合法就报错（**子 agent 的输出也不能全信**）。
- **断点续跑**：每个 `agent()` 结果写进 `.runtime/<runId>.journal.jsonl`，缓存键是
  **调用内容的稳定哈希**（不是完成顺序——并发顺序不确定，用序号会缓存错位）；
  `resume_from_run_id` 续跑时未改动的步骤直接命中缓存（**实测续跑 agents=0，零重复调用**）。
- **产物**：`.runtime/` 下有快照 `.json`、journal `.jsonl`、输出 `.output.json`、排他创建的 `.lock`。
- 内置示例 `review-changes`：并行审查代码变更（正确性/安全性两个维度各自「审计→对抗性验证」），
  实测在 SQL 注入片段上跑出 6 个子 agent、确认 4 个真实问题。
- 说明：教程用 asyncio，本项目是同步代码，并行原语用线程实现；嵌套子工作流未实现。

## 目标循环（Goal Loop）

参考 learn-claude-code s17：模型不再调用工具，只代表「这一轮想停」——**目标是否达成由独立判断器决定**。

- `/goal <完成条件>`：设置会话级目标并立即开工；`/goal` 查看状态（判断次数/续轮数/耗时/tokens/最近理由）；
  `/goal clear`（或 stop/off/reset/none/cancel）清除。
- 实现方式是注册在 **Stop 事件**上的 hook：每轮结束（模型不再要工具）时，判断器读对话记录判断：
  - `ok=false` → 把理由追加回 messages，**自动续轮**（不需要用户再说「继续」）
  - `ok=true` → 放行退出，标记完成；`impossible=true` → 保留目标、如实报告，不伪装成完成
  - 有后台任务在跑 → 先等待（defer），关键结果没到就不判断
  - 判断器调用失败 → 停止自动续轮、保留目标、把错误交给用户
- **判断器没有工具**，只能根据对话里已经出现的结果判断（工具输出、退出码、文件内容），
  所以主模型的 system prompt 要求：验证命令和结果必须写进对话。
- 好的完成条件 = 结束状态 + 验证方式 + 限制条件（如「直到 pytest 退出码为 0，且不改动其它测试文件」）。
- 两道通用出口：主循环轮数上限 + 连续阻止上限（`GOAL_MAX_BLOCKS`）；到上限交还控制权，**目标保留**。

## 会话持久化

退出再启动，接着上次聊——主会话存到 `.sessions/latest.json`（临时文件 + 原子替换）。

- 启动时自动恢复并提示条数；`/clear` 清空会话（**不影响** `.memory/` 长期记忆）。
- 序列化处理了 SDK 对象：assistant 消息里的 `text`/`thinking`/`tool_use` 块用 `model_dump` 转 dict，
  恢复时 dict 本来就是合法请求参数（thinking 的 signature 原样保留）。
- 恢复时会清理**半截回合**：崩溃点可能留下没有对应 `tool_result` 的 `tool_use`，直接发会 400——自动丢弃。
- 存档损坏会被忽略并从新会话开始，不阻塞启动。

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest               # 离线测试（74 个，不需要 API key，约 1 秒）
python -m pytest -m slow       # 真实 API 冒烟测试（会消耗 token）
```

| 文件 | 覆盖 |
| ---- | ---- |
| `tests/test_offline_core.py` | cron 表达式校验/匹配、记忆预筛（含中文二元组切词）、权限三道闸门、MCP 命名与错误兜底、技能加载 |
| `tests/test_tasks.py` | 原子认领、依赖解锁、owner 互斥、环检测、worktree 绑定（目录丢失时失败不回落） |
| `tests/test_workflow.py` | JSON schema 校验、稳定调用键、journal 读写、pipeline 编排、续跑缓存命中（零重复调用） |
| `tests/test_runtime.py` | 消息总线（破坏性读/超时）、后台任务生命周期、压缩管线、cron 调度状态机、目标循环四个分支 |
| `tests/test_session.py` | 序列化往返（SDK 对象/dict/str）、原子保存、损坏容错、半截回合清理、`/clear` |
| `tests/test_smoke_api.py` | （slow）真实工具轮 + 独立判断器 |

隔离方式：`tests/conftest.py` 的 `iso` fixture 把 `WORKDIR` 和所有产物目录指到临时目录，
并清空模块级全局——测试之间互不影响，也不会碰真实项目文件。

## 权限控制（三道闸门）

参考 learn-claude-code s03：有副作用的工具执行前，会依次经过三道闸门。
现在它是注册在 `PreToolUse` 事件上的 `permission_hook`（[agent.py](agent.py)）：

| 闸门 | 作用 | 例子 |
| ---- | ---- | ---- |
| 1. 硬拒绝 | 永远禁止，不询问 | `rm -rf /`、`sudo`、`shutdown`、**删除/覆盖项目核心文件**（`agent.py`、`requirements.txt` 等，见 `PROTECTED_FILES`） |
| 2. 规则匹配 | 命中规则的操作进入闸门 3 | 删除/下载/`pip`/`git` 命令、写入修改普通文件 |
| 3. 用户审批 | 暂停等你在终端输入 y/n（与主循环**共用同一个输入队列**，避免两个消费者抢 stdin） | 每次有副作用的操作 |

只读操作（`add`、`get_time`、`read_file`、`glob`）和普通命令（`dir`、`python ...`）直接放行。
规则写在 `PERMISSION_RULES` 里，想调整哪些操作需要确认，改这个列表即可。
> 这是教学级的权限门，真正的安全边界需要容器/沙箱隔离。

## 下一步可以做什么

- **加自己的工具**：在 `agent.py` 里写一个 `@beta_tool` 函数即可——读文件、查天气 API、发邮件都行。
- **对话记忆**：把多轮对话历史存成文件，下次启动接着聊（现在还只在内存里）。
- **换模型**：把 `MODEL` 改成 `claude-haiku-4-5`（映射到 deepseek-v4-flash，更快更便宜）。
- **流式输出**：把 `tool_runner` 换成流式模式，让文字边生成边显示。
