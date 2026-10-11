"""GAP-004(로직층) 엔티틀먼트 테스트: 티어→권한 매핑 + 구독(mock) 엔드포인트.

실제 PG 결제는 미연동 — /api/auth/subscribe 는 권한 게이팅 로직을 E2E 검증하는
데모/테스트용(mock) 이다. 실 PG 연동 시 웹훅 핸들러로 교체.
"""

import pytest

from app.db import SessionLocal
from app.orchestrator.selection import seed_default_config, invalidate_config_cache
from app.entitlements import resolve_limits, has_feature
from app.models import User


@pytest.fixture
def db():
    s = SessionLocal()
    seed_default_config(s)
    invalidate_config_cache()
    yield s
    s.rollback()
    s.close()


def test_resolve_limits_free(db):
    lim = resolve_limits(db, None)
    assert lim["tier_key"] == "free"
    assert lim["daily_quota"] >= 1  # settings.per_user_daily_quota 기본값
    assert "generate" in lim["features"]


def test_resolve_limits_paid_tiers(db):
    std = resolve_limits(db, "standard")
    assert std["tier_key"] == "standard"
    assert std["daily_quota"] == 20
    assert "extend" in std["features"]
    assert "remix" not in std["features"]  # standard 미포함

    sac = resolve_limits(db, "sacred")
    assert sac["daily_quota"] == 50
    assert "remix" in sac["features"]

    ent = resolve_limits(db, "enterprise")
    assert ent["daily_quota"] == 999
    assert "stem" in ent["features"]


def test_resolve_limits_unknown_falls_back_to_free(db):
    lim = resolve_limits(db, "bogus_tier")
    assert lim["tier_key"] == "free"
    assert lim["daily_quota"] >= 1


def test_has_feature():
    lim = {"features": ["generate"]}
    assert has_feature(lim, "generate") is True
    assert has_feature(lim, "remix") is False


def test_subscribe_endpoint_updates_tier(client):
    # 注册 → 구독(standard) → /me/billing 이 티어/쿼터 반영
    r = client.post("/api/auth/register", json={"email": "sub@test.com", "password": "password123"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    r2 = client.post("/api/auth/subscribe", json={"tier_key": "standard"}, headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.json()["tier_key"] == "standard"
    assert r2.json()["limits"]["daily_quota"] == 20

    r3 = client.get("/api/songs/me/billing", headers=h)
    assert r3.status_code == 200, r3.text
    assert r3.json()["tier_key"] == "standard"
    assert r3.json()["daily_quota_limit"] == 20


def test_subscribe_rejects_unknown_tier(client):
    r = client.post(
        "/api/auth/register", json={"email": "sub2@test.com", "password": "password123"}
    )
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    r2 = client.post("/api/auth/subscribe", json={"tier_key": "bogus"}, headers=h)
    assert r2.status_code == 400
