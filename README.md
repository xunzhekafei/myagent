# myagent · AI 面试练习室

一个从零构建的 Python Agent 运行时，以及基于它实现的 **AI 技术岗模拟面试应用**。
支持浏览器中的文字面试，也保留终端交互模式。面试围绕候选人背景和项目经历展开，结束后生成四维评分、逐题证据与改进建议。

核心运行时按 [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) s01→s17 逐章实现，包含工具调用、hooks、记忆、上下文压缩、任务系统、团队协作和工作流编排。
模型通过 Anthropic Python SDK 连接 DeepSeek 的 Anthropic 兼容端点，使用 DeepSeek 密钥。

## 导航

- [主要功能](#主要功能)
- [快速开始](#快速开始)
- [跑一场模拟面试](#跑一场模拟面试)
- [架构与目录](#架构与目录)
- [数据与会话](#数据与会话)
- [开发与测试](#开发与测试)
- [配置与常见问题](#配置与常见问题)
- [语音扩展计划](#语音扩展计划)
- [文档与许可](#文档与许可)

## 主要功能

| 能力 | 当前实现 |
| --- | --- |
| 面试设置 | 姓名、目标岗位、个人背景与练习重点 |
| 面试对话 | 流式回复、Markdown 展示、多行回答；通过技能约束一次一题与追问节奏 |
| 面试评分 | 技术正确性、深度与原理、工程与场景思考、表达与结构；逐题证据与建议 |
| Web 场次管理 | 独立场次、完整问答保存、历史记录、报告 JSON 下载 |
| 运行恢复 | 取消、重试、提交去重、断线后恢复快照、服务重启后保留未完成记录 |
| 题库检索 | 内置 1647 道 AI 岗位题，按英文关键词、类别、难度和岗位筛选 |
| CLI 运行时 | 32 个主工具、hooks、任务图、记忆、上下文压缩、cron、子代理、团队、MCP 与 workflow |
| 语音扩展 | 已定义 ASR/TTS 接口；录音、识别、朗读和实时语音尚未接入 |

Web 版面向**本地单用户**，使用单后端进程与单工作线程队列；评分 workflow 内部仍可并行处理子任务。
Web 对话仅开放 `search_questions` 和 `load_skill`，评分与存档由后端管理。CLI 保留完整运行时能力。

## 快速开始

以下命令以 Windows PowerShell 为例，在项目根目录执行。使用虚拟环境中的 Python，无需手动激活环境。

### 1. 环境准备

- Python 3.10+。
- Web 前端构建使用 Node.js，建议使用 22.12+ 的 22.x 或 24.x；仅运行 CLI 不需要 Node.js。
- 开始模型对话需要有效的 DeepSeek API Key。

```powershell
python -m venv .venv
```

已有 `.venv` 时可跳过创建。

### 2. 安装并启动 Web 版

安装后端依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt
```

安装前端依赖并构建页面：

```powershell
cd frontend
npm ci
npm run build
cd ..
```

在同一终端设置密钥并启动：

```powershell
$env:ANTHROPIC_API_KEY = "你的 DeepSeek 密钥"
.\start-web.ps1
```

打开 [面试练习室](http://127.0.0.1:8000)。停止服务使用 Ctrl+C。

后续启动只需设置密钥并执行 `start-web.ps1`。前端源代码修改后需重新执行 `npm run build`。
如果 PowerShell 阻止执行启动脚本，可直接运行等效命令：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

没有密钥也能启动 Web 服务并查看已有记录，但不能开始模型对话。修改密钥后需重启后端。

### 3. 终端模式

仅使用 CLI 时安装基础依赖即可；已安装 Web 依赖则无需重复安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:ANTHROPIC_API_KEY = "你的 DeepSeek 密钥"
.\.venv\Scripts\python.exe agent.py
```

也支持单次提问：

```powershell
.\.venv\Scripts\python.exe agent.py "请用工具计算 123 + 456"
```

| CLI 命令 | 用途 |
| --- | --- |
| `/user 名字` | 设置候选人 |
| `/clear` 或 `/new` | 清空当前对话，保留长期记忆 |
| `/goal 完成条件` | 设置目标并自动推进 |
| `/goal` / `/goal clear` | 查看 / 清除目标 |
| `exit` / `quit` | 退出并保存会话 |

## 跑一场模拟面试

### Web 流程

1. 填写姓名、目标岗位及背景，进入面试室，点击「开始面试」。
2. 按问题提交回答。Enter 换行，Ctrl / Command + Enter 发送。
3. 需要中断时点击「停止本轮」；失败或取消后可以重试，已提交的回答不会重复插入。
4. 点击「结束并生成报告」，或提交精确文本「结束面试」「不面了」。至少回答一次才能评分。
5. 查看综合评分、四维得分和逐题复盘；在侧栏恢复历史场次，也可以下载报告 JSON。

评分使用**当前场次已经形成的问答**。结束时尚未回答的最后一个问题不送去评分；明确回答「不会 / 跳过」仍属于问答记录。
中断的面试官回复保留供查看，不进入后续上下文与评分。评分结果由模型生成。

### CLI 流程

```text
你 > /clear
你 > /user 你的名字
你 > 开始面试
（回答面试官的问题）
你 > 结束面试
（生成评分报告并存档）
```

CLI 启动会恢复最近一次对话，开始新场面试前建议先 `/clear`。
面试规则见 [mock-interviewer 技能](skills/mock-interviewer/SKILL.md)，评分由 `interview-report` workflow 完成。

### 题库

题库位于 `interview/data/ai_questions.json`，来源为 InterviewForge_GenDS：AI/ML 工程师 576 道、数据分析师 576 道、数据科学家 495 道。
原题为英文且没有参考答案，面试官检索后用中文提问。`search_questions` 的 `query` 使用英文关键词，例如 `model deployment latency`。

已有题库可直接使用；如需重新导入，在下载源 CSV 后运行：

```powershell
.\.venv\Scripts\python.exe interview/import_dataset.py "你的 CSV 文件路径"
```

数据来源与许可见文末。前端也提供前后端开发岗位选项，但当前内置题库主要覆盖 AI 与数据方向。

## 架构与目录

```text
React + TypeScript 页面
    │ HTTP：创建场次、读取历史、获取报告
    │ WebSocket：提交回答、接收回复与进度、取消与重试
    ▼
FastAPI 接口（backend/app.py）
    ▼
面试服务（backend/service.py）── SQLite 场次快照与事件日志
    ▼
Agent 适配层（backend/agent_adapter.py）
    ├── agent_loop：流式对话 + 面试工具白名单
    └── interview-report：解析问答 → 逐题评分 → 汇总报告

CLI：agent.py → chat_loop → ask → agent_loop
语音预留：ASR → 最终转写 → 提交回答；面试官回复 → TTS
```

`agent.py` 的主循环手动处理模型请求、工具调用和结果回传，使用 SDK 的 `messages.stream()` 接收增量文本。
Web 通过事件回调接收输出；CLI 继续打印到终端。Web 隔离模式不接入 CLI 的全局 hooks、记忆、cron、团队和会话状态。

| 路径 | 职责 |
| --- | --- |
| `agent.py` | Agent 核心、工具、hooks、CLI 与评分 workflow |
| `frontend/src/App.tsx` | 面试设置、历史场次、对话与连接恢复 |
| `frontend/src/components/ReportView.tsx` | 评分报告展示与下载 |
| `frontend/src/api.ts` / `types.ts` | HTTP / WebSocket 入口与数据类型 |
| `frontend/src/style.css` | 响应式页面样式 |
| `backend/app.py` | FastAPI 路由、校验和静态页面托管 |
| `backend/service.py` | 执行队列、轮次状态、取消、重试和去重 |
| `backend/sessions.py` | SQLite 存储与递增事件日志 |
| `backend/agent_adapter.py` | 连接现有 Agent，整理当前场次的上下文和评分记录 |
| `backend/speech.py` | ASR / TTS 接口定义和语音能力声明 |
| `skills/` | 面试官、代码审查与接口使用技能 |
| `interview/` | 题库与导入脚本 |
| `tests/` | 离线测试与真实 API 冒烟测试 |
| `requirements*.txt` | 基础、Web、开发测试依赖 |
| `start-web.ps1` | 本地 Web 启动脚本 |
| `documents/` | 运行时详解、Web 协议、进度与实现记录 |

完整的 hooks、权限三道闸门、压缩管线、任务系统和工作流说明见 [Agent 运行时详解](documents/AGENT_RUNTIME.md)。
HTTP 路由、WebSocket 事件、提交格式与取消机制见 [Web 前端说明](documents/WEB_FRONTEND.md)。

## 数据与会话

| 数据 | Web | CLI |
| --- | --- | --- |
| 对话与候选人 | `.web-data/interviews.sqlite3` 中的独立场次 | `.sessions/latest.json` 保存最近对话；`/user` 设置候选人 |
| 面试报告 | 存入当前 SQLite 场次 | `.interviews/<候选人>/` |
| 评分中间结果 | `.web-data/<session_id>/report.journal.jsonl` | `.runtime/` |
| 模型上下文 | 最近约 60000 字符的已完成消息，完整问答另行保存 | 四步上下文压缩，阈值由 `CONTEXT_CHAR_LIMIT` 控制 |
| 长期记忆 | 当前不启用 CLI 记忆 | `.memory/` |

两种模式的数据独立，目前不自动迁移或合并历史。
Web 评分会分批解析全部已形成的问答，不读取 CLI 的公共压缩存档。

WebSocket 连接时发送最新场次快照，包括部分生成文本；刷新或重连不会重复拼接旧回复。
断线不会取消后台工作，服务重启会将未完成轮次标记为中断并允许重试。

取消为协作式：页面立即停止接收旧轮次结果，后端在流式增量、工具和评分调用边界检查信号。
正在等待的网络调用可能仍需退出，新轮次也可能等待执行队列；取消不会撤销已经产生的模型费用。

运行数据目录、虚拟环境、前端依赖和构建产物均已加入 `.gitignore`。
备份 Web 数据时可停止服务后复制整个 `.web-data/`；运行中备份应使用 SQLite 在线备份方式。

## 开发与测试

### 前后端开发

先完成依赖安装，再开启两个终端。

终端 1（项目根目录，设置好密钥）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

终端 2：

```powershell
cd frontend
npm run dev
```

访问 [开发页面](http://127.0.0.1:5173)。Vite 将 `/api` 和 WebSocket 转发到后端 8000 端口。
API 文档位于 [FastAPI Docs](http://127.0.0.1:8000/docs)。

### 验证命令

完整测试集包含 Web 测试，因此需要同时安装 Web 和开发依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

默认只运行离线测试，不需要真实密钥。真实 API 冒烟测试单独执行，会消耗模型 token：

```powershell
.\.venv\Scripts\python.exe -m pytest -m slow
```

前端类型检查与生产构建：

```powershell
cd frontend
npm run build
# 按需格式化前端源码
npm run format
```

最近一次验证（2026-09-19）：**111 个离线测试通过，2 个真实 API 测试未运行；TypeScript 检查和生产构建通过**。

| 测试文件 | 覆盖 |
| --- | --- |
| `test_offline_core.py` | cron 校验、记忆、权限、MCP 与技能 |
| `test_tasks.py` | 任务依赖、认领、owner 互斥与工作目录绑定 |
| `test_workflow.py` | schema 校验、journal、并行编排与缓存续跑 |
| `test_runtime.py` | 消息总线、后台任务、上下文压缩、cron 与目标循环 |
| `test_session.py` | CLI 序列化、存档恢复、半截回合清理与输入合并 |
| `test_interview.py` | 题库检索、评分流程与面试存档 |
| `test_web.py` | 场次隔离、流式事件、取消重试、重连去重、重启恢复、完整评分与错误提示 |
| `test_smoke_api.py` | 真实模型工具轮与目标判断器（`slow`） |

测试使用临时目录和模拟模型；CLI 测试通过 `iso` fixture 重置运行时状态，Web 测试注入独立适配器和数据库。

## 配置与常见问题

| 配置 | 作用 |
| --- | --- |
| `ANTHROPIC_API_KEY` | DeepSeek 密钥，由后端或 CLI 进程环境读取 |
| `AUX_MODEL` | CLI 辅助模型，默认 `claude-haiku-4-5` |
| `WEB_ALLOWED_ORIGINS` | Web 允许的来源，逗号分隔；默认允许 localhost / 127.0.0.1 的 8000 和 5173 端口 |
| `agent.py` 中的 `MODEL` | 主模型请求名，当前为 `claude-opus-5` |
| `agent.py` 中的 `base_url` | 默认 `https://api.deepseek.com/anthropic` |

模型请求名是兼容接口配置，实际路由由服务商决定。对话、背景与评分内容会随模型请求发送给该服务商。
密钥不会写入前端或浏览器；默认启动命令直接读取进程环境，不会自动加载 `.env`。

**页面提示未配置密钥或鉴权失败（401）**：检查启动后端的同一终端是否设置了有效的 DeepSeek 密钥，然后重启服务。`/api/health` 仅检查密钥是否存在，不验证有效性。

**提示前端未构建**：在 `frontend/` 运行 `npm ci`、`npm run build`，然后重启后端。
如果 PowerShell 不允许执行 `npm.ps1`，可将命令中的 `npm` 改为 `npm.cmd`。

**更换端口后 WebSocket 无法连接**：同步设置允许来源，例如：

```powershell
$env:WEB_ALLOWED_ORIGINS = "http://127.0.0.1:8001,http://localhost:8001"
.\start-web.ps1 -Port 8001
```

前后端开发模式若更改后端端口，还需修改 `frontend/vite.config.ts` 的代理目标。

**停止本轮后下一轮没有立即回复**：旧模型请求可能仍在退出，Web 使用单执行队列；网络调用配置 60 秒超时和最多一次 SDK 重试。

**部署范围**：当前使用一个 Uvicorn worker，仅面向本地使用。账户鉴权、多用户权限和多进程队列尚未实现。

## 语音扩展计划

已经预留 `SpeechRecognizer`、`SpeechSynthesizer` 和带 `final` 标记的转写类型，但当前只支持文字面试。

后续按以下顺序扩展：

1. 录音 → ASR 转写 → 用户确认或修改 → 复用现有回答提交接口。
2. 面试官回复 → 分句 TTS → 前端播放；停止播放与取消生成分别处理。
3. 自动判断说话结束、允许打断；通过 `turn_id` 丢弃迟到的文字与音频，按需增加 WebRTC。

临时转写只用于展示，不逐段送给 Agent；最终提交的回答进入完整问答记录。
其他待完善方向包括更丰富的岗位题库、评分口径校准，以及跨场次进步对比。

## 文档与许可

- [Agent 运行时详解](documents/AGENT_RUNTIME.md)：保留原 README 的 s01→s17 机制说明。
- [Web 前端说明](documents/WEB_FRONTEND.md)：文件修改清单、实时协议、语音接口与验证记录。
- [项目进度与设计记录](documents/PROGRESS.md)：阶段里程碑和历史问题记录，状态以文件标注日期为准。
- [面试官技能](skills/mock-interviewer/SKILL.md)：面试行为与评分流程约束。

项目使用 [MIT License](LICENSE)。面试题库来自 [InterviewForge_GenDS](https://huggingface.co/datasets/Davichick/InterviewForge_GenDS)（MIT 许可），通过 `interview/import_dataset.py` 导入。
