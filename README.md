# myagent —— 从零手写的 Claude Agent

[![tests](https://github.com/xunzhekafei/myagent/actions/workflows/tests.yml/badge.svg)](https://github.com/xunzhekafei/myagent/actions/workflows/tests.yml)

一个**从零构建的 Agent 运行时**（单文件 `agent.py`，32 个工具），外加一个跑在它上面的真实应用：
**技术岗模拟面试官**（AI 方向 + 前端方向，题库 8990 道）。

运行时的框架按 [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 教程的路径搭建，
它的教学内容这里不再重复——机制原理请看原仓库，本 README 只讲这个项目实际做成了什么。
模型通过 Anthropic 官方 SDK 调用，接口指向 **DeepSeek 的 Anthropic 兼容端点**，只需一个 DeepSeek 密钥。

> 📋 **设计取舍、踩坑日志与详细进度见 [documents/PROGRESS.md](documents/PROGRESS.md)**

## 目录结构

| 文件                | 作用                                        |
| ------------------- | ------------------------------------------- |
| `agent.py`          | Agent 本体：循环 + 工具 + hooks + 各机制     |
| `requirements.txt`  | 依赖清单（Anthropic SDK）                   |
| `skills/`           | 技能目录：每个子目录一个 `SKILL.md`（按需加载的领域知识） |
| `interview/`        | 面试题库导入脚本与数据（AI 岗 + 前端岗）      |
| `tests/`            | 测试（112 离线 + 2 冒烟）                    |
| `documents/`        | 文档（进度、设计记录）                       |

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

   跑一场模拟面试：

   ```
   你 > /user 你的名字
   你 > 开始面试
   （面试官按技能流程提问，一次一题、追问式深挖）
   你 > 结束面试        → 自动评分 + 存档
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
- **工具**（`@beta_tool` 装饰的函数）：主 agent 目前有 32 个——基础 7 个 + `todo`/`load_skill`/`compact`/`task`
  + task 系统 6 个 + cron 3 个 + 团队 7 个 + `connect_mcp` + `run_workflow`
  + 面试三件套（`search_questions` / `save_interview_record` / `list_interviews`）。
  队友有自己的一套（11 个：工作区 5 个 + 任务 4 个 + `send_message`/`submit_plan`）。
  连接 MCP server 后，它的工具会动态加入主 agent 的工具池（不计入上面的固定数量）。

  加新工具 = 写一个 `@beta_tool` 函数 + 在 `TOOL_OBJECTS` 列表里加一行（schema 由函数签名自动生成）。
  注意：工具函数**必须返回字符串**（DeepSeek 兼容接口的要求，返回数字会报 400，用 `str()` 转换）。
  文件工具会把路径限制在项目目录内（`_safe_path`），防止 agent 读写工作目录之外的文件。
- **主循环**：手动循环——发请求 → 检查 `tool_use` → 逐个执行 → 结果回传，直到模型不再需要工具。
  循环本身不写任何扩展逻辑，只触发 hook 事件，扩展全部挂在外面。

## 运行时的能力

框架按教程路径逐章搭建，下面是这些机制**在这个项目里的实际形态**（不是教学讲解）。
每一节的设计取舍和踩过的坑，见 [documents/PROGRESS.md](documents/PROGRESS.md)。

| 层 | 能力 |
| --- | --- |
| 内核 | 手动 agent 循环、流式输出、四类 hooks（`UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `Stop`）、权限三道闸门 |
| 基础工具 | 读写文件（**支持分页**）、glob、bash（超时/编码/截断保护）、计算、时间 |
| 计划 | `todo` 会话内清单（+ 防跑偏提醒）、`task` 任务图（依赖/认领/解锁，`.tasks/` 持久化） |
| 知识 | 技能按需加载（`skills/*/SKILL.md`）、跨会话记忆（`.memory/`，中文二元组切词召回） |
| 上下文 | 四步压缩管线（大结果落盘 → 剪消息 → 旧结果替换 → 模型摘要），压缩存档可重建对话 |
| 异步 | 后台任务（`run_in_background` + 通知注入）、cron 定时任务（5 段表达式 + 持久化） |
| 协作 | 子代理（一次性委派）、持久队友团队（收件箱总线、原子认领、任务目录、关机/计划协议） |
| 编排 | Workflow 运行时（宿主注册脚本、`agent/parallel/pipeline` 原语、journal 断点续跑）、MCP 外部工具接入 |
| 目标 | `/goal` 目标循环（独立判断器决定是否继续，不伪装完成） |
| 会话 | `/user` 候选人、`/clear` 清空、退出自动存档、启动恢复 |

## 进度

| 日期 | 阶段 | 产出 |
| ---- | ---- | ---- |
| 09-12 | 教程 s01~s17 全部实现 | 工具分发、权限、hooks、todo、子代理、技能、压缩、记忆、任务图、后台任务、cron、团队、MCP、workflow、目标循环 |
| 09-12 | 工程化第一步 | 流式输出；测试套件（66 离线 + 2 冒烟） |
| 09-13 | 会话持久化 | `.sessions/latest.json` 原子存档、SDK 对象序列化、半截回合清理 |
| 09-15 | 面试官（数据层） | 题库导入 + `search_questions` 检索 |
| 09-15 | 面试官（应用层） | 面试官技能、`interview-report` 评分 workflow、`.interviews/` 场次存档 |
| 09-16 | 实战调试与修复 | 六项体验/稳定性修复；发布 GitHub + MIT 许可 |
| 09-17 | 岗位扩展到前端 | 导入 haizlin/fe-interview（5469 题）；修掉检索打分的既有缺陷 |
| 09-17 | 题库扩充到九个来源 | 共 **8990 题**；导入器改为参数化抽取；借鉴 ASu-skills 的追问方法论 |
| 09-17 | 加上 CI | GitHub Actions 双平台（Ubuntu + Windows）跑离线测试，无需配置密钥 |

**当前能做什么**：跑一场完整的技术岗模拟面试——按岗位（AI / 前端）从 8990 道题库选题、
一次一题追问式深挖、结束后并行评分并引用原话作证据、存档供进步追踪。
测试 **112 个离线用例**，约 2 秒跑完，不需要 API key。

## 会话持久化

退出再启动，接着上次聊——主会话存到 `.sessions/latest.json`（临时文件 + 原子替换）。

- 启动时自动恢复并提示条数；`/clear` 清空会话（**不影响** `.memory/` 长期记忆）。
- 序列化处理了 SDK 对象：assistant 消息里的 `text`/`thinking`/`tool_use` 块用 `model_dump` 转 dict，
  恢复时 dict 本来就是合法请求参数（thinking 的 signature 原样保留）。
- 恢复时会清理**半截回合**：崩溃点可能留下没有对应 `tool_result` 的 `tool_use`，直接发会 400——自动丢弃。
- 存档损坏会被忽略并从新会话开始，不阻塞启动。

## 面试题库（模拟面试官 · 数据层）

题库共 **8990 道题**，九个来源（都只导入题目、不含答案——对模拟面试反而好，不泄题）：

| 来源 | 文件 | 题数 | 方向 | 许可 |
| ---- | ---- | ---- | ---- | ---- |
| [haizlin/fe-interview](https://github.com/haizlin/fe-interview) | `fe_questions.json` | 5469 | 前端（按日期归档，带分类标签） | MIT |
| [InterviewForge_GenDS](https://huggingface.co/datasets/Davichick/InterviewForge_GenDS) | `ai_questions.json` | 1647 | AI（英文，带难度/阶段标注） | MIT |
| [guocong-bincai/ai-interview-guide](https://github.com/guocong-bincai/ai-interview-guide) | `zh_questions.json` | 640 | AI（26 个细分考点） | MIT |
| [lf2021/Front-End-Interview](https://github.com/lf2021/Front-End-Interview) | `fe_questions.json` | 374 | 前端（含手撕代码题） | MIT |
| [lengyue1024/BAT_interviews](https://github.com/lengyue1024/BAT_interviews) | `zh_questions.json` `fe_questions.json` | 280 | 机器学习 / Python / 前端 | MIT |
| [FEGuideTeam/FEGuide](https://github.com/FEGuideTeam/FEGuide) | `fe_questions.json` | 245 | 前端 | MIT |
| [bcefghj/ai-agent-interview-guide](https://github.com/bcefghj/ai-agent-interview-guide) | `zh_questions.json` | 181 | AI Agent 开发 | MIT |
| [bcefghj/learn-nanobot](https://github.com/bcefghj/learn-nanobot) | `zh_questions.json` | 134 | AI Agent（10 个板块） | MIT |
| [aceliuchanghong/FAQ_Of_LLM_Interview](https://github.com/aceliuchanghong/FAQ_Of_LLM_Interview) | `zh_questions.json` | 20 | 大模型算法 | MIT |

```bash
# 导入英文题库（huggingface.co 直连不通时用 hf-mirror 镜像）
curl -L -o /tmp/interview_forge.csv \
  https://hf-mirror.com/datasets/Davichick/InterviewForge_GenDS/resolve/main/interview_forge_v3_complete.csv
python interview/import_dataset.py /tmp/interview_forge.csv

# 导入中文与前端题库（浅克隆八个 MIT 仓库并抽取；简历/招聘/个人面经等求职向目录已排除）
python interview/import_github_bank.py
```

`search_questions(query, category, level, role, stage, limit)`：支持中英文关键词、
类别/岗位/阶段过滤。**面试时务必带 `role`**——前端题库有 6000 多道，不传岗位过滤会把 AI 题淹没。

三处容易踩的坑（工具在检索落空时会把该字段的**实际取值**回给模型，便于自纠）：

- `level` **只有英文题库有**（`Level 1/2/3`），中文题库全是空的，传了就一道都搜不到
- 关键词要跟题库语言一致：中文词匹配不到英文题，反之亦然
- **`category` 和 `role` 是绑定的**：`Python` 只挂在 `AI 应用开发` 下、`机器学习` 只挂在
  `大模型算法工程师` 下，跨岗位取会落空

`role` 取值：`AI 应用开发`（AI 岗主力）、`AI/ML Engineer`、`Data Scientist`、`Data Analyst`、
`AI Agent 开发`、`大模型算法工程师`、`前端工程师`。`stage="Stage 3"` 可筛出行为面/团队协作类
题目（导入时按信号词标注）。英文题检索到后由面试官翻译/改写成中文提问。

> 注：`role`、`category`、`stage` 只作为**过滤器**，不参与相关性打分——否则 query 里
> 出现「前端」二字会让该岗位下每一道题都命中（实测 5380 条全中，修掉后 280 条）。

## AI 模拟面试官

在 agent 上长出的一条完整业务线（数据层见上一节「面试题库」）：

| 组件 | 位置 | 作用 |
| ---- | ---- | ---- |
| 面试官技能 | `skills/mock-interviewer/SKILL.md` | 一次一题、追问式深挖、面试中不给反馈、结束触发评分 |
| 评分 workflow | `interview-report` | 完整问答记录（或 `.transcripts/` 存档文件）→ 解析问答对（解析为空会**报错**，不伪造报告）→ 逐题并行评分 → 汇总 |
| 场次存档 | `.interviews/<候选人>/` | `save_interview_record` 存档、`list_interviews` 回顾对比 |
| 题库检索 | `search_questions` | 中英文关键词 + 类别/岗位/阶段过滤 |

技能里固化了四条追问纪律（借鉴自 [ASu-skills](https://github.com/Hisn00w/ASu-skills) 的面试技能）：
**提问前先锁定评分标准**（定了就不因答得好坏而改）、按 **Claim 类型**追问项目经历
（归属 / 指标 / 技术 / 架构 / 结果）、**按证据停止**而非只看次数、一份**风险信号清单**
（模糊词无证据、报数字说不清口径、强表述划不清个人边界…）。

评分维度：技术正确性 / 深度与原理 / 工程与场景思考 / 表达与结构，每维度**必须引用候选人原话作为证据**。
跑一场完整面试：`/user 名字` → 说「开始面试」→ 面试官按技能流程走 → 说「结束面试」→ 自动评分 → 存档。
对话被压缩过、记录不完整时，评分可用 `transcript_file` 参数指向 `.transcripts/` 里最新的
存档文件重建（压缩存档现在存的是可解析的消息 JSON）。

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest               # 离线测试（112 个，不需要 API key，约 2 秒）
python -m pytest -m slow       # 真实 API 冒烟测试（会消耗 token）
```

| 文件 | 覆盖 |
| ---- | ---- |
| `tests/test_offline_core.py` | cron 表达式校验/匹配、记忆预筛（含中文二元组切词）、权限三道闸门、MCP 命名与错误兜底、技能加载 |
| `tests/test_tasks.py` | 原子认领、依赖解锁、owner 互斥、环检测、worktree 绑定（目录丢失时失败不回落） |
| `tests/test_workflow.py` | JSON schema 校验、稳定调用键、journal 读写、pipeline 编排、续跑缓存命中（零重复调用） |
| `tests/test_runtime.py` | 消息总线（破坏性读/超时）、后台任务生命周期、压缩管线、cron 调度状态机、目标循环四个分支 |
| `tests/test_interview.py` | 题库检索（关键词/筛选/上限/落空提示）、多数据源加载、题库抽取（分类标签、URL 去重、markdown 清洗、分类归一）、评分 workflow、场次存档 |
| `tests/test_session.py` | 序列化往返（SDK 对象/dict/str）、原子保存、损坏容错、半截回合清理、`/clear` |
| `tests/test_smoke_api.py` | （slow）真实工具轮 + 独立判断器 |

隔离方式：`tests/conftest.py` 的 `iso` fixture 把 `WORKDIR` 和所有产物目录指到临时目录，
并清空模块级全局——测试之间互不影响，也不会碰真实项目文件。

CI（[.github/workflows/tests.yml](.github/workflows/tests.yml)）在 push 和 PR 时跑上面这套离线测试，
Ubuntu 与 Windows 双平台——本项目在 Windows 上开发，`agent.py` 里有 GBK 解码等平台相关分支。
**不需要配置任何密钥**：`slow` 标记的冒烟测试被默认排除，`conftest.py` 会给
`ANTHROPIC_API_KEY` 兜一个假值。

## 权限控制（三道闸门）

有副作用的工具执行前，会依次经过三道闸门。它是注册在 `PreToolUse` 事件上的
`permission_hook`（[agent.py](agent.py)）：

| 闸门 | 作用 | 例子 |
| ---- | ---- | ---- |
| 1. 硬拒绝 | 永远禁止，不询问 | `rm -rf /`、`sudo`、`shutdown`、**删除/覆盖项目核心文件**（`agent.py`、`requirements.txt` 等，见 `PROTECTED_FILES`） |
| 2. 规则匹配 | 命中规则的操作进入闸门 3 | 删除/下载/`pip`/`git` 命令、写入修改普通文件 |
| 3. 用户审批 | 暂停等你在终端输入 y/n（与主循环**共用同一个输入队列**，避免两个消费者抢 stdin） | 每次有副作用的操作 |

只读操作（`add`、`get_time`、`read_file`、`glob`）和普通命令（`dir`、`python ...`）直接放行。
规则写在 `PERMISSION_RULES` 里，想调整哪些操作需要确认，改这个列表即可。
> 这是教学级的权限门，真正的安全边界需要容器/沙箱隔离。

## 参考与来源

**框架来源**

- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) —— 本项目的 agent 运行时按它的
  s01→s17 教程路径逐章搭建（工具分发、权限、hooks、技能、压缩、记忆、任务图、后台任务、cron、
  团队协作、MCP、workflow、目标循环），全部集成在同一个循环上。

**题库来源**（9 个仓库/数据集，均为 MIT 许可，**只导入题目、不复制答案**）

| 方向 | 来源 |
| --- | --- |
| AI（英文） | [InterviewForge_GenDS](https://huggingface.co/datasets/Davichick/InterviewForge_GenDS) |
| AI（中文） | [guocong-bincai/ai-interview-guide](https://github.com/guocong-bincai/ai-interview-guide) · [bcefghj/learn-nanobot](https://github.com/bcefghj/learn-nanobot) · [bcefghj/ai-agent-interview-guide](https://github.com/bcefghj/ai-agent-interview-guide) · [aceliuchanghong/FAQ_Of_LLM_Interview](https://github.com/aceliuchanghong/FAQ_Of_LLM_Interview) · [lengyue1024/BAT_interviews](https://github.com/lengyue1024/BAT_interviews) |
| 前端 | [haizlin/fe-interview](https://github.com/haizlin/fe-interview) · [lf2021/Front-End-Interview](https://github.com/lf2021/Front-End-Interview) · [FEGuideTeam/FEGuide](https://github.com/FEGuideTeam/FEGuide) · [lengyue1024/BAT_interviews](https://github.com/lengyue1024/BAT_interviews) |

各自的题数与版权声明见上面「面试题库」和下面「许可」两节。

**方法论参考**

- [Hisn00w/ASu-skills](https://github.com/Hisn00w/ASu-skills) —— 面试官技能的追问纪律借鉴自它的
  `interview` 技能。只借鉴方法论，未安装它的技能文件（它的 `references/` 子目录和技能间交叉引用
  与本项目的技能加载方式不兼容）。

## 下一步可以做什么

- **面试官打磨**：评测前端岗的实战效果（岗位推断、选题分布、评分是否贴合前端）；开场单问的落实验证；评分松紧校准。
- **报告生成提速**：评分 workflow 目前偏慢（逐题并行评分 + 汇总），待研究减少轮次或复用缓存。
- **加自己的工具**：在 `agent.py` 里写一个 `@beta_tool` 函数即可——查天气 API、发邮件都行。
- **可选增强**：语音面试（TTS/ASR，需外部服务）、Web UI、场次对比报告。
- **换模型**：把 `MODEL` 改成 `claude-haiku-4-5`（映射到 deepseek-v4-flash，更快更便宜）。

## 许可

本项目代码：MIT License（见 [LICENSE](LICENSE)）。

面试题库只导入**题目**、不复制答案，九个来源均为 MIT 许可，各自的版权声明如下：

| 来源 | 版权声明 |
| ---- | ---- |
| [haizlin/fe-interview](https://github.com/haizlin/fe-interview) | Copyright (c) 2019 haizhilin |
| [InterviewForge_GenDS](https://huggingface.co/datasets/Davichick/InterviewForge_GenDS)（Hugging Face） | MIT |
| [guocong-bincai/ai-interview-guide](https://github.com/guocong-bincai/ai-interview-guide) | Copyright (c) 2026 guocong-bincai |
| [lf2021/Front-End-Interview](https://github.com/lf2021/Front-End-Interview) | Copyright (c) 2020 Lee |
| [lengyue1024/BAT_interviews](https://github.com/lengyue1024/BAT_interviews) | Copyright (c) 2018 冰羽 |
| [FEGuideTeam/FEGuide](https://github.com/FEGuideTeam/FEGuide) | Copyright (c) 2018 古月梦雅 |
| [bcefghj/ai-agent-interview-guide](https://github.com/bcefghj/ai-agent-interview-guide) | Copyright (c) 2026 |
| [bcefghj/learn-nanobot](https://github.com/bcefghj/learn-nanobot) | Copyright (c) 2026 bcefghj |
| [aceliuchanghong/FAQ_Of_LLM_Interview](https://github.com/aceliuchanghong/FAQ_Of_LLM_Interview) | Copyright (c) 2024 Lawrence kraft |

导入脚本：`interview/import_dataset.py`（英文）、`interview/import_github_bank.py`（中文与前端）。
