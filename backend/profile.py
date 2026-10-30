# -*- coding: utf-8 -*-
"""跨场次能力画像。

单场报告的字段已经是结构化的（overall / dimension_scores / weaknesses），
所以这里不需要再调模型——纯聚合，结果确定、可测。

回答的是单场报告回答不了的问题：

    · 哪个维度一直在拖后腿？
    · 从第一场到现在有没有变好？（整体 + 每个维度）
    · 不同岗位方向的表现差在哪里？
    · 最近几场反复出现哪些短板？

刻意不做的：把 weaknesses 这些自由文本做语义聚类。那需要模型，既慢又不稳定；
先如实呈现「最近几场说过什么」，语义归并留给报告生成阶段去做标签。
"""
from collections import Counter
from statistics import mean

DIMENSIONS = ("技术正确性", "深度与原理", "工程与场景思考", "表达与结构")
RECENT_SESSIONS = 5


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _completed(sessions):
    """只统计真正生成了报告的场次，并按时间排好。"""
    result = []
    for session in sessions:
        report = session.get("report")
        if session.get("status") != "completed" or not isinstance(report, dict):
            continue
        if _overall(session) is None:
            continue
        result.append(session)
    result.sort(key=lambda s: s.get("created_at") or "")
    return result


def _dimensions(session):
    scores = (session.get("report") or {}).get("dimension_scores") or {}
    result = {}
    for name in DIMENSIONS:
        value = _number(scores.get(name))
        if value is not None:
            result[name] = max(0.0, min(10.0, value))
    return result


def _version(session):
    """老报告没有这个字段——按 v1 处理。"""
    value = (session.get("report") or {}).get("scoring_version")
    return int(value) if isinstance(value, int) else 1


def _overall(session):
    """总分统一按维度均分重算。

    老报告是在量纲修复之前生成的，overall 可能是 100 分制（实测有 79.3），
    直接拿来画成长曲线会把纵轴拉爆。维度分一直是 0-10，用它的均分最稳；
    没有维度分时才退回 original overall，且必须落在 0-10 内。
    """
    scores = _dimensions(session)
    if scores:
        return round(mean(scores.values()), 2)
    value = _number((session.get("report") or {}).get("overall"))
    return value if value is not None and 0 <= value <= 10 else None


def _average(values):
    return round(mean(values), 2) if values else None


def _delta(first, latest):
    if first is None or latest is None:
        return None
    return round(latest - first, 2)


def build_profile(sessions) -> dict:
    completed = _completed(sessions)
    if not completed:
        return {"sessions": 0, "timeline": [], "dimensions": {}, "roles": [],
                "weakest": [], "recurring": [], "recent_weaknesses": [],
                "scoring_versions": []}

    timeline = []
    for session in completed:
        timeline.append({
            "id": session["id"],
            "created_at": session.get("created_at"),
            "role": session.get("role") or "技术",
            "pressure": session.get("pressure") or "标准",
            "overall": _overall(session),
            "dimensions": _dimensions(session),
            "scoring_version": _version(session),
        })

    # 每个维度：均分、首场、最近一场、变化
    dimensions = {}
    for name in DIMENSIONS:
        series = [(_dimensions(s).get(name)) for s in completed]
        series = [v for v in series if v is not None]
        if not series:
            continue
        dimensions[name] = {
            "average": _average(series),
            "first": series[0],
            "latest": series[-1],
            "delta": _delta(series[0], series[-1]),
            "best": max(series),
            "worst": min(series),
            "samples": len(series),
        }

    # 按岗位方向分组
    roles = []
    by_role = {}
    for session in completed:
        by_role.setdefault(session.get("role") or "技术", []).append(session)
    for role, items in by_role.items():
        overalls = [_overall(s) for s in items]
        overalls = [v for v in overalls if v is not None]
        roles.append({
            "role": role,
            "sessions": len(items),
            "average": _average(overalls),
            "latest": overalls[-1] if overalls else None,
            "dimensions": {name: _average([_dimensions(s).get(name) for s in items
                                           if _dimensions(s).get(name) is not None])
                           for name in DIMENSIONS},
        })
    roles.sort(key=lambda r: r["sessions"], reverse=True)

    weakest = sorted(dimensions.items(), key=lambda kv: kv[1]["average"])[:2]
    weakest = [{"name": name, **data} for name, data in weakest]

    recent = completed[-RECENT_SESSIONS:]
    counts = Counter(
        " ".join(str(w).split())
        for session in recent
        for w in ((session.get("report") or {}).get("weaknesses") or [])
        if str(w).strip()
    )
    recurring = [{"text": text, "count": count}
                 for text, count in counts.most_common(6) if count >= 2]

    recent_weaknesses = []
    for session in reversed(completed[-2:]):
        for text in ((session.get("report") or {}).get("weaknesses") or [])[:4]:
            recent_weaknesses.append({
                "text": str(text).strip(),
                "role": session.get("role"),
                "created_at": session.get("created_at"),
            })

    overalls = [entry["overall"] for entry in timeline]
    return {
        "sessions": len(completed),
        "range": {"from": timeline[0]["created_at"], "to": timeline[-1]["created_at"]},
        "overall": {
            "average": _average(overalls),
            "first": overalls[0],
            "latest": overalls[-1],
            "delta": _delta(overalls[0], overalls[-1]),
            "best": max(overalls),
            "worst": min(overalls),
        },
        "timeline": timeline,
        "scoring_versions": sorted({entry["scoring_version"] for entry in timeline}),
        "dimensions": dimensions,
        "roles": roles,
        "weakest": weakest,
        "recurring": recurring,
        "recent_weaknesses": recent_weaknesses,
    }
