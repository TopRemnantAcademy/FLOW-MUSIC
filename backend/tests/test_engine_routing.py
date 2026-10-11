"""P0 引擎路由骨架测试：resolve_engine 映射（DB 驱动）+ Song 字段契约。

resolve_engine 现为 DB 后台配置驱动（app.orchestrator.selection），需先注入默认种子。
不依赖外部 API；纯函数与 ORM 字段验证。
"""

from app.db import SessionLocal
from app.models import Song, EngineConfig, RoutingRule, PricingTier
from app.orchestrator.selection import (
    resolve_engine,
    seed_default_config,
    invalidate_config_cache,
)


def _seed(db):
    invalidate_config_cache()
    db.query(PricingTier).delete()
    db.query(RoutingRule).delete()
    db.query(EngineConfig).delete()
    db.commit()
    seed_default_config(db)


def test_resolve_engine_default_and_general():
    db = SessionLocal()
    try:
        _seed(db)
        # 默认种子：general -> mureka（主引擎，已默认启用）
        assert resolve_engine(db, None) == "mureka"
        assert resolve_engine(db, "general") == "mureka"
        assert resolve_engine(db, "GENERAL") == "mureka"  # 大小写归一
    finally:
        db.close()


def test_resolve_engine_christian_routes_to_mureka():
    db = SessionLocal()
    try:
        _seed(db)
        # 默认种子里 mureka 已「启用」且为默认引擎（对准 mureka 主引擎策略）。
        assert resolve_engine(db, "christian_worship") == "mureka"
        # 后台禁用 mureka 后，christian_worship 实时回退到 failover 引擎 suno（规避单点）。
        db.query(EngineConfig).filter(EngineConfig.provider == "mureka").update({"enabled": False})
        db.commit()
        invalidate_config_cache()
        assert resolve_engine(db, "christian_worship") == "suno"
        # 重新启用 mureka 立即恢复主引擎路由。
        db.query(EngineConfig).filter(EngineConfig.provider == "mureka").update({"enabled": True})
        db.commit()
        invalidate_config_cache()
        assert resolve_engine(db, "christian_worship") == "mureka"
    finally:
        db.close()


def test_resolve_engine_unknown_category_falls_back():
    db = SessionLocal()
    try:
        _seed(db)
        # 未知分类回退到默认引擎 mureka（主引擎）
        assert resolve_engine(db, "unknown_cat") == "mureka"
    finally:
        db.close()


def test_song_has_routing_fields():
    # ORM 契约：字段存在，类型符合预期
    assert hasattr(Song, "content_category")
    assert hasattr(Song, "engine_name")
    assert hasattr(Song, "cost_cny")
