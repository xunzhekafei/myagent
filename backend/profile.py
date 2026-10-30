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
import re
from collections import Counter
from datetime import date, timedelta
from difflib import SequenceMatcher
from statistics import mean

DIMENSIONS = ("技术正确性", "深度与原理", "工程与场景思考", "表达与结构")
RECENT_SESSIONS = 5

# 弱点合并与复习调度
WEAK_POINT_SIMILARITY = 0.75
REVIEW_INTERVALS = (1, 3, 7, 14)   # 简化版 SM-2：连续答得不错就逐步拉长
ARCHIVE_AFTER_MISSES = 3           # 连续 3 场没再出现且已改善 → 归档

# 各场报告的 weaknesses 是长句、措辞各异，纯相似度合并不了。
# 先用确定性主题归类，把「深度与原理均分最低…」和「部分原理表述不严谨」
# 归到同一个「原理深度」下，跨场次的次数才有意义。
WEAK_THEMES = (
    ("指标口径", ("指标", "口径", "基线", "度量", "统计", "数字")),
    ("架构与故障", ("架构", "设计", "故障", "降级", "回滚", "容量", "边界", "一致性")),
    ("原理深度", ("原理", "深度", "为什么", "本质", "机制", "量化")),
    ("表达结构", ("表达", "结构", "组织", "自我介绍", "术语堆砌", "重点")),
    ("事实一致性", ("不一致", "矛盾", "前后", "错误", "口误", "严谨")),
    ("个人边界", ("归属", "亲手", "负责哪", "个人贡献", "主导")),
    ("术语准确", ("术语", "名词", "拼写", "同音")),
    ("反问与主动性", ("反问", "主动性", "提问")),
    ("编码能力", ("代码", "编码", "手写", "实现", "算法")),
)


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


def _when(session):
    raw = str(session.get("created_at") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _normalize_weak(text) -> str:
    """去掉空白和标点，只留字词，用于聚类比较。"""
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def _similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= WEAK_POINT_SIMILARITY


def _theme(text) -> str | None:
    """把一条弱点归到固定主题下；归不进去就返回 None，走文本相似度。"""
    value = str(text or "")
    for name, keywords in WEAK_THEMES:
        if any(keyword in value for keyword in keywords):
            return name
    return None


def _quality(score) -> int:
    """把 0-10 的场次得分映射到 0-5 的复习质量。"""
    if score is None:
        return 2
    if score >= 8.5:
        return 5
    if score >= 7.0:
        return 4
    if score >= 5.5:
        return 3
    if score >= 4.0:
        return 2
    return 0


def _schedule(streak: int, quality: int) -> tuple[int, int]:
    """简化版 SM-2：质量 >= 3 就按 1/3/7/14 天拉长；否则重置到 1 天。"""
    if quality < 3:
        return 1, 0
    interval = REVIEW_INTERVALS[min(streak, len(REVIEW_INTERVALS) - 1)]
    return interval, streak + 1


def _weak_points(completed, today):
    """把各场报告里的 weaknesses 聚合成有生命周期的弱点。

    · 相似文本合并（0.75 阈值），first_seen / last_seen / times_seen
    · 连续两场没再出现且以前出现过 >= 2 次 → improved
    · improved 后再缺席到 3 场 → archived
    · 按场次得分更新 1/3/7/14 天的复习间隔
    """
    clusters = []
    for index, session in enumerate(completed):
        seen = _when(session) or today
        score = _overall(session)
        texts = [str(w).strip() for w in
                 ((session.get("report") or {}).get("weaknesses") or [])
                 if str(w).strip()]
        touched = set()
        for text in texts:
            normalized = _normalize_weak(text)
            if not normalized:
                continue
            theme = _theme(text)
            if theme:
                cluster = next((c for c in clusters if c.get("theme") == theme), None)
            else:
                cluster = next((c for c in clusters
                                if not c.get("theme") and _similar(normalized, c["_norm"])),
                               None)
            if cluster is None:
                cluster = {
                    "_norm": normalized, "theme": theme, "text": text,
                    "first_seen": seen, "last_seen": seen, "last_index": index,
                    "times_seen": 0, "sessions": [],
                    "role": session.get("role") or "技术",
                    "interval_days": 1, "review_streak": 0,
                    "next_review": seen + timedelta(days=1),
                }
                clusters.append(cluster)
            if id(cluster) in touched:       # 同一场里重复的相似表述只算一次
                continue
            touched.add(id(cluster))
            cluster["text"] = text           # 用最近一次的表述做展示
            cluster["last_seen"] = seen
            cluster["last_index"] = index
            cluster["role"] = session.get("role") or cluster["role"]
            cluster["times_seen"] += 1
            if session["id"] not in cluster["sessions"]:
                cluster["sessions"].append(session["id"])
            interval, streak = _schedule(cluster["review_streak"], _quality(score))
            cluster["interval_days"] = interval
            cluster["review_streak"] = streak
            cluster["next_review"] = seen + timedelta(days=interval)

    last_index = len(completed) - 1
    points = []
    for cluster in clusters:
        missed = last_index - cluster["last_index"]
        improved = cluster["times_seen"] >= 2 and missed >= 2
        archived = improved and missed >= ARCHIVE_AFTER_MISSES
        next_review = None if archived else cluster["next_review"]
        points.append({
            "text": cluster["text"],
            "theme": cluster.get("theme"),
            "role": cluster["role"],
            "first_seen": cluster["first_seen"].isoformat(),
            "last_seen": cluster["last_seen"].isoformat(),
            "times_seen": cluster["times_seen"],
            "improved": improved,
            "archived": archived,
            "interval_days": cluster["interval_days"],
            "review_streak": cluster["review_streak"],
            "next_review": next_review.isoformat() if next_review else None,
            "due": bool(next_review and next_review <= today),
        })

    # 活跃的在前，出现次数多的在前，其次按复习日期
    points.sort(key=lambda p: (p["archived"], -p["times_seen"],
                               p["next_review"] or "9999-12-31"))
    due = sorted((p for p in points if p["due"]),
                 key=lambda p: p["next_review"] or "9999-12-31")
    return points, due


def build_profile(sessions, today: date | None = None) -> dict:
    today = today or date.today()
    completed = _completed(sessions)
    if not completed:
        return {"sessions": 0, "timeline": [], "dimensions": {}, "roles": [],
                "weakest": [], "recurring": [], "recent_weaknesses": [],
                "weak_points": [], "due_reviews": [], "scoring_versions": []}

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

    weak_points, due_reviews = _weak_points(completed, today)

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
        "weak_points": weak_points,
        "due_reviews": due_reviews,
    }
