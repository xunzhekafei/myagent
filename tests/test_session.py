"""会话持久化测试：序列化往返、原子保存、损坏容错、半截回合清理、/clear。"""
import json


class FakeSDKBlock:
    """模拟 SDK 的 block 对象（有 model_dump 的 pydantic 风格）。"""

    def __init__(self, block_type: str, **fields):
        self.type = block_type
        self._data = {"type": block_type, **fields}

    def model_dump(self, mode="json"):
        return dict(self._data)


class BrokenDumpBlock(FakeSDKBlock):
    """model_dump 抛错，应回退到 model_dump_json。"""

    def model_dump(self, mode="json"):
        raise ValueError("boom")

    def model_dump_json(self):
        return json.dumps(self._data)


def test_serialize_handles_all_content_shapes(iso):
    import agent
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": [
            FakeSDKBlock("text", text="hi"),
            FakeSDKBlock("thinking", thinking="…", signature="sig")]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "r"}]},
    ]
    out = agent._serialize_messages(messages)
    assert out[0]["content"] == "你好"
    assert out[1]["content"][0] == {"type": "text", "text": "hi"}
    assert out[1]["content"][1]["signature"] == "sig"     # thinking 块原样保留
    assert out[2]["content"][0]["type"] == "tool_result"   # dict 原样保留


def test_serialize_falls_back_on_dump_error(iso):
    import agent
    out = agent._serialize_messages([
        {"role": "assistant", "content": [BrokenDumpBlock("text", text="x")]}])
    assert out[0]["content"][0] == {"type": "text", "text": "x"}


def test_session_roundtrip(iso):
    import agent
    agent.session_history = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": [{"type": "text", "text": "回答"}]},
    ]
    agent._save_session()
    assert agent.SESSION_FILE.is_file()
    assert not (agent.SESSION_DIR / "latest.json.tmp").exists()  # 临时文件已替换
    assert agent._load_session() == agent.session_history


def test_save_skips_empty_history(iso):
    import agent
    agent.session_history = []
    agent._save_session()
    assert not agent.SESSION_FILE.exists()


def test_load_missing_or_corrupt(iso):
    import agent
    assert agent._load_session() == []            # 没有存档
    agent.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    agent.SESSION_FILE.write_text("这不是 JSON", encoding="utf-8")
    assert agent._load_session() == []            # 损坏 → 忽略，不阻塞启动


def test_sanitize_drops_dangling_tool_use(iso):
    import agent
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]},
    ]
    assert agent._sanitize_session(messages) == [{"role": "user", "content": "q"}]


def test_sanitize_drops_leading_non_user(iso):
    import agent
    messages = [
        {"role": "assistant", "content": "半截"},
        {"role": "user", "content": "q"},
    ]
    result = agent._sanitize_session(messages)
    assert result[0]["role"] == "user" and len(result) == 1


def test_clear_session_removes_archive(iso):
    import agent
    agent.session_history = [{"role": "user", "content": "q"}]
    agent._save_session()
    assert agent.SESSION_FILE.is_file()
    assert agent._clear_session() == []
    assert not agent.SESSION_FILE.exists()
