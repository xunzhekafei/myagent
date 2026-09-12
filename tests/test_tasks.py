"""任务系统测试：原子认领、依赖解锁、owner 互斥、worktree 绑定。"""


def test_full_lifecycle_with_unblock(iso):
    import agent
    a = agent.TASKS.create("任务A")
    b = agent.TASKS.create("任务B")
    agent.TASKS.update_dependencies(b.id, [a.id])

    assert "被阻塞" in agent._claim_task_for(b.id, "alice")      # 依赖未完成
    assert agent._claim_task_for(a.id, "alice").startswith("已认领")
    result = agent._complete_task_for(a.id, "alice")
    assert "解锁了下游任务" in result and "任务B" in result
    assert agent._claim_task_for(b.id, "alice").startswith("已认领")  # 解锁后可认领


def test_claim_is_exclusive(iso):
    import agent
    t = agent.TASKS.create("独占任务")
    assert agent._claim_task_for(t.id, "alice").startswith("已认领")
    assert "不可认领" in agent._claim_task_for(t.id, "bob")       # 已被认领
    assert agent.TASKS.load(t.id).owner == "alice"


def test_owner_cannot_hold_two_tasks(iso):
    import agent
    t1 = agent.TASKS.create("第一个")
    t2 = agent.TASKS.create("第二个")
    agent._claim_task_for(t1.id, "alice")
    assert "先完成它" in agent._claim_task_for(t2.id, "alice")
    agent._complete_task_for(t1.id, "alice")
    assert agent._claim_task_for(t2.id, "alice").startswith("已认领")


def test_complete_requires_owner(iso):
    import agent
    t = agent.TASKS.create("任务")
    agent._claim_task_for(t.id, "alice")
    assert "不是 bob" in agent._complete_task_for(t.id, "bob")
    assert agent.TASKS.load(t.id).status == "in_progress"


def test_dependency_cycle_rejected(iso):
    import agent
    a = agent.TASKS.create("A")
    b = agent.TASKS.create("B")
    agent.TASKS.update_dependencies(b.id, [a.id])
    try:
        agent.TASKS.update_dependencies(a.id, [b.id])
        assert False, "环没有被拒绝"
    except ValueError as error:
        assert "环" in str(error)


def test_self_dependency_rejected(iso):
    import agent
    t = agent.TASKS.create("自依赖")
    try:
        agent.TASKS.update_dependencies(t.id, [t.id])
        assert False
    except ValueError as error:
        assert "自己" in str(error)


def test_worktree_binding_and_assignment_cwd(iso):
    import agent
    t = agent.TASKS.create("绑定目录的任务")
    message = agent.create_worktree.call({"name": "cfg", "task_id": t.id})
    assert "已为" in message
    assert agent.TASKS.load(t.id).worktree == "cfg"

    agent._claim_task_for(t.id, "alice")
    cwd, error = agent._assignment_cwd("alice")
    assert error is None and cwd.name == "cfg"
    assert cwd.parent == agent.WORKTREES_DIR


def test_worktree_missing_dir_fails_closed(iso):
    import agent
    t = agent.TASKS.create("目录丢失的任务")
    agent.create_worktree.call({"name": "gone", "task_id": t.id})
    (agent.WORKTREES_DIR / "gone").rmdir()
    assert "无法认领" in agent._claim_task_for(t.id, "alice")   # 失败不回落仓库目录
    assert agent.TASKS.load(t.id).status == "pending"


def test_claim_fails_when_no_assignment(iso):
    import agent
    cwd, error = agent._assignment_cwd("nobody")
    assert cwd is None and "请先认领" in error


def test_release_returns_task_to_board(iso):
    import agent
    t = agent.TASKS.create("被放弃的任务")
    agent._claim_task_for(t.id, "alice")
    agent._release_assignment("alice", return_to_board=True)
    task = agent.TASKS.load(t.id)
    assert task.status == "pending" and task.owner is None
