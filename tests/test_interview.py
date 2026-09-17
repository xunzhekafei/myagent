"""面试题库检索测试：关键词匹配、筛选、条数上限、空库提示、真实题库结构。"""
import json
import pathlib

REAL_BANK = (pathlib.Path(__file__).resolve().parent.parent
             / "interview" / "data" / "ai_questions.json")

FAKE_BANK = [
    {"question": "How would you deploy a model to production with low latency?",
     "keywords": ["model deployment", "latency", "serving"], "role": "AI/ML Engineer",
     "category": "System Design & Architecture", "level": "Level 2: Practical",
     "stage": "Technical"},
    {"question": "Explain the bias-variance tradeoff.",
     "keywords": ["bias", "variance"], "role": "Data Scientist",
     "category": "Coding & Problem Solving", "level": "Level 1: Foundational",
     "stage": "Technical"},
    {"question": "How do you handle a conflict with a stakeholder about metrics?",
     "keywords": ["conflict", "metrics"], "role": "Data Analyst",
     "category": "Conflict Resolution", "level": "Level 3: Edge Case & Conflict",
     "stage": "Behavioral"},
]


def _write_bank(iso, records=None):
    iso.QUESTION_BANK_PATH.write_text(
        json.dumps(records if records is not None else FAKE_BANK, ensure_ascii=False),
        encoding="utf-8")
    iso._QUESTION_CACHE = None


def test_search_by_keyword(iso):
    _write_bank(iso)
    result = iso.search_questions.call({"query": "deployment latency"})
    assert "找到" in result and "deploy a model" in result
    assert "关键词：" in result


def test_search_with_filters(iso):
    _write_bank(iso)
    assert "bias-variance" in iso.search_questions.call({"query": "", "role": "Data Scientist"})
    assert "conflict" in iso.search_questions.call({"query": "", "category": "Conflict"})
    assert "bias-variance" in iso.search_questions.call({"query": "", "level": "Level 1"})
    assert "没有找到" in iso.search_questions.call({"query": "不存在的主题zzzz"})


def test_limit_is_capped(iso):
    bank = [{"question": f"Question {i}", "keywords": ["k"], "role": "R",
             "category": "C", "level": "L", "stage": ""} for i in range(20)]
    _write_bank(iso, bank)
    result = iso.search_questions.call({"query": "", "limit": 50})
    assert result.count("- [") == 10          # 上限 10


def test_empty_bank_gives_import_hint(iso):
    result = iso.search_questions.call({"query": "x"})
    assert "题库为空" in result and "import_dataset" in result


def test_real_bank_shape():
    import pytest
    if not REAL_BANK.is_file():
        pytest.skip("真实题库未导入（运行 python interview/import_dataset.py）")
    records = json.loads(REAL_BANK.read_text(encoding="utf-8"))
    assert len(records) > 1000
    assert {"question", "keywords", "role", "category", "level"} <= set(records[0])
    assert all(record["question"] for record in records)


# ---------- 多数据源加载 & 中文题库导入 ----------

def test_bank_loads_multiple_json_files(iso):
    """回归：题库加载器合并目录下所有 JSON（英文 InterviewForge + 中文 GitHub 题库）。"""
    import agent
    iso.QUESTION_BANK_PATH.parent.mkdir(parents=True, exist_ok=True)
    (iso.QUESTION_BANK_PATH.parent / "en.json").write_text(
        json.dumps([FAKE_BANK[0]], ensure_ascii=False), encoding="utf-8")
    (iso.QUESTION_BANK_PATH.parent / "zh.json").write_text(
        json.dumps([{"question": "请讲讲「Transformer模型结构」的关键点", "keywords": [],
                     "role": "大模型算法工程师", "category": "大模型应用基础", "level": "",
                     "stage": "", "lang": "zh", "source": "test"}], ensure_ascii=False),
        encoding="utf-8")
    iso._QUESTION_CACHE = None

    assert "deploy a model" in iso.search_questions.call({"query": "deployment latency"})
    assert "Transformer模型结构" in iso.search_questions.call({"query": "Transformer 模型结构"})


def test_importer_extraction_functions():
    """导入器的解析函数（纯函数，无需网络）。"""
    import sys as _sys
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "interview"))
    import import_github_bank as bank

    markdown = """# 标题
## 目录
## 1. 什么是 AI Agent
正文……
### Q13：ReAct 三要素是什么？
**A：** ……
## 8. 综合面试题库（15+ 题）
### Q14：如何设计停止条件？
"""
    questions = bank.extract_from_agent_guide(markdown)
    assert "什么是 AI Agent" in questions
    assert "ReAct 三要素是什么？" in questions
    assert all("目录" not in q and "综合面试题库" not in q for q in questions)   # 小节标题不算题

    assert bank.question_from_filename("Transformer模型结构.md") == \
        "请讲讲「Transformer模型结构」的关键点，并结合实际场景举例。"
    assert bank.question_from_filename("README.md") == ""            # 元文件被过滤
    assert bank.question_from_filename("Accelerate 使用进阶.md") == ""
    assert bank.question_from_filename("什么是RAG.md").endswith("？")
    assert bank.category_from_path(pathlib.Path("1-大模型应用基础/x.md")) == "大模型应用基础"


