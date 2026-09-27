"""语音转文字的离线测试。

不加载真实模型：注入假识别器。这里验证的是**协议与边界**——端点状态码、
能力声明、会话不被写入、错误不外泄。
"""
import time

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import create_app
from backend.service import InterviewService
from backend.speech import Transcript


class FakeRecognizer:
    """满足 speech.SpeechRecognizer 协议：async 生成器 + 一个 final 转写。"""

    def __init__(self, text="这是识别出来的文字", fail=False):
        self.text = text
        self.fail = fail
        self.ready_flag = False
        self.warmups = 0
        self.seen = []

    def ready(self):
        return self.ready_flag

    def warmup(self):
        self.warmups += 1
        self.ready_flag = True

    async def transcribe(self, audio, *, mime_type):
        payload = b"".join([chunk async for chunk in audio])
        self.seen.append((payload, mime_type))
        if self.fail:
            raise RuntimeError("内部细节不该外泄")
        yield Transcript(self.text, True, "utterance-1")


class FakeAgent:
    def configured(self):
        return True

    def reply(self, session, emit, cancel, directory=None):
        return "请介绍你的项目。"

    def report(self, session, emit, cancel, directory):
        return {"overall": 8}


def make_client(tmp_path, recognizer):
    app = create_app(tmp_path, FakeAgent(), recognizer=recognizer)
    return TestClient(app)


def new_session(client):
    return client.post("/api/sessions", json={"candidate": "小明", "role": "AI"}).json()["id"]


