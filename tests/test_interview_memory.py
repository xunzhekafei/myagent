"""面试蓝图与结构化笔记的离线测试。

不调模型：更新逻辑用假的 _call 打桩，重点验证
「阶段不会越级」「单项目不硬凑对比」「渲染的权重符合衰减」。
"""
import pytest

import backend.interview_memory as memory_module
from backend.interview_memory import _enforce, _last_qa, _weight, empty, render, update


def messages(*pairs, trailing_assistant=False):
    result = []
    for index, (question, answer) in enumerate(pairs):
        result.append({"role": "assistant", "text": question, "status": "completed"})
        result.append({"role": "user", "text": answer, "status": "completed"})
    if trailing_assistant:
        result.append({"role": "assistant", "text": "", "status": "streaming"})
    return result


def project(name, dimensions=("归属", "指标"), turns=2, done=True, role="主导"):
    return {"name": name, "role_claim": role, "metrics": ["准确率 95%"],
            "tech": ["Milvus"], "dimensions_asked": list(dimensions),
            "open_threads": [], "turns": turns, "done": done}


# ---------- 衰减权重 ----------

def test_weight_decays_exponentially():
    assert _weight(0, 0.5) == 1.0
    assert _weight(1, 0.5) == 0.5
    assert _weight(2, 0.5) == 0.25
    assert _weight(3, 0.4) == pytest.approx(0.064)


# ---------- 阶段不能越级 ----------

def test_compare_requires_two_done_projects():
    state = _enforce({"projects": [project("A")], "phase": "compare",
                      "compare_points": ["A 主导，B 参与"]})
    assert state["phase"] == "deep_dive"
    assert state["compare_points"] == []


def test_compare_blocked_when_second_project_not_done():
    state = _enforce({"projects": [project("A"), project("B", dimensions=["归属"], done=False)],
                      "phase": "compare"})
    assert state["phase"] == "deep_dive"


def test_compare_allowed_when_both_done():
    state = _enforce({"projects": [project("A"), project("B", role="参与")],
                      "phase": "compare", "compare_points": ["A 说主导、B 说参与"]})
    assert state["phase"] == "compare"
    assert state["compare_points"] == ["A 说主导、B 说参与"]


def test_done_requires_two_dimensions():
    """模型可能把只问了一个维度的项目标成 done，代码要兜住。"""
    state = _enforce({"projects": [project("A", dimensions=["归属"], done=True)], "phase": "deep_dive"})
    assert state["projects"][0]["done"] is False


def test_intro_becomes_deep_dive_once_projects_exist():
    state = _enforce({"projects": [project("A")], "phase": "intro"})
    assert state["phase"] == "deep_dive"


# ---------- 渲染 ----------

def test_render_empty_returns_blank():
    assert render(None, []) == ""
    assert render(empty(), messages(("Q", "A"))) == ""


def test_render_keeps_latest_verbatim_and_weights_history():
    state = {**empty(), "projects": [project("知识库问答")],
             "phase": "deep_dive",
             "timeline": [{"question": "上一轮问题", "answer": "上一轮摘要"}]}
    text = render(state, messages(("现在的问题", "这是最新一轮的完整回答")), alpha=0.5)
    assert "这是最新一轮的完整回答" in text       # 最新一轮原文
    assert "权重 1.00" in text
    assert "权重 0.50" in text
    assert "上一轮摘要" in text
    assert "知识库问答" in text
    assert "不衰减" in text


def test_render_single_project_does_not_force_comparison():
    state = {**empty(), "projects": [project("唯一项目")], "phase": "deep_dive"}
    text = render(state, messages(("Q", "A")))
    assert "不要硬凑对比" in text


def test_render_compare_points_only_shown_in_compare_phase():
    state = {**empty(), "projects": [project("A"), project("B")],
             "phase": "deep_dive", "compare_points": ["A 主导、B 参与"]}
    assert "A 主导、B 参与" not in render(state, messages(("Q", "A")))

    state["phase"] = "compare"
    assert "A 主导、B 参与" in render(state, messages(("Q", "A")))


# ---------- 更新 ----------

def test_update_keeps_previous_when_model_fails(monkeypatch):
    def boom(_prompt):
        raise RuntimeError("模型挂了")
    monkeypatch.setattr(memory_module, "_call", boom)

    previous = {**empty(), "projects": [project("A")], "phase": "deep_dive"}
    session = {"memory": previous, "messages": messages(("Q", "A"))}
    assert update(session) is previous


def test_update_appends_timeline_and_cleans(monkeypatch):
    monkeypatch.setattr(memory_module, "_call", lambda _prompt: {
        "projects": [project("A")], "phase": "deep_dive",
        "compare_points": ["不该出现的对比"], "answer_summary": "他讲了检索链路"})

    session = {"memory": empty(), "messages": messages(("你怎么做的？", "先检索再重排"))}
    result = update(session)

    assert result["phase"] == "deep_dive"
    assert result["compare_points"] == []           # 只有一个项目，对比点必须清掉
    assert result["timeline"][-1]["answer"] == "他讲了检索链路"


