# Web 面试界面与语音扩展

## 本次修改

| 文件 / 目录 | 修改内容 |
| --- | --- |
| `frontend/` | React + TypeScript + Vite；设置、流式对话、历史场次、评分报告与 JSON 下载；响应式布局和 Markdown 回复；报告组件独立，提供 `npm run format` |
| `backend/app.py` | FastAPI HTTP / WebSocket 接口、输入校验、来源校验、生产静态页面托管 |
| `backend/sessions.py` | SQLite 场次快照与递增事件日志；记录独立于模型上下文 |
| `backend/service.py` | 单工作线程执行队列、场次隔离、提交去重、取消、重试、重启恢复 |
| `backend/agent_adapter.py` | 复用 Agent 和评分 workflow；面试工具白名单、上下文裁剪、按场次评分与进度事件 |
| `backend/speech.py` | ASR / TTS Protocol 与转写结果类型，能力查询明确返回尚未启用语音 |
| `agent.py` | 缺少密钥时可导入；增加事件回调、取消信号、隔离模式和可注入客户端；评分可分批解析全部记录 |
| `tests/test_web.py` | WebSocket 链路、场次隔离、取消与迟到回复、重试去重、重启、校验、流式输出与完整评分覆盖 |
| `requirements-web.txt` / `requirements-dev.txt` | Web 和测试依赖 |
| `start-web.ps1` | 构建完成后启动本地服务 |
| `.gitignore` | 忽略数据库、依赖目录、前端产物与测试缓存 |

原有 `python agent.py` CLI 保留。原有 API Key 显式传入方式保留，在没有密钥时不初始化客户端。
Web 不调用 CLI 的 `chat_loop()`，不共享 `session_history`、候选人、目标、记忆、cron、后台任务或会话存档。

## 首次安装

