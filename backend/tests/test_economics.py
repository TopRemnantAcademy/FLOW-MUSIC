"""单位经济（成本/价格/毛利）核算测试：解析逻辑 + 生成落库 + 后台汇总 + 实账单对账。

conftest 在会话级 set_orchestrator(MockProvider())、每测试后 _clean 清空全表，
故本文件内自行建配置行 / 调 seed_default_config。
"""

import pytest

from app.db import SessionLocal
from app.config import settings
from app.models import Song, User
from app.orchestrator.selection import (
    seed_default_config,
    invalidate_config_cache,
    resolve_cost_cny,
    resolve_price_cny,
    compute_margin_cny,
)
from app.routers.songs import _create_song
from app.routers.admin import config_dashboard, stats
from app.schemas import GenerateReq


@pytest.fixture
def db():
    s = SessionLocal()
    seed_default_config(s)
    invalidate_config_cache()
    yield s
    s.rollback()
    s.close()


# ---------- 1. 解析逻辑 ----------
def test_resolve_cost_multiplies_by_n(db):
    # 默认种子 mureka 单价 0.33
    assert resolve_cost_cny(db, "general") == pytest.approx(0.33)
    assert resolve_cost_cny(db, "general", n=2) == pytest.approx(0.66)
    assert resolve_cost_cny(db, "general", n=3) == pytest.approx(0.99)


def test_resolve_price_uses_tier_or_fallthrough(db):
    # sacred 套餐价 2.0
    assert resolve_price_cny(db, "sacred", "christian_worship") == pytest.approx(2.0)
    # free 套餐价 0 -> 不向用户收款(loss-leader, price=0)
    assert resolve_price_cny(db, "free", "general") == pytest.approx(0.0)
    # 未知/无套餐(None) -> 同样 loss-leader(price=0)
    assert resolve_price_cny(db, None, "general") == pytest.approx(0.0)


def test_compute_margin_cny():
    assert compute_margin_cny(2.0, 0.33) == pytest.approx(1.67)
    assert compute_margin_cny(0.33, 0.33) == pytest.approx(0.0)
    assert compute_margin_cny(0.1, 0.33) == pytest.approx(-0.23)  # 亏损


# ---------- 2. 生成落库记录 economics ----------
def test_create_song_records_economics(db):
    user = User(email="eco1@test.com", hashed_password="x", tier_key="sacred")
    db.add(user)
    db.commit()
    db.refresh(user)
    req = GenerateReq(title="t", content_category="christian_worship", lyric="x")
    song = _create_song(req, user, db)
    assert song.engine_name == "mureka"
    assert float(song.cost_cny) == pytest.approx(0.33)  # n=1
    assert float(song.price_cny) == pytest.approx(2.0)  # sacred 套餐价
    assert float(song.margin_cny) == pytest.approx(1.67)  # 2.0 - 0.33
    assert song.tier_key == "sacred"


def test_create_song_free_tier_fallthrough(db):
    user = User(email="eco2@test.com", hashed_password="x", tier_key=None)
    db.add(user)
    db.commit()
    db.refresh(user)
    req = GenerateReq(title="t", content_category="general")
    song = _create_song(req, user, db)
    assert float(song.cost_cny) == pytest.approx(0.33)
    assert float(song.price_cny) == pytest.approx(0.0)  # free/None -> loss-leader: 不向用户收款
    assert float(song.margin_cny) == pytest.approx(-0.33)  # 毛利 = 0 - 成本（亏损，获客费用）
    assert song.tier_key is None


# ---------- 3. 后台汇总 ----------
def test_config_dashboard_aggregates(db):
    user = User(email="agg@test.com", hashed_password="x", tier_key="sacred")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(
        Song(
            user_id=user.id,
            status="completed",
            content_category="christian_worship",
            engine_name="mureka",
            cost_cny=0.33,
            price_cny=2.0,
            margin_cny=1.67,
            tier_key="sacred",
        )
    )
    db.commit()
    out = config_dashboard(db)
    assert out.total_cost_cny == pytest.approx(0.33)
    assert out.total_revenue_cny == pytest.approx(2.0)
    assert out.total_margin_cny == pytest.approx(1.67)
    assert out.margin_rate == pytest.approx(1.67 / 2.0)
    assert out.revenue_by_tier.get("sacred") == pytest.approx(2.0)
    assert out.margin_by_tier.get("sacred") == pytest.approx(1.67)


def test_stats_aggregates(db):
    user = User(email="st@test.com", hashed_password="x", tier_key="standard")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(
        Song(
            user_id=user.id,
            status="completed",
            cost_cny=0.33,
            price_cny=0.36,
            margin_cny=0.03,
            tier_key="standard",
        )
    )
    db.commit()
    out = stats(db)
    assert out.total_cost_cny == pytest.approx(0.33)
    assert out.total_revenue_cny == pytest.approx(0.36)
    assert out.total_margin_cny == pytest.approx(0.03)


# ---------- 4. Mureka 实账单对账（mock，不计费） ----------
def test_billing_endpoint_reconciles(client, monkeypatch):
    settings.mureka_api_key = "op_test"
    settings.admin_token = "test-admin"

    async def fake_billing(self):
        return {
            "account_id": 1,
            "balance": 1000,
            "total_recharge": 1000,
            "total_spending": 50,
            "concurrent_request_limit": 1,
        }

    monkeypatch.setattr("app.routers.admin.MurekaProvider.get_billing", fake_billing)
    r = client.get("/api/admin/billing", headers={"X-Admin-Token": "test-admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["live"]["balance"] == 1000
    assert "estimated_cost_cny" in body
    assert body.get("live_error") is None  # 账单查询未失败
