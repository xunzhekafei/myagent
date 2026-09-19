"""Run locally: python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000."""
import asyncio
import json
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


class SessionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    candidate: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=120)
    background: str = Field(default="", max_length=8000)


class Command(BaseModel):
    action: Literal["start", "answer", "finish", "cancel", "retry"]
    text: str = Field(default="", max_length=12000)
    request_id: str = Field(min_length=8, max_length=80)


def create_app(directory=None, adapter=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = InterviewService(directory or ROOT / ".web-data", adapter)
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

    @app.get("/api/health")
    def health():
        return {"configured": app.state.service.adapter.configured(), "speech": CAPABILITIES}

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
