"""本地模型接入的配置逻辑测试。

不加载模型：验证历史截断和上限的环境变量优先级。
"""
import backend.agent_adapter as adapter


def test_select_history_keeps_latest_and_respects_limit():
    messages = [{"role": "user", "text": "x" * 100} for _ in range(10)]
    assert len(adapter.select_history(messages, limit=250)) == 2


def test_select_history_preserves_order():
    messages = [{"role": "user", "text": str(index)} for index in range(5)]
    result = adapter.select_history(messages, limit=2)
    assert [message["content"] for message in result] == ["3", "4"]


def test_select_history_always_keeps_one_message():
    """单条消息本身就超限时，也必须带上——否则模型看不到当前问题。"""
    result = adapter.select_history([{"role": "user", "text": "x" * 100000}], limit=100)
    assert len(result) == 1


def test_history_limit_defaults_to_cloud_size(monkeypatch):
    monkeypatch.delenv("HISTORY_CHAR_LIMIT", raising=False)
    monkeypatch.delenv("LOCAL_MODEL", raising=False)
    assert adapter.history_limit() == 60000


def test_history_limit_shrinks_in_local_mode(monkeypatch):
    monkeypatch.delenv("HISTORY_CHAR_LIMIT", raising=False)
    monkeypatch.setenv("LOCAL_MODEL", "on")
    assert adapter.history_limit() == 12000


def test_history_limit_explicit_value_wins(monkeypatch):
    monkeypatch.setenv("LOCAL_MODEL", "on")
    monkeypatch.setenv("HISTORY_CHAR_LIMIT", "8000")
    assert adapter.history_limit() == 8000


def test_history_limit_ignores_invalid_value(monkeypatch):
    monkeypatch.setenv("LOCAL_MODEL", "on")
    monkeypatch.setenv("HISTORY_CHAR_LIMIT", "不是数字")
    assert adapter.history_limit() == 12000


def test_history_limit_has_a_floor(monkeypatch):
    """设得太小会让模型连上一轮都看不到，给个下限。"""
    monkeypatch.setenv("HISTORY_CHAR_LIMIT", "10")
    assert adapter.history_limit() == 2000