# ---------- 面试评分 workflow ----------

def _stub_interview_runner(calls, question_count=2):
    def stub(prompt, schema, label, stats):
        calls.append(label)
        stats["agents"] += 1
        if label.startswith("parse"):
            return {"qa": [{"question": f"Q{i}", "answer": f"A{i}"}
                           for i in range(question_count)]}
        if label.startswith("score:"):
            return {"技术正确性": 8, "深度与原理": 7, "工程与场景思考": 6, "表达与结构": 9,
                    "evidence": "他说过……", "suggestion": "建议……"}
        return {"overall": 7.5,
                "dimension_scores": {"技术正确性": 8, "深度与原理": 7,
                                     "工程与场景思考": 6, "表达与结构": 9},
                "strengths": ["基础扎实"], "weaknesses": ["场景思考弱"],
                "recommendations": ["多练系统设计"], "summary": "整体不错"}
    return stub


def test_interview_report_workflow(iso, monkeypatch):
    import agent
    calls: list = []
    monkeypatch.setattr(agent, "_workflow_agent_call", _stub_interview_runner(calls))
    out = json.loads(agent.run_workflow.call({
        "name": "interview-report",
        "args": {"role": "AI/ML 工程师", "transcript": "面试官：Q0\n候选人：A0"}}))
    assert out["status"] == "completed"
    assert out["result"]["overall"] == 7.5
    assert len(out["result"]["per_question"]) == 2
    assert calls[0].startswith("parse") and "score:0" in calls and calls[-1] == "summary"


def test_split_transcript_keeps_lines_intact(iso):
    import agent
    text = "\n".join(f"line-{i:04d}" for i in range(500))
    chunks = agent._split_transcript(text, max_chars=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 260 for chunk in chunks)          # 每段不超上限太多
    assert "".join(chunks).count("line-") == 500               # 一行都没丢
    assert all(not chunk.startswith("\n") for chunk in chunks)


def test_interview_report_parses_in_chunks(iso, monkeypatch):
    """回归：长记录必须分段解析——整段一次解析会超输出上限被截断（真实事故）。"""
    import agent
    calls: list = []

    def stub(prompt, schema, label, stats):
        calls.append(label)
        stats["agents"] += 1
        if label.startswith("parse"):
            return {"qa": [{"question": "Q" + label, "answer": "A"}]}
        if label.startswith("score:"):
            return {**{d: 5 for d in agent.INTERVIEW_SCORE_DIMENSIONS},
                    "evidence": "e", "suggestion": "s"}
        return {"overall": 5,
                "dimension_scores": {d: 5 for d in agent.INTERVIEW_SCORE_DIMENSIONS},
                "strengths": [], "weaknesses": [], "recommendations": [], "summary": "s"}

    monkeypatch.setattr(agent, "_workflow_agent_call", stub)
    long_transcript = "\n".join(f"面试官：问题{i}\n候选人：回答{i}" for i in range(400))
    out = json.loads(agent.run_workflow.call(
        {"name": "interview-report", "args": {"transcript": long_transcript}}))
    assert out["status"] == "completed"
    parse_labels = [c for c in calls if c.startswith("parse")]
    assert len(parse_labels) > 1                                # 确实分段了
    assert len(out["result"]["per_question"]) == len(parse_labels)  # 每段贡献一题，都合并了


def test_interview_report_batches_over_parallel_limit(iso, monkeypatch):
    import agent
    calls: list = []
    monkeypatch.setattr(agent, "_workflow_agent_call",
                        _stub_interview_runner(calls, question_count=10))
    out = json.loads(agent.run_workflow.call({
        "name": "interview-report", "args": {"transcript": "x"}}))
    assert len(out["result"]["per_question"]) == 10   # 10 题分 2 批，未触发并发上限


def test_interview_report_requires_transcript(iso):
    import agent
    assert "transcript" in agent.run_workflow.call(
        {"name": "interview-report", "args": {}})


def test_interview_report_from_transcript_file(iso, monkeypatch):
    """对话被压缩后，从 .transcripts/ 存档重建记录（存档现在是可解析的消息 JSON）。"""
    import agent
    archive = [
        {"role": "user", "content": "开始面试"},
        {"role": "assistant", "content": [{"type": "text", "text": "第一题：介绍一下你的项目。"}]},
        {"role": "user", "content": "我做过一个图像识别工具。"},
    ]
    path = iso.WORKDIR / ".transcripts" / "snip-test.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")

    seen: dict = {}

    def stub(prompt, schema, label, stats):
        stats["agents"] += 1
        if label.startswith("parse"):
            seen["transcript"] = prompt
            return {"qa": [{"question": "Q", "answer": "A"}]}
        if label.startswith("score:"):
            return {"技术正确性": 5, "深度与原理": 5, "工程与场景思考": 5, "表达与结构": 5,
                    "evidence": "x", "suggestion": "y"}
        return {"overall": 5,
                "dimension_scores": {"技术正确性": 5, "深度与原理": 5,
                                     "工程与场景思考": 5, "表达与结构": 5},
                "strengths": [], "weaknesses": [], "recommendations": [], "summary": "s"}

    monkeypatch.setattr(agent, "_workflow_agent_call", stub)
    out = json.loads(agent.run_workflow.call({
        "name": "interview-report",
        "args": {"transcript_file": ".transcripts/snip-test.txt"}}))
    assert out["status"] == "completed"
    assert "面试官：第一题" in seen["transcript"]
    assert "候选人：我做过" in seen["transcript"]


