"""测试基础设施：把 agent 的所有状态目录指到临时目录，清空模块级全局。

离线测试不需要真实 API key；需要真实 API 的冒烟测试标了 slow（默认不跑）。
"""
import os
import pathlib
import queue
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-offline")  # 只在缺失时兜底，不影响 slow 测试

import agent  # noqa: E402


class FakeBlock:
    """模拟模型发来的 tool_use block（permission_hook 只需要 name 和 input）。"""

    def __init__(self, name: str, **inputs):
        self.name = name
        self.input = inputs


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离 fixture：所有产物目录进 tmp_path，所有模块级状态清空。"""
    # 目录常量（函数在调用时读模块全局，monkeypatch 即可生效）
    monkeypatch.setattr(agent, "WORKDIR", tmp_path)
    monkeypatch.setattr(agent, "WORKTREES_DIR", tmp_path / "worktrees")
    monkeypatch.setattr(agent, "MEMORY_DIR", tmp_path / "memory")
    monkeypatch.setattr(agent, "ARCHIVE_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(agent, "TOOL_RESULTS_DIR", tmp_path / "tool-results")
    monkeypatch.setattr(agent, "MAILBOX_DIR", tmp_path / "mailboxes")
    monkeypatch.setattr(agent, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(agent, "CRON_FILE", tmp_path / "scheduled_tasks.json")

    # 单例对象
    monkeypatch.setattr(agent, "TASKS", agent.TaskStore(tmp_path / "tasks"))
    monkeypatch.setattr(agent, "TODO", agent.TodoManager())
    monkeypatch.setattr(agent, "BACKGROUND", agent.BackgroundManager())
    monkeypatch.setattr(agent, "COMPACTOR", agent.ContextCompactor())

    # 模块级全局状态
    for name in ("teammate_assignments", "assignment_versions", "active_teammates",
                 "plan_gates", "plan_request_ids", "teammate_threads", "pending_requests",
                 "mcp_clients", "_mcp_tool_origins", "scheduled_jobs"):
        monkeypatch.setattr(agent, name, {})
    monkeypatch.setattr(agent, "cron_queue", [])
    monkeypatch.setattr(agent, "GOAL", None)
    monkeypatch.setattr(agent, "_SCHEDULED_TURN", False)

    # 交互确认走输入队列（测试里用 feed 注入答案）
    monkeypatch.setattr(agent, "_input_queue", queue.Queue())
    monkeypatch.setattr(agent, "_STDIN_READER_STARTED", True)

    yield agent

    # thread-local 清理（permission 的队友旁路会用到）
    agent._tool_context.teammate = None
    agent._tool_context.cwd = None


@pytest.fixture()
def feed(iso):
    """向交互确认队列注入答案：feed("y") / feed("n")（走真实的 _prompt_line 路径）。"""
    def _feed(*lines: str) -> None:
        for line in lines:
            iso._input_queue.put(line)
    return _feed


@pytest.fixture()
def confirm_yes(iso, monkeypatch):
    """让所有交互确认自动回答 y（不读队列，避免测试挂起）。"""
    monkeypatch.setattr(iso, "_prompt_line", lambda text: "y")


@pytest.fixture()
def confirm_no(iso, monkeypatch):
    """让所有交互确认自动回答 n。"""
    monkeypatch.setattr(iso, "_prompt_line", lambda text: "n")
