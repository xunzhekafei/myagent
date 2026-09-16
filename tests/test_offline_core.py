"""离线核心测试：cron 表达式、记忆预筛、权限闸门、MCP 命名、技能加载、文本工具。"""
import datetime

import pytest

from conftest import FakeBlock


# ---------- cron 表达式 ----------

@pytest.mark.parametrize("expr", [
    "* * * * *",
    "*/5 * * * *",
    "0 9 * * 1-5",
    "0,30 8,20 * * *",
    "15 3 1,15 * *",
])
def test_cron_valid(expr):
    assert FakeBlock  # 保持导入
    import agent
    assert agent.validate_cron(expr) is None


@pytest.mark.parametrize("expr", [
    "61 * * * *",     # 分钟越界
    "* * *",          # 段数不足
    "0 9 * * 7",      # 星期越界（0-6）
    "*/0 * * * *",    # 步长必须 > 0
    "a * * * *",      # 非数字
    "0 9 * * 5-2",    # 区间起点大于终点
])
def test_cron_invalid(expr):
    import agent
    assert agent.validate_cron(expr) is not None


def test_cron_matches_basics():
    import agent
    moment = datetime.datetime(2026, 9, 12, 9, 0)  # 周六
    assert agent.cron_matches("0 9 * * *", moment)
    assert not agent.cron_matches("5 9 * * *", moment)
    assert agent.cron_matches("*/5 * * * *", moment)
    nine_five = datetime.datetime(2026, 9, 12, 9, 5)
    assert agent.cron_matches("*/5 * * * *", nine_five)
    assert not agent.cron_matches("*/7 * * * *", nine_five)


def test_cron_weekday_sunday_is_zero():
    import agent
    sunday = datetime.datetime(2026, 9, 13, 10, 0)
    assert sunday.weekday() == 6
    assert agent.cron_matches("0 10 * * 0", sunday)   # cron 里 0=周日
    assert not agent.cron_matches("0 10 * * 6", sunday)


def test_cron_day_and_weekday_use_or_semantics():
    import agent
    moment = datetime.datetime(2026, 9, 12, 10, 0)  # 12 号（周六=6）
    assert agent.cron_matches("0 10 12 * 1", moment)   # 日匹配（或语义）
    assert agent.cron_matches("0 10 3 * 6", moment)    # 星期匹配
    assert not agent.cron_matches("0 10 3 * 1", moment)  # 都不匹配


# ---------- 记忆：中文切词与预筛 ----------

def test_text_terms_chinese_bigrams():
    import agent
    terms = agent._text_terms("你知道我的猫咪吗")
    assert "猫咪" in terms and "我的" in terms
    assert agent._text_terms("hello world_test") >= {"hello", "world_test"}


def test_catalog_overlaps_and_keyword_selection():
    import agent
    records = [{"filename": "milu-cat.md", "name": "milu-cat",
                "description": "用户养的猫叫咪噜，三花猫", "body": ""}]
    assert agent._catalog_overlaps(records, "你知道我的猫咪吗")
    assert not agent._catalog_overlaps(records, "今天天气如何")
    assert agent._keyword_selection(records, "咪噜是谁", 5) == ["milu-cat.md"]


def test_should_try_extract_gate():
    import agent
    def msg(text):
        return [{"role": "user", "content": text}]
    assert not agent._should_try_extract(msg("你的名字是什么？"))     # 问句：索取信息
    assert agent._should_try_extract(msg("我的猫咪是咪噜"))           # 关键词
    assert agent._should_try_extract(msg("这是一段很长的陈述" * 5))   # 长度
    assert not agent._should_try_extract(msg("你好呀"))               # 闲聊


def test_memory_document_roundtrip(iso):
    import agent
    doc = agent._memory_document("milu-cat", "user", "猫叫咪噜", "正文内容")
    metadata, body = agent._parse_memory_doc(doc)
    assert metadata["name"] == "milu-cat" and metadata["type"] == "user"
    assert body == "正文内容"


def test_read_file_paging(iso):
    """回归：大文件必须能分页读——之前没有 offset，模型只能反复整读，
    触发"压缩→指针→再读"死循环（面试报告演练事故）。"""
    import agent
    target = iso.WORKDIR / "big.txt"
    target.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")

    page1 = agent.read_file.call({"path": "big.txt", "limit": 4})
    assert "line0" in page1 and "line4" not in page1 and "offset=4" in page1
    page2 = agent.read_file.call({"path": "big.txt", "limit": 4, "offset": 4})
    assert "line4" in page2 and "line0" not in page2
    tail = agent.read_file.call({"path": "big.txt", "offset": 8})
    assert "line9" in tail and "文件末尾" in tail


def test_memory_write_list_rebuild(iso):
    import agent
    agent._write_memory("测试记忆", "project", "描述", "正文")
    records = agent._list_memories()
    assert len(records) == 1 and records[0]["type"] == "project"
    assert "测试记忆" in (agent.MEMORY_DIR / "MEMORY.md").read_text(encoding="utf-8")


