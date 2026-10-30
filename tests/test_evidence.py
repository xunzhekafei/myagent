"""评分证据的原话校验。

报告里的 evidence 必须是候选人回答的逐字原文；这里验证「逐字」到底怎么判。
"""
import agent

ANSWER = "我负责评测集建设，整理了 400 多条问答对。准确率从 71% 提升到 83%。"


def test_verbatim_quote_accepted():
    ok, quote, _ = agent.validate_evidence("整理了 400 多条问答对", ANSWER)
    assert ok
    assert quote == "整理了 400 多条问答对"


def test_whitespace_variant_accepted():
    """纯排版差异（空格/换行）不算改写。"""
    ok, _, _ = agent.validate_evidence("整理了  400 多条\n问答对", ANSWER)
    assert ok


def test_quoted_phrase_accepted():
    ok, quote, _ = agent.validate_evidence(
        "候选人说：「准确率从 71% 提升到 83%」", ANSWER)
    assert ok
    assert quote == "准确率从 71% 提升到 83%"


def test_prefix_is_stripped():
    ok, quote, _ = agent.validate_evidence("候选人说：整理了 400 多条问答对", ANSWER)
    assert ok
    assert quote == "整理了 400 多条问答对"


def test_paraphrase_rejected():
    """模型改写原文 = 编造引用，必须拒绝。"""
    ok, _, reason = agent.validate_evidence("我整理了数百条问答对", ANSWER)
    assert not ok
    assert "逐字原文" in reason


def test_question_text_rejected():
    ok, _, _ = agent.validate_evidence("具体怎么提升准确率的", ANSWER)
    assert not ok


def test_empty_quote_rejected():
    assert agent.validate_evidence("", ANSWER)[0] is False
    assert agent.validate_evidence("   ", ANSWER)[0] is False


def test_quote_from_any_turn_accepted():
    ok, _, _ = agent.validate_evidence_any(
        "提到了 badcase 归类", ["第一轮回答", "第二轮提到了 badcase 归类"])
    assert ok


def test_cross_turn_splice_rejected():
    """跨两轮回答拼接的引用不算逐字原文。"""
    ok, _, _ = agent.validate_evidence_any(
        "整理了 400 条问答对每周做一次",
        ["整理了 400 条问答对", "每周做一次 badcase 归类"])
    assert not ok


def test_unknown_answer_rejected():
    ok, _, _ = agent.validate_evidence_any("这句话不在任何回答里", ["第一轮", "第二轮"])
    assert not ok


# ---------- 评分循环里的重试与降级 ----------

class _Task:
    def event(self, *_args, **_kwargs):
        pass


class _RetryContext:
    """按顺序吐出 evidence；用来验证非法证据的重试与降级。"""

    task = _Task()

    def __init__(self, evidence_sequence):
        self.evidence = list(evidence_sequence)
        self.calls = []

    def phase(self, _title):
        pass

    def parallel(self, thunks):
        return [fn() for fn in thunks]

    def pipeline(self, batch, stage, on_done=None):
        out = []
        for index, item in enumerate(batch):
            value = stage(item, item, index)
            if on_done is not None:
                on_done(index, value)
            out.append(value)
        return out

    def agent(self, prompt, schema, label, phase=""):
        self.calls.append(label)
        if label.startswith("parse"):
            return {"qa": [{"question": "Q0",
                            "answer": "我负责评测集建设，整理了 400 多条问答对。"}]}
        if label.startswith("score:"):
            return {"技术正确性": 8, "深度与原理": 7, "工程与场景思考": 6,
                    "表达与结构": 9, "evidence": self.evidence.pop(0),
                    "suggestion": "建议"}
        return {"overall": 7.5,
                "dimension_scores": {"技术正确性": 8, "深度与原理": 7,
                                     "工程与场景思考": 6, "表达与结构": 9},
                "strengths": [], "weaknesses": [], "recommendations": [], "summary": ""}


def test_retry_fixes_invalid_evidence():
    ctx = _RetryContext(["我做了很多优化", "整理了 400 多条问答对"])
    report = agent._interview_report(ctx, {"transcript": "x", "include_all": True})
    item = report["per_question"][0]
    assert item["evidence_verified"] is True
    assert item["evidence"] == "整理了 400 多条问答对"
    assert "score:0" in ctx.calls and "score:0:retry1" in ctx.calls


def test_invalid_evidence_is_flagged_after_retries():
    bad = "我做了很多优化"
    ctx = _RetryContext([bad, bad, bad])
    report = agent._interview_report(ctx, {"transcript": "x", "include_all": True})
    item = report["per_question"][0]
    assert item["evidence_verified"] is False
    assert "score:0:retry2" in ctx.calls          # 1 次首评 + 2 次重试
    assert item["技术正确性"] == 8                # 证据不过，分数照样保留
