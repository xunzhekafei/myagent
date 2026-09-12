"""真实 API 冒烟测试：python -m pytest -m slow（会消耗 token，默认不跑）。

这些测试同时是回归保护：流式输出、工具轮、判断器解析都在这里走真实链路。
"""
import pytest

pytestmark = pytest.mark.slow


def test_tool_round_end_to_end(iso):
    """一次完整的工具轮：模型决定调用 add → 执行 → 结果回传 → 给出答案。"""
    import agent
    messages = []
    agent.ask("请用 add 工具算一下 1+1 等于多少？", messages)
    dumped = agent._dump(messages)
    assert '"add"' in dumped or "add" in dumped
    assert "tool_result" in dumped
    assert "2" in dumped


def test_goal_evaluator_real(iso):
    """独立判断器能读到对话里的证据并给出结构化判断。"""
    import agent
    agent.GOAL = agent.GoalState(condition="对话中出现了数字 42")
    verdict = agent._goal_evaluate([{"role": "user", "content": "计算结果是 42"}])
    assert isinstance(verdict.get("ok"), bool)
    assert verdict.get("reason")