def test_interview_report_auto_picks_latest_archive(iso, monkeypatch):
    """回归：模型什么都不传时，workflow 自动取最新 .transcripts 存档重建——
    模型不需要（也不应该）自己啃大文件。"""
    import agent
    archive_dir = iso.ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "snip-20260101-000000-000001.txt").write_text("[]", encoding="utf-8")
    (archive_dir / "snip-20260102-000000-000002.txt").write_text(
        json.dumps([{"role": "user", "content": "开始面试"},
                    {"role": "assistant", "content": [{"type": "text", "text": "第一题是什么？"}]}],
                   ensure_ascii=False), encoding="utf-8")

    seen: dict = {}

    def stub(prompt, schema, label, stats):
        stats["agents"] += 1
        if label.startswith("parse"):
            seen["prompt"] = prompt
            return {"qa": [{"question": "Q", "answer": "A"}]}
        if label.startswith("score:"):
            return {**{d: 5 for d in agent.INTERVIEW_SCORE_DIMENSIONS},
                    "evidence": "e", "suggestion": "s"}
        return {"overall": 5,
                "dimension_scores": {d: 5 for d in agent.INTERVIEW_SCORE_DIMENSIONS},
                "strengths": [], "weaknesses": [], "recommendations": [], "summary": "s"}

    monkeypatch.setattr(agent, "_workflow_agent_call", stub)
    out = json.loads(agent.run_workflow.call(
        {"name": "interview-report", "args": {"role": "数据分析师"}}))
    assert out["status"] == "completed"
    assert "第一题是什么" in seen["prompt"]          # 自动选中了最新存档


def test_transcript_file_rejects_traversal(iso):
    import agent
    payload = json.loads(agent.run_workflow.call({
        "name": "interview-report", "args": {"transcript_file": "../../etc/passwd"}}))
    assert payload["status"] == "failed"
    assert "越界" in json.dumps(payload, ensure_ascii=False)


def test_empty_parse_fails_loud(iso, monkeypatch):
    """事故回归：解析不出问答对时必须失败并报错——
    绝不能拿空列表让汇总模型编一份假报告（张三的报告 per_question=0 就是这个问题）。"""
    import agent

    def stub(prompt, schema, label, stats):
        stats["agents"] += 1
        return {"qa": []} if label.startswith("parse") else {}

    monkeypatch.setattr(agent, "_workflow_agent_call", stub)
    payload = json.loads(agent.run_workflow.call({
        "name": "interview-report", "args": {"transcript": "随便一段文本"}}))
    assert payload["status"] == "failed"
    assert "解析出任何问答对" in json.dumps(payload, ensure_ascii=False)


# ---------- 面试记录存档 ----------

def test_save_and_list_interview_records(iso):
    import agent
    report = json.dumps({"overall": 8.2, "summary": "不错"}, ensure_ascii=False)
    saved = agent.save_interview_record.call(
        {"user": "小明", "role": "AI/ML 工程师", "report": report})
    assert "已保存" in saved
    listing = agent.list_interviews.call({"user": "小明"})
    assert "8.2" in listing and "AI/ML 工程师" in listing
    assert "还没有面试记录" in agent.list_interviews.call({"user": "查无此人"})


def test_record_with_plain_text_report(iso):
    import agent
    agent.save_interview_record.call({"user": "小红", "role": "数据科学家", "report": "纯文本报告"})
    listing = agent.list_interviews.call({"user": "小红"})
    assert "数据科学家" in listing and "总分" not in listing   # 没有 overall 就不显示分数


def test_records_are_separated_per_user(iso):
    import agent
    agent.save_interview_record.call({"user": "甲", "role": "岗位A", "report": "x"})
    agent.save_interview_record.call({"user": "乙", "role": "岗位B", "report": "y"})
    assert "岗位A" in agent.list_interviews.call({"user": "甲"})
    assert "岗位B" not in agent.list_interviews.call({"user": "甲"})
    everyone = agent.list_interviews.call({})
    assert "甲" in everyone and "乙" in everyone


def test_user_slug_cannot_escape(iso):
    import agent
    agent.save_interview_record.call({"user": "../../etc", "role": "x", "report": "y"})
    files = list(agent.INTERVIEWS_DIR.rglob("*.json"))
    assert files
    assert all(agent.INTERVIEWS_DIR.resolve() in path.resolve().parents for path in files)
