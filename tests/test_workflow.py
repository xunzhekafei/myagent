"""Workflow 运行时测试：schema 校验、稳定键、journal、pipeline 编排、续跑缓存。"""
import json


def test_json_schema_validation():
    import agent
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object",
                      "properties": {"n": {"type": "number"}}, "required": ["n"]}},
            "level": {"type": "string", "enum": ["high", "low"]},
        },
        "required": ["items"],
    }
    assert agent._validate_json_schema({"items": [], "level": "high"}, schema) is None
    assert agent._validate_json_schema({}, schema)                       # 缺必需字段
    assert agent._validate_json_schema({"items": [{"n": "x"}]}, schema)  # 类型错误
    assert agent._validate_json_schema({"items": [], "level": "mid"}, schema)  # 枚举外
    assert agent._validate_json_schema("不是对象", schema)


def test_stable_call_key_deterministic():
    import agent
    schema = {"type": "object"}
    k1 = agent._stable_call_key("agent", "audit:正确性", "检查这段", schema)
    k2 = agent._stable_call_key("agent", "audit:正确性", "检查这段", schema)
    k3 = agent._stable_call_key("agent", "audit:安全性", "检查这段", schema)
    assert k1 == k2 and k1 != k3
    # 与 schema 的键序无关（sort_keys）
    assert agent._stable_call_key("agent", "l", "p", {"b": 1, "a": 2}) == \
        agent._stable_call_key("agent", "l", "p", {"a": 2, "b": 1})


def test_workflow_meta_validation():
    import agent
    assert agent._validate_workflow_meta({"name": "ok-name", "description": "d"})
    for bad in ({"description": "d"}, {"name": "ok"}, {"name": "有中文名", "description": "d"},
                {"name": "ok", "description": "d", "phases": [1]}):
        try:
            agent._validate_workflow_meta(bad)
            assert False, f"应当拒绝：{bad}"
        except agent.WorkflowInputError:
            pass


def test_journal_record_and_reload(iso):
    import agent
    path = agent.RUNTIME_DIR / "wf_test.journal.jsonl"
    journal = agent.WorkflowJournal(path)
    journal.record("agent-1", {"x": 1})
    journal.close()
    reloaded = agent.WorkflowJournal(path)
    assert reloaded.cached("agent-1") == {"x": 1}
    assert reloaded.cached("agent-missing") is agent._MISS
    reloaded.close()


def _stub_runner(calls):
    def stub(prompt, schema, label, stats):
        calls.append(label)
        stats["agents"] += 1
        if label.startswith("audit:"):
            dimension = label.split(":", 1)[1]
            return {"findings": [{"title": f"{dimension}问题", "detail": "细节",
                                  "severity": "high"}]}
        return {"isReal": True, "reason": "验证通过"}
    return stub


def test_review_changes_pipeline_and_resume(iso, monkeypatch):
    import agent
    calls: list = []
    monkeypatch.setattr(agent, "_workflow_agent_call", _stub_runner(calls))

    out1 = json.loads(agent.run_workflow.call(
        {"name": "review-changes", "args": {"changes": "被审查的代码"}}))
    assert out1["status"] == "completed"
    assert out1["agents"] == 4                     # 2 维度审计 + 2 条验证
    confirmed = out1["result"]["confirmed"]
    assert len(confirmed) == 2
    assert {f["dimension"] for f in confirmed} == {"正确性", "安全性"}
    assert len(calls) == 4

    # 续跑：调用内容没变 → 全部命中 journal，零真实调用
    out2 = json.loads(agent.run_workflow.call(
        {"name": "review-changes", "args": {"changes": "被审查的代码"},
         "resume_from_run_id": out1["run_id"]}))
    assert out2["agents"] == 0
    assert len(calls) == 4                          # 没有再调用子 agent
    assert out2["result"] == out1["result"]

    # 改动输入 → 缓存不再命中，重新调用
    out3 = json.loads(agent.run_workflow.call(
        {"name": "review-changes", "args": {"changes": "改过的代码"},
         "resume_from_run_id": out1["run_id"]}))
    assert len(calls) == 8


def test_workflow_input_errors(iso):
    import agent
    assert "未知工作流" in agent.run_workflow.call({"name": "nope"})
    assert "args.changes" in agent.run_workflow.call(
        {"name": "review-changes", "args": {}})
    assert "找不到运行记录" in agent.run_workflow.call(
        {"name": "review-changes", "args": {"changes": "x"}, "resume_from_run_id": "wf_dead"})


def test_parallel_barrier_and_pipeline_order(iso):
    import agent
    journal = agent.WorkflowJournal(agent.RUNTIME_DIR / "wf_order.journal.jsonl")
    task = agent.WorkflowTask("wf_order", "test")
    ctx = agent.WorkflowContext(journal, task, agent_call=lambda *a: None)

    ctx.parallel([lambda: 1, lambda: 2, lambda: 3])
    results = ctx.pipeline([10, 20], lambda v, item, i: v + 1, lambda v, item, i: v * 2)
    assert results == [22, 42]                     # 每个 item 独立走完两个 stage
    journal.close()

    # 并发上限保护
    try:
        ctx.parallel([lambda: 1] * (agent.WORKFLOW_MAX_PARALLEL + 1))
        assert False
    except agent.WorkflowInputError:
        pass