def wait_done(service, session_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        session = service.store.get(session_id)
        if not session["active_turn"]:
            return session
        time.sleep(0.01)
    pytest.fail("轮次没有结束")


def ws_done(ws):
    for _ in range(150):
        event = ws.receive_json()
        if event["type"] == "session.snapshot" and not event["data"]["active_turn"]:
            return event["data"]
    pytest.fail("没等到终态快照")


# ---------- 能力声明 ----------

def test_asr_is_false_when_recognizer_is_none(tmp_path):
    """显式关闭语音时能力声明必须是 False——这条断言必须与环境无关。

    如果 asr 由「faster_whisper 能否 import」决定，那么本机一装上语音依赖这条就会挂，
    而 CI（不装该依赖）照样绿：故障只在功能开始可用之后、且只在本机出现。
    """
    with make_client(tmp_path, None) as client:
        speech = client.get("/api/health").json()["speech"]
    assert speech["asr"] is False
    assert speech["ready"] is False
    assert speech["tts"] is False
    assert speech["input_modes"] == ["text"]


def test_web_speech_off_disables_capability(tmp_path, monkeypatch):
    """装了依赖也能显式关掉——不必去卸载。

    这里刻意不注入 recognizer，走 _AUTO 那条路（也就是真实用户的环境探测路径）。
    """
    monkeypatch.setenv("WEB_SPEECH", "off")
    with TestClient(create_app(tmp_path, FakeAgent())) as client:
        assert client.get("/api/health").json()["speech"]["asr"] is False


def test_web_speech_accepts_common_false_spellings(tmp_path, monkeypatch):
    for value in ("0", "false", "NO", " off "):
        monkeypatch.setenv("WEB_SPEECH", value)
        with TestClient(create_app(tmp_path, FakeAgent())) as client:
            assert client.get("/api/health").json()["speech"]["asr"] is False, value


def test_asr_is_true_with_injected_recognizer(tmp_path):
    with make_client(tmp_path, FakeRecognizer()) as client:
        speech = client.get("/api/health").json()["speech"]
    assert speech["asr"] is True
    assert speech["ready"] is False          # 有能力 ≠ 模型已驻留


def test_health_does_not_mutate_shared_capabilities(tmp_path):
    """CAPABILITIES 是模块级共享对象，改它会串到别的 create_app 上。"""
    from backend.speech import CAPABILITIES
    before = dict(CAPABILITIES)
    with make_client(tmp_path, FakeRecognizer()) as client:
        client.get("/api/health")
    assert CAPABILITIES == before


# ---------- 转写端点 ----------

def test_transcribe_returns_text_and_does_not_touch_session(tmp_path):
    recognizer = FakeRecognizer(text="我做过一个检索项目。")
    with make_client(tmp_path, recognizer) as client:
        sid = new_session(client)
        response = client.post(f"/api/sessions/{sid}/transcribe",
                               content=b"\x1aE\xdf\xa3fake-webm",
                               headers={"content-type": "audio/webm;codecs=opus"})
        assert response.status_code == 200
        assert response.json()["text"] == "我做过一个检索项目。"
        # 转写**不写入会话**：文字要回给前端由用户确认后再走 answer 提交
        assert client.get(f"/api/sessions/{sid}").json()["messages"] == []
    assert recognizer.seen[0][1] == "audio/webm"      # mime_type 去掉了 codecs 参数


def test_transcribe_rejects_unsupported_audio_type(tmp_path):
    with make_client(tmp_path, FakeRecognizer()) as client:
        sid = new_session(client)
        response = client.post(f"/api/sessions/{sid}/transcribe", content=b"1234",
                               headers={"content-type": "application/pdf"})
        assert response.status_code == 415


def test_transcribe_rejects_empty_body(tmp_path):
    with make_client(tmp_path, FakeRecognizer()) as client:
        sid = new_session(client)
        assert client.post(f"/api/sessions/{sid}/transcribe", content=b"").status_code == 400


def test_transcribe_rejects_oversized_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_AUDIO_BYTES", 32)
    with make_client(tmp_path, FakeRecognizer()) as client:
        sid = new_session(client)
        response = client.post(f"/api/sessions/{sid}/transcribe", content=b"x" * 64,
                               headers={"content-type": "audio/webm"})
        assert response.status_code == 413


def test_transcribe_requires_unknown_session(tmp_path):
    with make_client(tmp_path, FakeRecognizer()) as client:
        assert client.post("/api/sessions/nope/transcribe", content=b"1234").status_code == 404


def test_transcribe_disabled_returns_503(tmp_path):
    with make_client(tmp_path, None) as client:
        sid = new_session(client)
        assert client.post(f"/api/sessions/{sid}/transcribe", content=b"1234").status_code == 503


def test_transcribe_rejects_completed_session(tmp_path):
    with make_client(tmp_path, FakeRecognizer()) as client:
        sid = new_session(client)
        service = client.app.state.service
        session = service.store.get(sid)
        session["status"] = "completed"
        service.store.save(session)          # 要写回存储，只改本地 dict 不算数
        assert client.post(f"/api/sessions/{sid}/transcribe", content=b"1234").status_code == 409


def test_transcribe_failure_does_not_leak_details(tmp_path):
    with make_client(tmp_path, FakeRecognizer(fail=True)) as client:
        sid = new_session(client)
        response = client.post(f"/api/sessions/{sid}/transcribe", content=b"1234",
                               headers={"content-type": "audio/webm"})
        assert response.status_code == 500
        assert "内部细节不该外泄" not in response.text


# ---------- 预热 ----------

def test_warmup_loads_model_and_flips_ready(tmp_path):
    recognizer = FakeRecognizer()
    with make_client(tmp_path, recognizer) as client:
        assert client.get("/api/health").json()["speech"]["ready"] is False
        body = client.post("/api/speech/warmup").json()
        assert recognizer.warmups == 1
        assert body["asr"] is True and body["ready"] is True


def test_warmup_disabled_returns_503(tmp_path):
    with make_client(tmp_path, None) as client:
        assert client.post("/api/speech/warmup").status_code == 503


# ---------- 语音来源标记：让评分对术语拼写宽容 ----------

def _msg(role, text, status="completed", input_mode="text"):
    return {"role": role, "text": text, "status": status, "input_mode": input_mode}


def test_voice_answers_are_marked_in_transcript():
    """语音提交的回答要带标注——本地 ASR 会把术语听错，那不该算候选人说错。"""
    import agent
    from backend.agent_adapter import answered_transcript

    transcript = answered_transcript([
        _msg("assistant", "请自我介绍"),
        _msg("user", "我用 LightGBM 做建模", input_mode="voice"),
        _msg("assistant", "追问一下"),
        _msg("user", "这是手打的回答"),
    ])
    assert f"候选人（{agent.VOICE_MARK}）：我用 LightGBM 做建模" in transcript
    assert "候选人：这是手打的回答" in transcript
    assert f"候选人（{agent.VOICE_MARK}）：这是手打的回答" not in transcript


def test_voice_fragment_marks_the_whole_answer():
    """一条回答分几次提交时，只要有一段来自语音，整条都该标上。"""
    import agent
    from backend.agent_adapter import answered_transcript

    transcript = answered_transcript([
        _msg("assistant", "请继续"),
        _msg("user", "前半段是语音说的", input_mode="voice"),
        _msg("user", "后半段补打"),
    ])
    assert f"候选人（{agent.VOICE_MARK}）：" in transcript
    assert "后半段补打" in transcript


def test_submit_records_input_mode(tmp_path):
    service = InterviewService(tmp_path, FakeAgent())
    try:
        sid = service.create("甲", "AI", "")["id"]
        service.submit(sid, action="start", request_id="r" * 8)
        wait_done(service, sid)
        service.submit(sid, action="answer", text="语音说的", request_id="s" * 8,
                       input_mode="voice")
        session = wait_done(service, sid)
        modes = {m["role"]: m["input_mode"] for m in session["messages"]}
        assert modes["user"] == "voice"
        assert modes["assistant"] == "text"      # 面试官的话永远是文字
    finally:
        service.close()


def test_command_carries_input_mode_over_websocket(tmp_path):
    """走一遍真实指令解析——Command 没声明这个字段的话会被 pydantic 静默丢掉。"""
    with make_client(tmp_path, FakeRecognizer()) as client:
        sid = new_session(client)
        with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
            ws.receive_json()
            ws.send_json({"action": "start", "text": "", "request_id": "a" * 8})
            ws_done(ws)
            ws.send_json({"action": "answer", "text": "语音答的", "request_id": "b" * 8,
                          "input_mode": "voice"})
            state = ws_done(ws)
        user = [m for m in state["messages"] if m["role"] == "user"]
        assert user and user[-1]["input_mode"] == "voice"


def test_command_rejects_unknown_input_mode(tmp_path):
    with make_client(tmp_path, FakeRecognizer()) as client:
        sid = new_session(client)
        with client.websocket_connect(f"/api/sessions/{sid}/ws") as ws:
            ws.receive_json()
            ws.send_json({"action": "start", "text": "", "request_id": "c" * 8,
                          "input_mode": "telepathy"})
            assert ws.receive_json()["type"] == "command.error"


# ---------- 真实识别器（离线，不加载模型） ----------

def test_whisper_recognizer_build_is_import_safe():
    """无论本机装没装 faster-whisper，build_recognizer 都不能抛异常。"""
    from backend.whisper_asr import build_recognizer
    recognizer = build_recognizer()
    if recognizer is not None:
        assert recognizer.ready() is False        # 构建不该加载模型
        assert recognizer._load.__self__ is recognizer
