"""评分口径的单一来源：维度、权重、版本只定义一次。"""
import agent
import backend.profile as profile
import interview_spec as spec


def test_weights_sum_to_one():
    assert round(sum(spec.DIMENSION_WEIGHTS.values()), 6) == 1.0


def test_weighted_overall():
    scores = {"技术正确性": 8, "深度与原理": 7, "工程与场景思考": 6, "表达与结构": 9}
    assert spec.weighted_overall(scores) == 7.35


def test_weighted_overall_renormalizes_missing_dimensions():
    """未考察的维度不按 0 分拉低总分，而是用已有维度的权重重新归一化。"""
    assert spec.weighted_overall({"技术正确性": 8, "深度与原理": 6}) == 7.0


def test_agent_uses_the_shared_dimensions():
    assert agent.INTERVIEW_SCORE_DIMENSIONS == spec.DIMENSION_NAMES


def test_profile_uses_the_shared_dimensions():
    assert profile.DIMENSION_NAMES == spec.DIMENSION_NAMES
