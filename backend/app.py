"""Run locally: python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000."""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .service import InterviewService
from .speech import CAPABILITIES

ROOT = Path(__file__).resolve().parent.parent

# create_app 没显式传 recognizer 时用这个哨兵，表示「按环境试着构建一个」。
# 显式传 None 则表示「不要语音」，两者含义不同：测试靠它拿到确定行为。
_AUTO = object()

# 音频大小上限（约 20 分钟的 opus）。真正的防线在前端时长上限，这里只是兜底。
MAX_AUDIO_BYTES = 5 * 1024 * 1024

# 浏览器 MediaRecorder 可能产出的容器。PyAV 三种都能解，这里只做早期拦截给出清晰报错。
AUDIO_TYPES = ("audio/webm", "audio/ogg", "audio/mp4", "audio/aac", "audio/wav", "audio/x-wav")


def _build_recognizer():
    """按环境尝试构建本地语音识别器；依赖没装就返回 None，不抛异常。

    WEB_SPEECH=off 可以显式关掉语音——装了依赖但暂时不想用时，不必去卸载。
    必须在函数内部 import：CI 只装 requirements-web.txt + requirements-dev.txt，
    模块级 import 会把 faster-whisper 变成硬依赖，也会让每次 import backend.app 都变慢。
    """
    if (os.environ.get("WEB_SPEECH") or "").strip().lower() in ("off", "0", "false", "no"):
        print("[语音] 已由 WEB_SPEECH 关闭（麦克风按钮不会出现）", flush=True)
        return None
    try:
        from .whisper_asr import build_recognizer
    except Exception:      # 依赖缺失、CUDA DLL 加载失败，都算「这台机器没有语音能力」
        return None
    return build_recognizer()


class SessionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    candidate: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=120)
    background: str = Field(default="", max_length=8000)
    # 面试压力档位，各档规则见 skills/mock-interviewer/SKILL.md
    pressure: Literal["温和", "标准", "压力"] = "标准"


class Command(BaseModel):
    action: Literal["start", "answer", "finish", "cancel", "retry"]
    text: str = Field(default="", max_length=12000)
    request_id: str = Field(min_length=8, max_length=80)
    # 只用来告诉评分「这条是语音转写的」——服务端在提交时只拿到一个字符串，
    # 无法核实它到底是不是语音输入。作为宽容术语拼写的提示足够，别当事实用。
    # 注意这个模型没设 model_config，pydantic 默认 extra="ignore"：
    # 客户端多传字段不会报错，只会被静默丢掉。加字段必须同时改这里。
    input_mode: Literal["text", "voice"] = "text"


