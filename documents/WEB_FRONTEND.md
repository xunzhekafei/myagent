# Web 面试界面与语音扩展

## 本次修改

| 文件 / 目录 | 修改内容 |
| --- | --- |
| `frontend/` | React + TypeScript + Vite；设置、流式对话、历史场次、评分报告与 JSON 下载；响应式布局和 Markdown 回复；报告组件独立，提供 `npm run format` |
| `backend/app.py` | FastAPI HTTP / WebSocket 接口、输入校验、来源校验、生产静态页面托管 |
| `backend/sessions.py` | SQLite 场次快照与递增事件日志；记录独立于模型上下文 |
| `backend/service.py` | 单工作线程执行队列、场次隔离、提交去重、取消、重试、重启恢复 |
| `backend/agent_adapter.py` | 复用 Agent 和评分 workflow；面试工具白名单、上下文裁剪、按场次评分与进度事件 |
| `backend/speech.py` | ASR / TTS Protocol 与转写结果类型（零依赖，能力由注入的 recognizer 决定） |
| `backend/whisper_asr.py` | 本地 faster-whisper 实现：懒加载、GPU 优先 CPU 兜底、串行化推理 |
| `requirements-speech.txt` | 语音依赖（可选；不装则 `speech.asr` 为 false、麦克风按钮不出现） |
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
| `POST /api/sessions/{id}/transcribe` | 语音转文字：body 是原始音频字节，返回 `{text, utterance_id}`。**不写入会话**、不落盘。状态码：404 场次不存在 / 409 已结束 / 503 未启用语音 / 413 超限 / 415 类型不支持 |
| `POST /api/speech/warmup` | 显式加载语音模型（首次可能要下载几百 MB），返回更新后的能力声明 |

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

## 语音转文字

**已实现，但默认关闭**（要另装 `requirements-speech.txt`）：录音 → 本地 faster-whisper 转写 →
文字填入输入框 → 用户确认或修改 → 走原来的 `answer` 提交。
**TTS、实时部分转写、自动断句与打断仍未做。**

三种状态：什么都不做 = 没有语音（麦克风按钮不出现）；装了依赖 = 有语音；
装了但设 `WEB_SPEECH=off`（接受 `0` / `false` / `no`，大小写与空格不敏感）= 仍按没有语音处理，
不必卸载。成本与准确率的实测数据见 README 的「语音转文字」一节。

协议形状**保持不变**：`speech.py` 的 `SpeechRecognizer` 仍是流式契约
（`transcribe(audio: AsyncIterator[bytes], *, mime_type) -> AsyncIterator[Transcript]`），
批式后端天然满足它——先收完音频块，用 `asyncio.to_thread` 跑推理，只 yield 一个 `final=True`。
将来要接真流式，位置还留着。`SpeechSynthesizer` 仍是空契约。

**与原计划的偏离**：这里原本记的是「扩展 WebSocket 音频事件」。实际改走**独立的 HTTP 端点**
`POST /api/sessions/{id}/transcribe`，因为音频根本不需要进入会话——转写结果进输入框、由用户确认后
才以文字提交。这样一次绕开三个障碍：WS 循环只收 `receive_text()`、单条消息 64000 字符上限、
`Command` 的 `action: Literal[...]` 校验。原计划里的「输入模式校验」和「音频存储策略」因此**不再需要**，
不是被砍掉而是被绕过了——服务端在提交时只看到一个字符串，无法区分打字与口述，所以
`input_mode` 保持 `"text"`（`types.ts` 里的 `"voice"` 联合类型保留未用）。

几个实现要点：

- **能力与就绪分开**：`/api/health` 的 `speech.asr` 表示「这台机器有语音能力」（取决于注入的
  recognizer 是否为 None），`speech.ready` 表示「模型已加载」。前端只看 `asr`。
  `asr` **不能**由「faster_whisper 能否 import」决定——那样本机一装依赖，
  断言 `asr is False` 的测试就挂，而 CI 照样绿，故障只在功能开始可用之后、只在本机出现。
- **转写不碰 `service.lock`，也不走 `service.executor`**。后者是 `max_workers=1` 且绑着轮次状态机，
  转写不是轮次；前者被 WS 处理在事件循环上同步持有，占住它会把快照和健康检查一起冻住。
- **模型不在启动时加载**，首次转写或 `POST /api/speech/warmup` 才加载。
  `start-web.ps1` 默认设 `HF_ENDPOINT=https://hf-mirror.com`——国内直连 Hugging Face 通常卡到超时。
- 音频转写完即丢，**不落盘**。
- 端点没有 `request_id` 幂等：本地无副作用、结果确定，重复转写无害。

## 尚未接入（TTS 与实时语音）

- `SpeechSynthesizer.synthesize()` 接收回复文字与 `turn_id`，输出音频流——仍是空契约。
- 实现 TTS 时再考虑句级缓冲与播放；停止播放仅清空播放队列、取消生成走轮次取消、结束面试走 `finish`，三者分别处理。
- 进一步支持自动说话结束检测与打断时，复用 `turn_id` 丢弃旧音频和旧文字；需要时增加 WebRTC 音频通道。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest
cd frontend
npm run build
```

离线测试不加载语音模型（`tests/test_speech.py` 注入假识别器），也不需要密钥，约 5 秒跑完。
装了 `requirements-speech.txt` 之后这一点**依然成立**——测试里构造 app 时显式传 `recognizer=None`。

离线测试不会调用模型。现有 `-m slow` 仍表示真实 API 冒烟测试，会使用已配置的密钥。

本次验证结果（2026-09-19）：111 个离线测试通过，2 个真实 API 测试未运行；TypeScript 检查和 Vite 生产构建通过。
浏览器已检查设置页、场次连接、刷新恢复、中断提示，以及已有完成场次的报告展示。
由开发验证进程发起的真实模型请求收到 401 鉴权失败，因此未宣称该进程完成了真实模型的全链路验证。

实现参考：[FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websockets/)、[React](https://react.dev/learn)、[Vite](https://vite.dev/guide/)。
