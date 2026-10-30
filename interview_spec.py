# -*- coding: utf-8 -*-
"""面试评分口径的单一来源。

维度名、权重、评分版本只在这里定义一次：
- agent.py 的评分 workflow 从这里读
- backend/profile.py 的跨场次聚合从这里读
- 前端不硬编码维度名——报告和逐题事件里带的是 dimension_scores 的键

改这里的任何一项（维度、权重、分制）都必须同时递增 SCORING_VERSION，
否则成长曲线会把「口径变了」误画成「水平变了」。
"""

# v1：只有 0-10 的整数要求
# v2：加入分档标尺
# v3：总分由「四维均分」改为「按权重加权和」
SCORING_VERSION = 3

DIMENSIONS = (
    ("技术正确性", 0.30),
    ("深度与原理", 0.30),
    ("工程与场景思考", 0.25),
    ("表达与结构", 0.15),
)

DIMENSION_NAMES = tuple(name for name, _ in DIMENSIONS)
DIMENSION_WEIGHTS = dict(DIMENSIONS)


def weighted_overall(scores: dict) -> float | None:
    """按权重算总分；缺维度时用已有维度的权重重新归一化。

    未考察的维度不应该按 0 分拉低总分——这和 backend-interview-simulator
    的阶段性综合分是同一条规则。
    """
    total = 0.0
    weight_sum = 0.0
    for name, weight in DIMENSIONS:
        value = scores.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value) * weight
            weight_sum += weight
    if weight_sum == 0:
        return None
    return round(total / weight_sum, 2)
