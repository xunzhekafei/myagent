"""Offline web acceptance tests; no API requests or real interview files."""
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.agent_adapter import answered_transcript
from backend.service import InterviewService, public_error


class FakeAgent:
    def configured(self):
        return True

    def reply(self, session, emit, cancel):
        emit("tool.started", {"name": "search_questions"})
        result = f"{session['candidate']}，请介绍你的项目。"
        for text in result:
            emit("reply.delta", {"text": text})
        return result

    def report(self, session, emit, cancel, directory):
        emit("report.progress", {"stage": "汇总"})
        return {"overall": 8, "summary": "离线测试报告", "dimension_scores": {},
                "strengths": [], "weaknesses": [], "recommendations": [],
                "source": [m["text"] for m in session["messages"] if m["status"] == "completed"]}


def command(action, text="", request_id=None):
    return dict(action=action, text=text, request_id=request_id or uuid.uuid4().hex)


def wait_done(service, session_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        session = service.store.get(session_id)
        if not session["active_turn"]:
            return session
        time.sleep(0.01)
    pytest.fail("Turn did not complete")


def ws_done(ws):
    for _ in range(150):
        event = ws.receive_json()
        if event["type"] == "session.snapshot" and not event["data"]["active_turn"]:
            return event["data"]
    pytest.fail("No terminal snapshot")


def test_web_interview_and_report_reconnect(tmp_path):
    app = create_app(tmp_path, FakeAgent())
    with TestClient(app) as client:
        assert client.get("/api/health").json()["speech"]["asr"] is False
        s = client.post("/api/sessions", json={"candidate": "小明", "role": "工程师"}).json()
        url = f"/api/sessions/{s['id']}"
        with client.websocket_connect(url + "/ws") as ws:
            assert ws.receive_json()["type"] == "session.snapshot"
            ws.send_json(command("start"))
            state = ws_done(ws)
            assert "小明" in state["messages"][-1]["text"]
            answer = command("answer", "我做了一个检索项目。")
            ws.send_json(answer)
            ws_done(ws)
        with client.websocket_connect(url + "/ws") as ws:
            state = ws.receive_json()["data"]
            assert sum(m["role"] == "user" for m in state["messages"]) == 1
            ws.send_json(answer)  # reconnect must not execute twice
            state = ws_done(ws)
            assert sum(m["role"] == "user" for m in state["messages"]) == 1
            ws.send_json(command("finish"))
            state = ws_done(ws)
            assert state["status"] == "completed"
        assert "我做了一个检索项目。" in client.get(url + "/report").json()["source"]
    with TestClient(create_app(tmp_path, FakeAgent())) as client:
        assert client.get(url).json()["report"]["overall"] == 8


def test_sessions_do_not_share_transcript(tmp_path):
    service = InterviewService(tmp_path, FakeAgent())
    try:
        first = service.create("甲", "A", "")
        second = service.create("乙", "B", "")
        for session in (first, second):
            service.submit(session["id"], **command("start"))
        a, b = [wait_done(service, s["id"]) for s in (first, second)]
        assert "乙" not in a["messages"][0]["text"]
        assert "甲" not in b["messages"][0]["text"]
        events = service.store.events(a["id"], 0)
        assert len({e["seq"] for e in events}) == len(events)
        assert all(e["session_id"] == a["id"] for e in events)
    finally:
        service.close()


def test_cancel_discards_late_output_and_retries(tmp_path):
    started, release = threading.Event(), threading.Event()

    class BlockingAgent(FakeAgent):
        calls = 0

        def reply(self, session, emit, cancel):
            self.calls += 1
            if self.calls == 1:
                emit("reply.delta", {"text": "半句"})
                started.set()
                assert release.wait(5)
                emit("reply.delta", {"text": "不应出现"})
            return super().reply(session, emit, cancel)

    service = InterviewService(tmp_path, BlockingAgent())
    try:
        session = service.create("甲", "AI", "")
        sid = session["id"]
        service.submit(sid, **command("start"))
        assert started.wait(5)
        with pytest.raises(ValueError, match="仍在处理"):
            service.submit(sid, **command("start"))
        service.submit(sid, **command("cancel"))
        service.submit(sid, **command("retry"))
        release.set()
        state = wait_done(service, sid)
        assert state["messages"][0]["status"] == "interrupted"
        assert state["messages"][0]["text"] == "半句"
        assert state["messages"][-1]["status"] == "completed"
    finally:
        release.set()
        service.close()


def test_retry_does_not_duplicate_answer(tmp_path):
    class FailOnce(FakeAgent):
        calls = 0

        def reply(self, session, emit, cancel):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("mock outage")
            return super().reply(session, emit, cancel)

    service = InterviewService(tmp_path, FailOnce())
    try:
        sid = service.create("甲", "AI", "")["id"]
        service.submit(sid, **command("start"))
        wait_done(service, sid)
        service.submit(sid, **command("answer", "唯一的回答"))
        assert wait_done(service, sid)["error"]
        service.submit(sid, **command("retry"))
        state = wait_done(service, sid)
        assert sum(m["role"] == "user" for m in state["messages"]) == 1
        assert state["error"] is None
    finally:
        service.close()


def test_restart_marks_partial_turn_interrupted(tmp_path):
    service = InterviewService(tmp_path, FakeAgent())
    s = service.create("甲", "AI", "")
    s.update(active_turn="old-turn", status="running", last_action="start")
    service._add_message(s, "old-turn", "assistant", "未完成", "streaming")
    service.store.save(s)
    service.close()
    service = InterviewService(tmp_path, FakeAgent())
    try:
        restored = service.store.get(s["id"])
        assert restored["active_turn"] is None
        assert restored["messages"][0]["status"] == "interrupted"
        assert restored["error"]
    finally:
        service.close()


def test_web_validates_inputs_and_origin(tmp_path):
    with TestClient(create_app(tmp_path, FakeAgent())) as client:
        assert client.post("/api/sessions", json={"candidate": " ", "role": "AI"}).status_code == 422
        assert client.post("/api/sessions", headers={"origin": "https://unrelated.example"},
                           json={"candidate": "甲", "role": "AI"}).status_code == 403
        assert client.get("/api/sessions/unknown").status_code == 404
        sid = client.post("/api/sessions", json={"candidate": "甲", "role": "AI"}).json()["id"]
        with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
            ws.receive_json()
            for invalid in (command("finish"), command("answer", ""), {"action": "audio.chunk", "request_id": "12345678"}):
                ws.send_json(invalid)
                assert ws.receive_json()["type"] == "command.error"
        assert client.get(f"/api/sessions/{sid}/report").status_code == 404


def test_unconfigured_server_still_serves_history(tmp_path):
    adapter = FakeAgent()
    adapter.configured = lambda: False
    with TestClient(create_app(tmp_path, adapter)) as client:
        assert client.get("/api/health").json()["configured"] is False
        sid = client.post("/api/sessions", json={"candidate": "甲", "role": "AI"}).json()["id"]
        with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
            ws.receive_json()
            ws.send_json(command("start"))
            assert "ANTHROPIC_API_KEY" in ws.receive_json()["data"]["message"]


def test_isolated_agent_stream_keeps_cli_globals_untouched(iso, monkeypatch, capsys):
    class Stream:
        text_stream = iter(["你好", "，请介绍自己。"])

        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_final_message(self):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="你好，请介绍自己。")], stop_reason="end_turn")

    client = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs: Stream()))
    monkeypatch.setattr(iso, "trigger_hooks", lambda *args: pytest.fail("CLI hooks must not run"))
    monkeypatch.setattr(iso, "_system_with_memories", lambda *args: pytest.fail("CLI memory must not run"))
    events = []
    result = iso.agent_loop([{"role": "user", "content": "开始"}], tools=[iso.add.to_dict()],
                            handlers={"add": iso.add.call}, isolated=True, api_client=client,
                            event_sink=lambda name, data: events.append((name, data)))
    assert result == "你好，请介绍自己。"
    assert [data["text"] for name, data in events] == ["你好", "，请介绍自己。"]
    assert capsys.readouterr().out == ""
    assert iso.session_history == []