# ---------- 权限三道闸门 ----------

def test_hard_deny_list():
    import agent
    result = agent.permission_hook(FakeBlock("bash", command="rm -rf /"))
    assert result and "硬拒绝" in result


def test_protected_files_hard_deny():
    import agent
    assert "核心文件" in agent.permission_hook(FakeBlock("bash", command="del agent.py"))
    assert "核心文件" in agent.permission_hook(FakeBlock("bash", command="echo x > agent.py"))
    assert "核心文件" in agent.permission_hook(FakeBlock("write_file", path="agent.py", content="x"))


def test_destructive_on_protected_variants():
    import agent
    assert agent._destructive_on_protected("del agent.py")
    assert agent._destructive_on_protected("move requirements.txt x")
    assert not agent._destructive_on_protected("type agent.py")        # 只读放行
    assert not agent._destructive_on_protected("del notes.txt")        # 普通文件仍走询问


def test_ask_path_deny_and_allow(iso, confirm_no):
    import agent
    denied = agent.permission_hook(FakeBlock("write_file", path="notes.txt", content="x"))
    assert denied and "拒绝" in denied


def test_ask_path_allow(iso, confirm_yes):
    import agent
    assert agent.permission_hook(FakeBlock("write_file", path="notes.txt", content="x")) is None
    assert agent.permission_hook(FakeBlock("bash", command="del notes.txt")) is None


def test_prompt_reads_from_queue(iso, feed):
    """交互确认走输入队列（回归：曾经 stdin 有两个消费者会互相抢输入）。"""
    import agent
    feed("y")
    assert agent.permission_hook(FakeBlock("write_file", path="notes.txt", content="x")) is None
    feed("n")
    assert "拒绝" in agent.permission_hook(FakeBlock("write_file", path="notes.txt", content="x"))


def test_archive_writes_are_auto_approved(iso, confirm_no):
    """存档类目录（面试记录/记忆/会话）的写入免确认——自动存档不需要手动同意。"""
    import agent
    assert agent.permission_hook(
        FakeBlock("write_file", path=".interviews/张三/20260101.json", content="y")) is None
    assert agent.permission_hook(
        FakeBlock("write_file", path=".memory/note.md", content="y")) is None
    assert "拒绝" in agent.permission_hook(          # 普通文件仍然要确认
        FakeBlock("write_file", path="notes.txt", content="x"))


def test_scheduled_turn_auto_denies(iso, monkeypatch):
    import agent
    monkeypatch.setattr(agent, "_SCHEDULED_TURN", True)
    result = agent.permission_hook(FakeBlock("bash", command="del notes.txt"))
    assert result and "自动拒绝" in result


def test_teammate_turn_bypasses_interactive(iso):
    import agent
    agent._tool_context.teammate = "alice"
    try:
        assert agent.permission_hook(FakeBlock("write_file", path="notes.txt", content="x")) is None
    finally:
        agent._tool_context.teammate = None


# ---------- MCP ----------

def test_mcp_name_normalization_and_collision():
    import agent
    assert agent._normalize_mcp_name("docs.one/get.version") == "docs_one_get_version"
    client = agent.MCPClient("x")
    assert client.register([{"name": "a" * 70, "description": ""}], {}) is not None  # 超长
    client2 = agent.MCPClient("y")
    error = client2.register(
        [{"name": "one/two", "description": ""}, {"name": "one_two", "description": ""}], {})
    assert error and "冲突" in error


def test_mcp_connect_and_pool(iso):
    import agent
    message = agent.connect_mcp.call({"name": "docs"})
    assert "mcp__docs__search" in message
    pool, handlers = agent._assemble_tool_pool(agent.TOOLS, agent.TOOL_HANDLERS)
    names = [t["name"] for t in pool]
    assert "mcp__docs__search" in names and "mcp__docs__get_version" in names
    assert len(pool) - 2 == len(agent.TOOLS)  # 不污染基础工具
    assert "MCP 错误" in handlers["mcp__docs__search"]({})  # 缺参数被兜住


def test_mcp_unknown_server(iso):
    import agent
    assert "未知的 MCP server" in agent.connect_mcp.call({"name": "nope"})


# ---------- 技能与工具表 ----------

def test_skill_loader(iso):
    import agent
    assert "code-review" in agent.SKILL_LOADER.skills
    assert "未知技能" in agent.SKILL_LOADER.load("不存在的技能")


def test_tool_registry_counts():
    import agent
    names = [t["name"] for t in agent.TOOLS]
    assert len(names) == len(set(names))          # 无重名
    assert "run_workflow" in names and "connect_mcp" in names
    assert "submit_plan" not in names             # 队友专用，不进主工具表
    assert "submit_plan" in [t["name"] for t in agent.TEAMMATE_TOOLS]
