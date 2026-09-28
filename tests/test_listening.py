"""中途打断的离线测试。

不加载真实模型：注入假识别器和假判断。这里验证的是**闸门、锚点和落库**——
也就是「面试官会不会在不该插话的时候插话」。
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

import backend.listening as listening
from backend.app import create_app
from backend.listening import ListenManager, ListenSession
from backend.service import InterviewService
from backend.speech import Transcript


class FakeAdapter:
    """既当 adapter 又当判断器——把两件事写在一起，测试里看得清楚。"""

    def __init__(self, verdict=None):
        self.verdict = verdict          # 判断器要返回的打断语，None = 不打断
        self.judged = []                # 每次都记下被判断的文本
        self.contexts = []              # 以及给判断器的上下文

    def configured(self):
        return True

    def reply(self, session, emit, cancel, directory=None):
        return "好的"

    def report(self, session, emit, cancel, directory):
        return {"overall": 8}

    def judge_interrupt(self, partial, pressure, context):
        self.judged.append((partial, pressure))
        self.contexts.append(context)
        return self.verdict


class FakeRecognizer:
    """按顺序吐出预设文本，模拟「说得越来越多」。"""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    def ready(self):
        return True

    async def transcribe(self, audio, *, mime_type):
        self.calls += 1
        text = self.texts.pop(0) if self.texts else ""
        yield Transcript(text, True, f"u{self.calls}")


def wait_for(predicate, timeout=5.0, what="条件"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    pytest.fail(f"等不到{what}")


@pytest.fixture
def fast(monkeypatch):
    """把 8 秒 tick 和几十秒冷缩成毫秒级，让测试跑得动。"""
    monkeypatch.setattr(listening, "JUDGE_INTERVAL", 0.05)
    monkeypatch.setattr(listening, "MIN_AUDIO_BYTES", 1)
    monkeypatch.setattr(listening, "MAX_SECONDS", 30.0)
    monkeypatch.setattr(listening, "GATES", {
        "温和": {"min_chars": 10, "min_new": 5, "cooldown": 0.0, "max_interrupts": 1},
        "标准": {"min_chars": 10, "min_new": 5, "cooldown": 0.0, "max_interrupts": 2},
        "压力": {"min_chars": 10, "min_new": 5, "cooldown": 0.0, "max_interrupts": 3},
    })


def make_service(tmp_path, adapter):
    service = InterviewService(tmp_path, adapter)
    session = service.create("甲", "AI", "")
    service.submit(session["id"], action="start", request_id="a" * 8)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not service.store.get(session["id"])["active_turn"]:
            break
        time.sleep(0.01)
    return service, service.store.get(session["id"])


def anchor_of(session):
    return session["messages"][-1]["id"]


# ---------- 闸门：不该插话的时候别插 ----------

def test_gate_requires_enough_text(fast):
    listen = ListenSession("s", "a", "标准", service=None, recognizer=None, judge=None)
    assert not listen._passes_gates("太短了。")                       # 没到最少字数


def test_gate_requires_a_sentence_end(fast):
    """8 秒的中文多半停在句子中间——在那里打断正是功能显得坏掉的原因。"""
    listen = ListenSession("s", "a", "标准", service=None, recognizer=None, judge=None)
    assert not listen._passes_gates("我觉得这个方案其实还有一点可以优化的地方")   # 没有句末标点
    assert listen._passes_gates("我觉得这个方案还有优化空间。")                  # 句子讲完了


def test_gate_requires_new_content(fast):
    listen = ListenSession("s", "a", "标准", service=None, recognizer=None, judge=None)
    assert listen._passes_gates("这句话已经够长了，应该可以通过闸门。")
    listen.judged_text = "这句话已经够长了，应该可以通过闸门。"                  # 已经判断过这段
    assert not listen._passes_gates("这句话已经够长了，应该可以通过闸门。")       # 没有新增


def test_gate_respects_per_question_limit(fast):
    listen = ListenSession("s", "a", "温和", service=None, recognizer=None, judge=None)
    text = "这是一句足够长的回答，用来测试上限。"
    assert listen._passes_gates(text)
    listen.interrupts = 1                                            # 温和档最多 1 次
    assert not listen._passes_gates(text + "又补充了一些内容。")


# ---------- 判断失败一律不打断 ----------

def test_judge_failure_never_interrupts(fast):
    def boom(partial, pressure):
        raise RuntimeError("模型挂了")

    listen = ListenSession("s", "a", "标准", service=None, recognizer=None, judge=boom)
    assert listen._ask_judge("随便什么") is None


# ---------- 端到端：判断说打断，就真的落库 ----------

def test_interrupt_records_both_messages(tmp_path, fast):
    adapter = FakeAdapter(verdict="等一下，你说用了 Flink——窗口的 key 是怎么设计的？")
    service, session = make_service(tmp_path, adapter)
    recognizer = FakeRecognizer(["我先说说我做的项目。", "我先说说我做的项目。用了混合检索。"])

    listen = ListenSession(session["id"], anchor_of(session), "标准",
                           service=service, recognizer=recognizer, judge=adapter.judge_interrupt)
    listen.append(b"x" * 100)
    listen.start()
    wait_for(lambda: listen.stopped_reason == "已打断", what="打断")

    stored = service.store.get(session["id"])
    assert stored["messages"][-2]["role"] == "user"
    # 落库的那半截必须**正是判断时看到的那段**——不是更早的、也不是更晚的
    assert stored["messages"][-2]["text"] == adapter.judged[-1][0]
    assert stored["messages"][-2]["input_mode"] == "voice"
    assert stored["messages"][-2]["interrupted"] is True   # 评分要知道它天生不完整
    assert stored["messages"][-1]["role"] == "assistant"
    assert "Flink" in stored["messages"][-1]["text"]
    service.close()


def test_interrupt_does_not_set_error(tmp_path, fast):
    """设了 error 前端就会渲染「重试」按钮，一点会拿旧回答重放上一轮。"""
    adapter = FakeAdapter(verdict="停一下，先解释这个。")
    service, session = make_service(tmp_path, adapter)
    recognizer = FakeRecognizer(["一个足够长的回答，带句号。"])

    listen = ListenSession(session["id"], anchor_of(session), "标准",
                           service=service, recognizer=recognizer, judge=adapter.judge_interrupt)
    listen.append(b"x" * 100)
    listen.start()
    wait_for(lambda: listen.stopped_reason == "已打断", what="打断")

    assert service.store.get(session["id"])["error"] is None
    service.close()


def test_interrupt_reports_an_event(tmp_path, fast):
    adapter = FakeAdapter(verdict="停一下，这个数字怎么来的？")
    service, session = make_service(tmp_path, adapter)
    recognizer = FakeRecognizer(["一个足够长的回答，带句号。"])

    listen = ListenSession(session["id"], anchor_of(session), "标准",
                           service=service, recognizer=recognizer, judge=adapter.judge_interrupt)
    listen.append(b"x" * 100)
    listen.start()
    wait_for(lambda: listen.stopped_reason == "已打断", what="打断")

    events = service.store.events(session["id"], 0)
    assert any(e["type"] == "interview.interrupted" for e in events)
    service.close()


# ---------- 锚点：别把半截回答算到下一题上 ----------

def test_interrupt_dropped_when_anchor_is_stale(tmp_path, fast):
    """候选人已经答完、模型已经问了下一题——此时插话会让配对全乱。"""
    adapter = FakeAdapter(verdict="停一下。")
    service, session = make_service(tmp_path, adapter)
    anchor = anchor_of(session)

    # 模拟「已经翻到下一题」：锚点后面又来了候选人消息和面试官消息
    service.submit(session["id"], action="answer", text="答完了", request_id="b" * 8)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not service.store.get(session["id"])["active_turn"]:
            break
        time.sleep(0.01)

    ok = service.interrupt_with(session["id"], "半截回答", "停一下？", anchor)
    assert ok is False
    stored = service.store.get(session["id"])
    assert stored["messages"][-1]["role"] == "assistant"        # 没有多出两条
    assert "半截回答" not in [m["text"] for m in stored["messages"]]
    service.close()


def test_interrupt_dropped_while_a_turn_is_running(tmp_path, fast):
    adapter = FakeAdapter(verdict="停一下。")
    service, session = make_service(tmp_path, adapter)
    anchor = anchor_of(session)
    live = service.store.get(session["id"])
    live["active_turn"] = "someone-else"
    service.store.save(live)

    assert service.interrupt_with(session["id"], "半截", "停一下？", anchor) is False
    service.close()


def test_interrupt_dropped_on_completed_session(tmp_path, fast):
    adapter = FakeAdapter()
    service, session = make_service(tmp_path, adapter)
    anchor = anchor_of(session)
    live = service.store.get(session["id"])
    live["status"] = "completed"
    service.store.save(live)

    assert service.interrupt_with(session["id"], "半截", "停一下？", anchor) is False
    service.close()


# ---------- 判断器的上下文与代码侧兜底 ----------

def test_context_carries_role_and_recent_pairs(tmp_path, fast):
    adapter = FakeAdapter()
    service, session = make_service(tmp_path, adapter)
    recognizer = FakeRecognizer(["一个足够长的回答，带句号。"])

    listen = ListenSession(session["id"], anchor_of(session), "标准",
                           service=service, recognizer=recognizer, judge=adapter.judge_interrupt)
    listen.append(b"x" * 100)
    listen.start()
    wait_for(lambda: adapter.contexts, what="一次判断")

    context = adapter.contexts[0]
    assert context["role"] == "AI"
    assert context["question"] == session["messages"][-1]["text"]
    assert context["recent"] == []
    service.close()


def test_skill_section_extracts_only_what_the_judge_needs():
    """整份技能塞进判断 prompt 会让它回「请继续」——那条碎片输入规则。"""
    from backend.agent_adapter import _skill_section
    skill = ("## 压力档位\n档位说明\n### 温和\n不主动加压\n\n"
             "## 语言与语气\n- 碎片输入：回一句「请继续」\n\n"
             "## 追问工具箱\n按 Claim 追\n")
    tiers = _skill_section(skill, "## 压力档位")
    assert "### 温和" in tiers and "碎片输入" not in tiers
    assert "请继续" not in _skill_section(skill, "## 追问工具箱")
    assert _skill_section(skill, "## 不存在的节") == ""


def test_interrupted_answer_is_marked_in_transcript():
    """被打算断的回答要能看出「说到哪被打断了」——评分才知道没答完不是他的问题。"""
    import agent
    from backend.agent_adapter import answered_transcript

    transcript = answered_transcript([
        {"role": "assistant", "text": "请自我介绍", "status": "completed", "input_mode": "text"},
        {"role": "user", "text": "我做的是检索增强。", "status": "completed",
         "input_mode": "voice", "interrupted": True},
        {"role": "assistant", "text": "停一下，先解释这个。", "status": "completed",
         "input_mode": "text"},
    ])
    assert f"候选人（{agent.VOICE_MARK}·{agent.INTERRUPT_MARK}）" in transcript
    assert "我做的是检索增强。" in transcript


def test_plain_answers_carry_no_interrupt_mark():
    import agent
    from backend.agent_adapter import answered_transcript

    transcript = answered_transcript([
        {"role": "assistant", "text": "请自我介绍", "status": "completed", "input_mode": "text"},
        {"role": "user", "text": "手打的完整回答。", "status": "completed", "input_mode": "text"},
    ])
    assert agent.INTERRUPT_MARK not in transcript
    assert "候选人：手打的完整回答。" in transcript


def test_judge_system_drops_the_fragment_rule():
    """整份技能塞进去，判断最可能的输出之一就是「请继续」——那条规则必须被抽掉。"""
    from backend.agent_adapter import _judge_system

    system = _judge_system("AI", "标准", "（档位）", "（工具箱）")
    assert "请继续" not in system
    assert "绝大多数情况应该是 false" in system
    assert "打断一下" in system                       # 明确禁止元话语


def test_judge_screens_bad_model_output(monkeypatch):
    """打桩模型回复，验证**真正**的校验分支——prompt 里写了也拦不住。"""
    from types import SimpleNamespace
    import agent as agent_module
    from backend.agent_adapter import InterviewAgent

    reply = {"text": ""}

    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=reply["text"])])

    monkeypatch.setattr(agent_module, "client", SimpleNamespace(messages=FakeMessages()))
    judge = InterviewAgent().judge_interrupt
    context = {"role": "AI", "question": "请自我介绍", "recent": []}

    for bad in (
        '{"interrupt": false, "question": "这是什么？"}',   # 判断结果是「不打断」
        '{"interrupt": true, "question": "你继续说"}',      # 不是问句
        '{"interrupt": true, "question": "请继续？"}',      # 正是那条碎片规则的产物
        '{"interrupt": true}',                              # 少了 question
        "完全不是 JSON",
    ):
        reply["text"] = bad
        assert judge("说到一半的内容", "标准", context) is None, bad

    reply["text"] = '{"interrupt": true, "question": "这个数字怎么算的？"}'
    assert judge("说到一半的内容", "标准", context) == "这个数字怎么算的？"


# ---------- 管理器：一个场次只能有一个监听 ----------

def test_manager_replaces_existing_listen(tmp_path, fast):
    adapter = FakeAdapter()
    service, session = make_service(tmp_path, adapter)
    manager = ListenManager(service, FakeRecognizer([]), adapter.judge_interrupt)

    first = manager.start(session["id"], anchor_of(session), "标准")
    second = manager.start(session["id"], anchor_of(session), "标准")
    assert first.listen_id != second.listen_id
    assert not first.alive                       # 旧的被停掉了，否则两个判断线程抢着打断
    assert manager.get(session["id"], first.listen_id) is None
    assert manager.get(session["id"], second.listen_id) is second

    manager.close()
    service.close()


def test_manager_close_joins_threads(tmp_path, fast):
    adapter = FakeAdapter()
    service, session = make_service(tmp_path, adapter)
    manager = ListenManager(service, FakeRecognizer([]), adapter.judge_interrupt)
    listen = manager.start(session["id"], anchor_of(session), "标准")

    manager.close()
    assert not listen.alive
    service.close()


# ---------- 端点 ----------

def test_listen_endpoints_503_without_listener(tmp_path):
    """没有识别器（或 adapter 没有判断能力）时要说清楚，而不是 500。"""
    with TestClient(create_app(tmp_path, FakeAdapter(), recognizer=None)) as client:
        assert client.post("/api/sessions/x/listen").status_code == 503
        assert client.post("/api/sessions/x/listen/y/audio", content=b"1").status_code == 503
        assert client.delete("/api/sessions/x/listen/y").status_code == 503


def test_listen_endpoint_returns_id_and_rejects_unknown(tmp_path, fast):
    with TestClient(create_app(tmp_path, FakeAdapter(), recognizer=FakeRecognizer([]))) as client:
        sid = client.post("/api/sessions", json={"candidate": "甲", "role": "AI"}).json()["id"]
        # 还没有问题可答（最后一条不是面试官消息）→ 拒绝
        assert client.post(f"/api/sessions/{sid}/listen").status_code == 409

        service = client.app.state.service
        session = service.store.get(sid)
        service._add_message(session, "t1", "assistant", "请自我介绍", "completed")
        service.store.save(session)

        started = client.post(f"/api/sessions/{sid}/listen")
        assert started.status_code == 201
        listen_id = started.json()["listen_id"]

        assert client.post(f"/api/sessions/{sid}/listen/{listen_id}/audio",
                           content=b"x" * 100).json()["received"] == 100
        assert client.post(f"/api/sessions/{sid}/listen/nope/audio",
                           content=b"x").status_code == 404
        assert client.delete(f"/api/sessions/{sid}/listen/{listen_id}").json()["stopped"] is True
        # 停过之后再推音频就找不到了
        assert client.post(f"/api/sessions/{sid}/listen/{listen_id}/audio",
                           content=b"x").status_code == 404