def test_agent_cancellation_before_model_call(iso):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        iso.agent_loop([], isolated=True, cancel_event=cancel)


def test_isolated_mode_requires_explicit_tool_scope(iso):
    with pytest.raises(ValueError, match="白名单"):
        iso.agent_loop([], isolated=True)


def test_sdk_error_details_are_not_exposed():
    error = type("AuthenticationError", (Exception,), {})("secret-key-must-not-leak")
    assert "401" in public_error(error)
    assert "secret-key" not in public_error(error)


def test_web_report_parses_all_chunks(iso, monkeypatch):
    monkeypatch.setattr(iso, "_split_transcript", lambda text: [f"chunk-{i}" for i in range(19)])
    parsed = []

    class Context:
        def phase(self, title): pass
        def parallel(self, thunks):
            assert len(thunks) <= iso.WORKFLOW_MAX_PARALLEL
            return [fn() for fn in thunks]
        def agent(self, prompt, schema, label):
            if label.startswith("parse"):
                parsed.append(label)
                return {"qa": [{"question": label, "answer": "答"}]}
            return {"overall": 8}
        def pipeline(self, batch, stage): return list(batch)

    report = iso._interview_report(Context(), {"transcript": "完整记录", "include_all": True})
    assert len(parsed) == 19
    assert len(report["per_question"]) == 19


def test_report_excludes_unanswered_last_question_and_partial_reply():
    def message(role, text, status="completed"):
        return {"role": role, "text": text, "status": status}
    transcript = answered_transcript([
        message("assistant", "问题一"), message("user", "回答一"),
        message("assistant", "不完整追问", "interrupted"), message("user", "补充说明"),
        message("assistant", "问题二"), message("user", "不会，跳过"),
        message("assistant", "结束前还未回答的问题"),
    ])
    assert "回答一\n补充说明" in transcript
    assert "不会，跳过" in transcript  # Explicit skips still count as an answer.
    assert "不完整追问" not in transcript
    assert "结束前还未回答的问题" not in transcript
