"""评分校准的统计逻辑：不调模型，只验证指标算得对。"""
import pytest

from evals import calibration


def test_spearman_perfect_and_inverse():
    assert calibration.spearman([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == pytest.approx(1.0)
    assert calibration.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_handles_ties_and_small_samples():
    assert calibration.spearman([1, 2, 2, 3], [1, 2, 3, 4]) is not None
    assert calibration.spearman([1, 2], [1, 2]) is None      # 样本不足
    assert calibration.spearman([1, 1, 1], [1, 2, 3]) is None  # 零方差


def test_normalize_ai_maps_0_10_to_1_5():
    assert calibration.normalize_ai(0) == 1.0
    assert calibration.normalize_ai(5) == 3.0
    assert calibration.normalize_ai(10) == 5.0
    assert calibration.normalize_ai(None) is None


def _perfect_data():
    packets = [{"id": f"p{i}", "ai_scores": {"技术正确性": i}} for i in range(1, 11)]
    ratings = []
    for rater in ("alice", "bob"):
        for i in range(1, 11):
            ratings.append({"packet_id": f"p{i}", "rater": rater,
                            "技术正确性": i / 2 + 0.5})
    return packets, ratings


def test_compute_perfect_agreement():
    packets, ratings = _perfect_data()
    result = calibration.compute(packets, ratings)
    data = result["dimensions"]["技术正确性"]
    assert data["ai_human_rho"] == pytest.approx(1.0)
    assert data["human_baseline_rho"] == pytest.approx(1.0)
    assert data["ai_vs_human"] == pytest.approx(1.0)
    assert data["direction_rate"] == pytest.approx(1.0)
    assert "100%" in result["overall"]["headline"]


def test_compute_warns_on_small_sample():
    result = calibration.compute([{"id": "p1", "ai_scores": {"技术正确性": 6}}], [])
    assert result["warnings"]
    assert result["overall"]["headline"].startswith("样本不足")


def test_public_packets_strip_ai_scores():
    """发给评委的版本不能带 AI 分数，否则不是盲评。"""
    packets = [{"id": "p1", "question": "Q", "answer": "A",
                "ai_scores": {"技术正确性": 8}}]
    public = calibration.public_packets(packets)
    assert "ai_scores" not in public[0]
    assert public[0]["question"] == "Q"
    assert "ai_scores" in packets[0]          # 原数据不被修改


def test_build_packets_pairs_questions_and_answers():
    session = {
        "id": "abc123def456",
        "status": "completed",
        "role": "AI 应用开发",
        "messages": [
            {"role": "assistant", "text": "Q1", "status": "completed"},
            {"role": "user", "text": "A1", "status": "completed"},
            {"role": "assistant", "text": "Q2", "status": "completed"},
            {"role": "user", "text": "A2", "status": "completed"},
        ],
        "report": {"per_question": [
            {"question": "Q1", "技术正确性": 8, "深度与原理": 7,
             "工程与场景思考": 6, "表达与结构": 9},
            {"question": "Q2", "技术正确性": 7, "深度与原理": 6,
             "工程与场景思考": 5, "表达与结构": 8},
        ]},
    }
    packets = calibration.build_packets([session])
    assert len(packets) == 2
    assert packets[0]["question"] == "Q1"
    assert packets[0]["answer"] == "A1"
    assert packets[1]["ai_scores"]["技术正确性"] == 7
    assert packets[0]["id"].startswith("abc123de")
