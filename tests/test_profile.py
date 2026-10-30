"""跨场次能力画像的离线测试。

聚合逻辑是纯函数，不需要模型——这里验证的是「多场数据怎么合成一条曲线」。
"""
from backend.profile import build_profile


def session(sid, created, role="AI 应用开发", overall=6.0, dims=None,
            weaknesses=None, status="completed", report=True,
            scoring_version=None):
    payload = {"overall": overall, "dimension_scores": dims or {},
               "weaknesses": weaknesses or []}
    if scoring_version is not None:
        payload["scoring_version"] = scoring_version
    return {
        "id": sid, "candidate": "甲", "role": role, "created_at": created,
        "pressure": "标准", "status": status,
        "report": payload if report else None,
    }


def dims(tech=6, depth=6, engineering=6, expression=6):
    return {"技术正确性": tech, "深度与原理": depth,
            "工程与场景思考": engineering, "表达与结构": expression}


def test_empty_returns_zero():
    profile = build_profile([])
    assert profile["sessions"] == 0
    assert profile["timeline"] == []
    assert profile["weakest"] == []
    assert profile["scoring_versions"] == []


def test_scoring_versions_are_exposed():
    """跨评分口径的场次不能当作同一把尺子——画像要把版本暴露出来。"""
    profile = build_profile([
        session("a", "2026-10-01T10:00:00", dims=dims(), scoring_version=1),
        session("b", "2026-10-02T10:00:00", dims=dims(), scoring_version=2),
        session("c", "2026-10-03T10:00:00", dims=dims()),      # 老报告没有该字段
    ])
    assert profile["scoring_versions"] == [1, 2]
    assert [t["scoring_version"] for t in profile["timeline"]] == [1, 2, 1]


def test_ignores_unfinished_and_reportless_sessions():
    profile = build_profile([
        session("a", "2026-10-01T10:00:00", overall=7, dims=dims(7, 7, 7, 7)),
        session("b", "2026-10-02T10:00:00", status="running"),
        session("c", "2026-10-03T10:00:00", report=False),
    ])
    assert profile["sessions"] == 1
    assert [t["id"] for t in profile["timeline"]] == ["a"]


def test_timeline_is_sorted_by_time():
    """store.list() 是按插入倒序返回的，画像必须自己按时间排。"""
    profile = build_profile([
        session("later", "2026-10-03T10:00:00", overall=8, dims=dims(8, 8, 8, 8)),
        session("earlier", "2026-10-01T10:00:00", overall=6, dims=dims(6, 6, 6, 6)),
    ])
    assert [t["id"] for t in profile["timeline"]] == ["earlier", "later"]
    assert profile["overall"]["first"] == 6
    assert profile["overall"]["latest"] == 8
    assert profile["overall"]["delta"] == 2
    assert profile["overall"]["average"] == 7


def test_dimension_delta_tracks_progress():
    profile = build_profile([
        session("a", "2026-10-01T10:00:00", overall=6, dims=dims(6, 4, 7, 7)),
        session("b", "2026-10-02T10:00:00", overall=7, dims=dims(7, 6, 7, 8)),
    ])
    depth = profile["dimensions"]["深度与原理"]
    assert depth["first"] == 4
    assert depth["latest"] == 6
    assert depth["delta"] == 2
    assert depth["average"] == 5


def test_roles_are_grouped():
    profile = build_profile([
        session("a", "2026-10-01T10:00:00", role="前端工程师", overall=6, dims=dims(6, 6, 6, 6)),
        session("b", "2026-10-02T10:00:00", role="前端工程师", overall=8, dims=dims(8, 8, 8, 8)),
        session("c", "2026-10-03T10:00:00", role="AI/ML Engineer", overall=7, dims=dims(7, 7, 7, 7)),
    ])
    roles = {r["role"]: r for r in profile["roles"]}
    assert roles["前端工程师"]["sessions"] == 2
    assert roles["前端工程师"]["average"] == 7
    assert roles["AI/ML Engineer"]["sessions"] == 1


def test_weakest_is_the_two_lowest_dimensions():
    profile = build_profile([
        session("a", "2026-10-01T10:00:00", overall=6, dims=dims(8, 4, 6, 3)),
    ])
    assert [w["name"] for w in profile["weakest"]] == ["表达与结构", "深度与原理"]


def test_recurring_weakness_needs_two_occurrences():
    profile = build_profile([
        session("a", "2026-10-01T10:00:00", dims=dims(), weaknesses=["指标口径说不清"]),
        session("b", "2026-10-02T10:00:00", dims=dims(), weaknesses=["指标口径说不清"]),
        session("c", "2026-10-03T10:00:00", dims=dims(), weaknesses=["只出现一次"]),
    ])
    recurring = {item["text"]: item["count"] for item in profile["recurring"]}
    assert recurring["指标口径说不清"] == 2
    assert "只出现一次" not in recurring


def test_missing_dimension_in_one_session_is_skipped():
    """老报告可能缺某个维度，不能把它当成 0 分拉低平均。"""
    profile = build_profile([
        session("a", "2026-10-01T10:00:00", overall=6, dims={"技术正确性": 8}),
        session("b", "2026-10-02T10:00:00", overall=6,
                dims={"技术正确性": 6, "深度与原理": 4}),
    ])
    assert profile["dimensions"]["技术正确性"]["average"] == 7
    assert profile["dimensions"]["技术正确性"]["samples"] == 2
    assert profile["dimensions"]["深度与原理"]["samples"] == 1


def test_legacy_100_point_overall_is_normalized():
    """量纲修复之前生成的报告 overall 可能是 79.3；画像必须按维度均分重算。"""
    profile = build_profile([
        session("old", "2026-10-10T10:00:00", overall=79.3,
                dims=dims(8.14, 7.0, 7.93, 8.64)),
    ])
    assert profile["overall"]["latest"] == 7.93
    assert profile["timeline"][0]["overall"] == 7.93


def test_profile_endpoint_smoke(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import create_app

    class Dummy:
        def configured(self):
            return False

        def reply(self, *args, **kwargs):
            return ""

        def report(self, *args, **kwargs):
            return {}

    with TestClient(create_app(tmp_path, Dummy(), recognizer=None)) as client:
        response = client.get("/api/profile")
        assert response.status_code == 200
        assert response.json()["sessions"] == 0


def test_recent_weaknesses_carry_source():
    profile = build_profile([
        session("a", "2026-10-01T10:00:00", role="前端工程师", dims=dims(),
                weaknesses=["第一个短板"]),
        session("b", "2026-10-02T10:00:00", role="AI/ML Engineer", dims=dims(),
                weaknesses=["第二个短板"]),
    ])
    latest = profile["recent_weaknesses"][0]
    assert latest["text"] == "第二个短板"
    assert latest["role"] == "AI/ML Engineer"