def create_app(directory=None, adapter=None, recognizer=_AUTO):
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = InterviewService(directory or ROOT / ".web-data", adapter)
        # 语音能力在启动时解析一次：_AUTO → 按环境试建；显式 None → 关闭语音。
        # 这里只构建识别器对象，**不加载模型**（首次转写或预热接口才加载）。
        app.state.recognizer = _build_recognizer() if recognizer is _AUTO else recognizer
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.service.close)

    app = FastAPI(title="Interview Studio", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    origins = set(os.environ.get("WEB_ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173").split(","))

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        # Same-origin app / Vite proxy. Reject browser writes from unrelated sites.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("origin") not in origins | {None}:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "不允许的来源"}, status_code=403)
        return await call_next(request)

    def get_session(session_id):
        try:
            return app.state.service.store.get(session_id)
        except KeyError:
            raise HTTPException(404, "找不到面试场次")

    def speech_capabilities():
        """对外提供的能力由注入的 recognizer 决定，不是「faster_whisper 能不能 import」。

        否则会出现这样的坑：本机装上语音依赖后 asr 变 True，而断言 asr is False 的测试就挂——
        故障只在功能开始可用之后、且只在本机出现，CI 反而照样绿。
        """
        recognizer = app.state.recognizer
        capabilities = dict(CAPABILITIES)          # 复制一份，不 mutate 模块级共享的那份
        capabilities["asr"] = recognizer is not None
        capabilities["ready"] = bool(recognizer is not None and recognizer.ready())
        return capabilities

    @app.get("/api/health")
    def health():
        return {"configured": app.state.service.adapter.configured(), "speech": speech_capabilities()}

    @app.get("/api/sessions")
    def sessions():
        return [{key: s[key] for key in ("id", "candidate", "role", "created_at", "status")}
                for s in app.state.service.store.list()]

    @app.post("/api/sessions", status_code=201)
    def create_session(body: SessionInput):
        return app.state.service.create(**body.model_dump())

    @app.get("/api/sessions/{session_id}")
    def session(session_id: str):
        return get_session(session_id)

    @app.get("/api/sessions/{session_id}/report")
    def report(session_id: str):
        result = get_session(session_id)["report"]
        if result is None:
            raise HTTPException(404, "报告尚未生成")
        return result

    @app.post("/api/sessions/{session_id}/transcribe")
    async def transcribe(session_id: str, request: Request):
        """把一段录音转成文字。**不写入会话**——文字回给前端填进输入框，由用户确认后再走 answer 提交。

        因此这里不碰 service.lock、也不走 service.executor（那是轮次专用的单线程队列）：
        推理要跑十几秒，占住它们会把事件循环和正在流式的轮次一起冻住。
        """
        recognizer = app.state.recognizer
        if recognizer is None:
            raise HTTPException(503, "本机未启用语音识别（需安装 requirements-speech.txt）")
        state = get_session(session_id)
        if state["status"] == "completed":
            raise HTTPException(409, "这场面试已完成，不能再录入回答")

        mime_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if mime_type and not any(mime_type.startswith(t) for t in AUDIO_TYPES):
            raise HTTPException(415, f"不支持的音频类型：{mime_type}")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "录音太长了，请分段提交")
        audio = await request.body()
        if not audio:
            raise HTTPException(400, "没有收到音频数据")
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "录音太长了，请分段提交")

        async def chunks():
            yield audio

        text, utterance_id = "", ""
        try:
            async for transcript in recognizer.transcribe(
                    chunks(), mime_type=mime_type or "application/octet-stream"):
                text, utterance_id = transcript.text, transcript.utterance_id
                if transcript.final:
                    break
        except Exception:
            # 不把底层异常细节回给浏览器（和 service.public_error 同样的考虑）
            logging.getLogger(__name__).exception("Transcribe failed for session %s", session_id)
            raise HTTPException(500, "语音识别失败，请检查后端日志")
        return {"text": text, "utterance_id": utterance_id}

    @app.post("/api/speech/warmup")
    async def warmup():
        """显式加载语音模型。首次可能要下载几百 MB，所以不在服务启动时做，由用户触发。"""
        recognizer = app.state.recognizer
        if recognizer is None:
            raise HTTPException(503, "本机未启用语音识别（需安装 requirements-speech.txt）")
        try:
            await asyncio.to_thread(recognizer.warmup)
        except Exception:
            logging.getLogger(__name__).exception("Speech warmup failed")
            raise HTTPException(500, "语音模型加载失败，请检查后端日志")
        return speech_capabilities()

    @app.websocket("/api/sessions/{session_id}/ws")
    async def socket(websocket: WebSocket, session_id: str):
        if websocket.headers.get("origin") not in origins | {None}:
            await websocket.close(code=1008)
            return
        try:
            state = app.state.service.store.get(session_id)
        except KeyError:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        cursor = state["seq"]
        await websocket.send_json({"type": "session.snapshot", "seq": cursor, "data": state})
        try:
            while True:
                events = app.state.service.store.events(session_id, cursor)
                if events:
                    for event in events:
                        await websocket.send_json(event)
                    # Snapshot is authoritative, including partial output on reconnect.
                    state = app.state.service.store.get(session_id)
                    cursor = state["seq"]
                    await websocket.send_json({"type": "session.snapshot", "seq": cursor, "data": state})
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                if len(raw) > 64000:
                    await websocket.close(code=1009)
                    return
                try:
                    command = Command.model_validate_json(raw)
                    state = app.state.service.submit(session_id, **command.model_dump())
                    await websocket.send_json({"type": "command.accepted", "request_id": command.request_id})
                    await websocket.send_json({"type": "session.snapshot", "seq": state["seq"], "data": state})
                except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                    await websocket.send_json({"type": "command.error", "data": {"message": str(exc)}})
        except WebSocketDisconnect:
            pass  # The worker persists its result even if the browser closes.

    dist = ROOT / "frontend" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (dist / "index.html").is_file():
            raise HTTPException(503, "请先在 frontend 执行 npm install 和 npm run build")
        return FileResponse(dist / "index.html")

    return app


app = create_app()
