"""运行时组件：消息总线、后台任务、压缩管线、cron 调度状态机、目标循环。"""
import datetime
import time


# ---------- MessageBus ----------

def test_message_bus_send_read_destructive(iso):
    import agent
    agent.BUS.send("lead", "alice", "hello", "message")
    assert agent.BUS.peek("alice")
    messages = agent.BUS.read_inbox("alice")
    assert messages[0]["from"] == "lead" and messages[0]["content"] == "hello"
    assert not agent.BUS.peek("alice")          # 读取即删除


def test_message_bus_wait_timeout(iso):
    import agent
    start = time.monotonic()
    assert agent.BUS.wait_for_messages("nobody", 0.2) == []
    assert time.monotonic() - start < 3          # 超时返回而不是死等


def test_protocol_matching(iso):
    import agent
    request_id = agent._new_request_id()
    agent.pending_requests[request_id] = agent.ProtocolState(
        request_id, "shutdown", "lead", "alice", "pending", "")
    assert agent._match_response("shutdown_response", request_id, True, "alice", "lead")
    assert agent.pending_requests[request_id].status == "approved"
    assert not agent._match_response("shutdown_response", request_id, True, "alice", "lead")  # 重复
    assert not agent._match_response("plan_approval_response", "req_unknown", False, "x", "y")


# ---------- 后台任务 ----------

def test_background_manager_lifecycle(iso):
    import agent
    bg_id = agent.BACKGROUND.start('python -c "print(42)"', 30)
    assert agent.BACKGROUND.has_running()
    deadline = time.monotonic() + 20
    while agent.BACKGROUND.has_running() and time.monotonic() < deadline:
        time.sleep(0.1)
    ready = agent.BACKGROUND.collect()
    assert ready and ready[0][0] == bg_id and ready[0][1] == "completed"
    assert "42" in ready[0][2]
    assert not agent.BACKGROUND.has_running()


# ---------- 上下文压缩 ----------

def test_snip_compact_keeps_pairs_and_marks(iso):
    import agent
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                for i in range(60)]
    messages.append({"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]})
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "r"}]})

    out = agent.COMPACTOR.snip_compact(messages)
    assert len(out) == agent.MAX_MESSAGES
    assert out[-1]["content"][0]["type"] == "tool_result"   # 配对没被切开
    assert "已归档" in out[3]["content"]                     # 归档标记


def test_micro_compact_replaces_old_keeps_recent(iso):
    import agent
    big = "x" * 5000
    messages = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"old{i}",
                                      "content": big} for i in range(5)]},
        {"role": "assistant", "content": "看完"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "new1",
                                      "content": big * 2}]},
    ]
    agent.COMPACTOR.micro_compact(messages, 5000)
    old_blocks = messages[2]["content"]
    assert "[较早的工具结果已保存" in old_blocks[0]["content"]  # 最旧的被替换
    assert old_blocks[4]["content"] == big                      # 最近 3 条保留
    assert messages[4]["content"][0]["content"] == big * 2      # 未读的保持完整
    assert list(agent.TOOL_RESULTS_DIR.glob("old0.txt"))        # 被替换内容可恢复


def test_compactor_archive_is_reloadable_json(iso):
    """回归：压缩存档必须是可解析的消息 JSON（能被重新读回来重建对话），
    而不是 SDK 对象的 repr 字符串——否则面试记录被压缩后就找不回来了。"""
    import json as _json
    import agent
    messages = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
                for i in range(60)]
    agent.COMPACTOR.snip_compact(messages)
    archives = list(agent.ARCHIVE_DIR.glob("snip-*.txt"))
    assert archives
    loaded = _json.loads(archives[0].read_text(encoding="utf-8"))
    assert isinstance(loaded, list) and loaded[0] == {"role": "user", "content": "m0"}


def test_tool_result_budget_persists_huge_output(iso):
    import agent
    messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "big1", "content": "y" * 250000}]}]
    agent.COMPACTOR.tool_result_budget(messages)
    block = messages[0]["content"][0]
    assert "[完整结果已转存" in block["content"]
    assert len(block["content"]) < 5000                         # 只留预览
    assert list(agent.TOOL_RESULTS_DIR.glob("big1.txt"))        # 完整内容在磁盘上


# ---------- cron 调度 ----------