def test_update_ignores_empty_answer(monkeypatch):
    called = []
    monkeypatch.setattr(memory_module, "_call", lambda p: called.append(p) or {})
    session = {"memory": empty(), "messages": []}
    update(session)
    assert called == []


def test_timeline_is_trimmed(monkeypatch):
    monkeypatch.setattr(memory_module, "_call", lambda _prompt: {
        "projects": [project("A")], "phase": "deep_dive",
        "compare_points": [], "answer_summary": "摘要"})
    state = {**empty(), "timeline": [{"question": f"Q{i}", "answer": "A"} for i in range(20)]}
    result = update({"memory": state, "messages": messages(("Q", "A"))})
    assert len(result["timeline"]) == memory_module.TIMELINE_KEEP


# ---------- 取最近一轮问答 ----------

def test_service_persists_memory_from_adapter(tmp_path):
    """adapter 在快照上写笔记，service 必须合并回权威会话——否则下一轮就丢了。"""
    import time
    from backend.service import InterviewService

    class MemoryAgent:
        def configured(self):
            return True

        def reply(self, session, emit, cancel, directory=None):
            session["memory"] = {**empty(), "phase": "deep_dive"}
            return "好的"

        def report(self, session, emit, cancel, directory):
            return {}

    service = InterviewService(tmp_path, MemoryAgent())
    session = service.create("甲", "AI", "")
    service.submit(session["id"], action="start", request_id="a" * 8)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not service.store.get(session["id"])["active_turn"]:
            break
        time.sleep(0.01)

    assert service.store.get(session["id"])["memory"]["phase"] == "deep_dive"
    service.close()


def test_last_qa_skips_trailing_streaming_assistant():
    """真实流程里快照末条是空的 assistant 流式占位，不能把它当成问题。"""
    question, answer = _last_qa(messages(("第一问", "第一答"), ("第二问", "第二答"),
                                         trailing_assistant=True))
    assert question == "第二问"
    assert answer == "第二答"


# ---------- 追问深度阶梯（动作由代码决定） ----------

def _focus_state(quality, depth=1, **extra):
    state = {"projects": [project("A")], "phase": "deep_dive",
             "focus": {"topic": "缓存一致性", "depth": depth, "quality": quality}}
    state.update(extra)
    return state


def test_ladder_deeper_on_strong_answer():
    result = _enforce(_focus_state(0.8), None, "标准")
    assert result["focus"]["next_action"] == "deeper"
    assert result["focus"]["weak_streak"] == 0


def test_ladder_angle_on_mediocre_answer():
    result = _enforce(_focus_state(0.5), None, "标准")
    assert result["focus"]["next_action"] == "angle"


def test_ladder_one_weak_answer_gets_another_angle():
    result = _enforce(_focus_state(0.2), None, "标准")
    assert result["focus"]["weak_streak"] == 1
    assert result["focus"]["next_action"] == "angle"


def test_ladder_switches_after_two_weak_answers():
    previous = {**empty(), "focus": {"weak_streak": 1}}
    result = _enforce(_focus_state(0.2), previous, "标准")
    assert result["focus"]["weak_streak"] == 2
    assert result["focus"]["next_action"] == "switch_topic"


def test_ladder_switches_at_depth_cap():
    # 标准档 cap=3；答得再好，到第 3 层也要换主题
    result = _enforce(_focus_state(0.9, depth=3), None, "标准")
    assert result["focus"]["next_action"] == "switch_topic"


def test_ladder_cap_follows_pressure_level():
    # 温和档 cap=2：第 2 层就换
    result = _enforce(_focus_state(0.9, depth=2), None, "温和")
    assert result["focus"]["next_action"] == "switch_topic"
    # 压力档 cap=4：第 2 层还能继续深入
    result = _enforce(_focus_state(0.9, depth=2), None, "压力")
    assert result["focus"]["next_action"] == "deeper"


def test_project_switch_wins_over_ladder():
    projects = [project("A"), project("B", dimensions=["归属"], done=False)]
    result = _enforce(_focus_state(0.9, projects=projects), None, "标准")
    assert result["focus"]["next_action"] == "switch_project"


def test_all_projects_done_moves_to_compare():
    result = _enforce(_focus_state(0.9, projects=[project("A"), project("B")]),
                      None, "标准")
    assert result["focus"]["next_action"] == "compare"


def test_render_shows_action_with_do_and_avoid():
    state = {**empty(), "projects": [project("A")], "phase": "deep_dive",
             "focus": {"topic": "缓存一致性", "depth": 2, "depth_cap": 3,
                       "quality": 0.8, "weak_streak": 0, "next_action": "deeper"}}
    text = render(state, messages(("Q", "A")))
    assert "本轮动作" in text
    assert "深入一层" in text
    assert "要做：" in text and "不要做：" in text
    assert "缓存一致性" in text