Python 3.10+；Node.js 建议 22.12+ 或 24。项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-web.txt -r requirements-dev.txt
cd frontend
npm install
npm run build
cd ..
```

设置模型密钥后启动（沿用原项目的 DeepSeek 兼容接口）：

```powershell
$env:ANTHROPIC_API_KEY = "你的 DeepSeek 密钥"
.\start-web.ps1
```

浏览器打开 <http://127.0.0.1:8000>。也可以直接运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

没有密钥也能启动并查看历史，页面会提示配置；无法开始模型对话。密钥只由后端环境读取，不写入前端、数据库或浏览器。
修改密钥后应重启后端。构建前端后若先前服务未发现静态产物，也需重启后端。

开发时开启两个终端：

```powershell
# 终端 1：根目录
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
# 终端 2
cd frontend
npm run dev
```

访问 <http://127.0.0.1:5173>，Vite 转发 `/api` 和 WebSocket。默认允许 8000 / 5173 的 localhost 与 127.0.0.1 来源；更换端口时同步设置 `WEB_ALLOWED_ORIGINS`（逗号分隔）。
当前设计为本地单用户、单后端进程。请使用一个 Uvicorn worker；多进程队列、账户鉴权与公网部署不在本版本范围。

## 面试流程与数据

1. 填写姓名、岗位和背景，进入面试室，点击「开始面试」。
2. 输入回答；Enter 换行，Ctrl / Command + Enter 发送。浏览器刷新可恢复场次。
3. 点击「结束并生成报告」，或提交精确文本「结束面试」「不面了」，由宿主进入评分流程。
4. 查看综合评分、四维得分、逐题证据与建议；可下载报告 JSON。

数据保存到 `.web-data/interviews.sqlite3`。工作流中间结果保存到 `.web-data/<session_id>/report.journal.jsonl`，重试可复用已完成步骤。
这些目录均已 gitignore；Web 不写入或迁移 CLI 的 `.sessions/latest.json`、`.interviews/`。
备份时停止服务后复制整个 `.web-data/`，或使用 SQLite 在线备份工具；运行中不要只复制数据库主文件而忽略 WAL。

每条问答记录包含 `id / turn_id / role / text / status / input_mode / created_at`。
完整问答不会因模型上下文裁剪而丢失；模型只读取最近约 60000 字符的已完成消息，另加背景与面试规则。
评分明确使用当前场次的全部已完成问答，按并发上限分批解析，不读取公共压缩存档，也不截掉前面的分段。
结束时尚未回答的最后一个问题不会送去评分；明确回答「不会 / 跳过」仍属于有效问答记录。已有报告保持原样。
中断的面试官回复保留供查看，但不进入后续上下文与评分。候选人已提交的回答仍然保留，重试不会重复插入。

## 实时协议

HTTP：

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 密钥是否配置、语音能力 |
| `GET /api/sessions` | 场次列表 |
| `POST /api/sessions` | 创建场次：`candidate / role / background` |
| `GET /api/sessions/{id}` | 完整场次快照 |
| `GET /api/sessions/{id}/report` | 评分报告 |

WebSocket：`/api/sessions/{id}/ws`。客户端提交：

```json
{"action":"answer","text":"我的回答","request_id":"客户端生成的唯一 ID"}
```

支持 `start / answer / finish / cancel / retry`。相同场次的 `request_id` 去重。
业务事件包含 `type / session_id / turn_id / seq / data`；`seq` 为数据库全局递增序号，允许场次间有间隔。
事件类型：`turn.started`、`reply.delta`、`reply.completed`、`tool.started`、`tool.completed`、`report.progress`、`report.completed`、`turn.completed`、`turn.cancelled`、`turn.failed`。

连接时发送 `session.snapshot`，包含最新序号和正在生成的部分文本。随后发送业务事件，并推送最新权威快照。
重连以完整快照恢复，不重放已显示的文字；客户端按序号拒绝旧快照。断线不取消工作，结果继续落盘。
`command.accepted / command.error` 是提交确认消息，不属于持久业务事件。

取消后立即让页面恢复可操作状态，并通过 `turn_id` 丢弃迟到结果。当前同步 SDK 使用协作式取消：在文本增量、工具和评分调用边界检查。
正在等待的网络调用并非强制终止；Web 调用配置 60 秒网络超时、至多一次 SDK 重试。由于只有一个执行线程，新轮次可能需要等待旧请求退出。
中断不会撤销已经产生的模型费用。后端异常的详情写入终端，页面区分鉴权失败、限流和网络错误，不直接展示 SDK 响应体。

## 语音接入边界

目前仅支持文字，未接入麦克风、识别或朗读服务，也没有伪装可用的语音按钮。

- `SpeechRecognizer.transcribe()` 接收音频流，输出带 `utterance_id` 和 `final` 的 `Transcript`。
- `SpeechSynthesizer.synthesize()` 接收回复文字与 `turn_id`，输出音频流。
- 下一阶段实现录音 → ASR → 用户确认最终转写 → `answer`。临时识别文本只用于展示。
- 届时扩展 WebSocket 音频事件、输入模式校验和音频存储策略，再实现句级 TTS 缓冲与播放。
- 停止播放仅清空播放队列；取消生成走轮次取消；结束面试走 `finish`，三者分别处理。
- 进一步支持自动说话结束检测与打断时，复用 `turn_id` 丢弃旧音频和旧文字；需要时增加 WebRTC 音频通道。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest
cd frontend
npm run build
```

离线测试不会调用模型。现有 `-m slow` 仍表示真实 API 冒烟测试，会使用已配置的密钥。

本次验证结果（2026-09-19）：111 个离线测试通过，2 个真实 API 测试未运行；TypeScript 检查和 Vite 生产构建通过。
浏览器已检查设置页、场次连接、刷新恢复、中断提示，以及已有完成场次的报告展示。
由开发验证进程发起的真实模型请求收到 401 鉴权失败，因此未宣称该进程完成了真实模型的全链路验证。

实现参考：[FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/)、[React](https://react.dev/learn)、[Vite](https://vite.dev/guide/)。