def test_cron_poll_ack_and_durable_reload(iso):
    import agent
    job = agent._schedule_job("* * * * *", "报时", recurring=True, durable=True)
    assert not isinstance(job, str)
    moment = datetime.datetime.now() + datetime.timedelta(minutes=1)

    agent.poll_due_jobs(moment)
    assert len(agent._consume_cron_queue()) == 1
    agent.poll_due_jobs(moment)                     # 同一分钟不重复入队
    assert agent._consume_cron_queue() == []
    assert agent.scheduled_jobs[job.id].pending_delivery

    agent._acknowledge_cron_jobs([job])             # 周期任务：解除待送达
    assert not agent.scheduled_jobs[job.id].pending_delivery

    assert agent.CRON_FILE.is_file()                # durable：落盘
    agent.scheduled_jobs.clear()
    agent._load_durable_jobs()
    assert job.id in agent.scheduled_jobs


def test_cron_one_shot_removed_after_delivery(iso):
    import agent
    job = agent._schedule_job("*/5 * * * *", "只跑一次", recurring=False, durable=False)
    agent._acknowledge_cron_jobs([job])
    assert job.id not in agent.scheduled_jobs


def test_cron_invalid_expression_rejected(iso):
    import agent
    assert isinstance(agent._schedule_job("61 * * * *", "x"), str)


# ---------- 目标循环 ----------

def test_goal_without_goal_passes(iso):
    import agent
    assert agent._goal_stop_hook([]) is None


def test_goal_block_then_complete(iso, monkeypatch):
    import agent
    agent.GOAL = agent.GoalState(condition="测试目标")
    monkeypatch.setattr(agent, "_goal_evaluate",
                        lambda m: {"ok": False, "reason": "证据不足", "impossible": False})
    forced = agent._goal_stop_hook([])
    assert forced and "证据不足" in forced and agent.GOAL.blocks == 1

    monkeypatch.setattr(agent, "_goal_evaluate",
                        lambda m: {"ok": True, "reason": "完成", "impossible": False})
    assert agent._goal_stop_hook([]) is None
    assert agent.GOAL.status == "completed"


def test_goal_impossible_not_faked(iso, monkeypatch):
    import agent
    agent.GOAL = agent.GoalState(condition="x")
    monkeypatch.setattr(agent, "_goal_evaluate",
                        lambda m: {"ok": False, "reason": "缺必要密钥", "impossible": True})
    assert agent._goal_stop_hook([]) is None
    assert agent.GOAL.status == "impossible"        # 保留目标、如实标记


def test_goal_evaluator_failure_stops_auto_continue(iso, monkeypatch):
    import agent
    agent.GOAL = agent.GoalState(condition="x")
    def boom(m):
        raise RuntimeError("API 挂了")
    monkeypatch.setattr(agent, "_goal_evaluate", boom)
    assert agent._goal_stop_hook([]) is None
    assert agent.GOAL.status == "active" and "失败" in agent.GOAL.last_reason


def test_goal_block_limit_returns_control(iso, monkeypatch):
    import agent
    agent.GOAL = agent.GoalState(condition="x")
    agent.GOAL.blocks = agent.GOAL_MAX_BLOCKS
    monkeypatch.setattr(agent, "_goal_evaluate",
                        lambda m: {"ok": False, "reason": "no", "impossible": False})
    assert agent._goal_stop_hook([]) is None
    assert agent.GOAL.status == "active"


def test_goal_commands(iso):
    import agent
    condition = agent._handle_goal_command("/goal 完成 X 直到测试通过")
    assert condition and "完成 X" in condition
    agent._handle_goal_command("/goal clear")
    assert agent.GOAL.status == "cleared"
    assert "没有目标" in agent._goal_status_text()


def test_summary_hook_never_prints_negative(iso, capsys, monkeypatch):
    """回归：压缩会让历史变短，工具计数曾出现负数（"使用了 -3 次工具调用"）。"""
    import agent
    messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "x"}]}]
    monkeypatch.setattr(agent, "_LAST_TOOL_COUNT", 5)   # 模拟压缩前的更大计数
    agent.summary_hook(messages)
    out = capsys.readouterr().out
    assert "次工具调用" not in out                      # 负增量不打印
    assert agent._LAST_TOOL_COUNT == 1                  # 基线已重置


# ---------- 队友自动旁路（回归：队友不能读用户输入） ----------

def test_teammate_workspace_tools_require_assignment(iso):
    import agent
    from conftest import FakeBlock
    result = agent._run_teammate_tool("alice", FakeBlock("read_file", path="x.txt"))
    assert "请先认领" in result
