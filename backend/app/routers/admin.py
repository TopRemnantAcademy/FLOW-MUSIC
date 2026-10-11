"""管理后台：歌曲审核 + 引擎/路由/计费后台控制（多引擎路由后台控制核心）。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import settings
from app.models import Song, User, EngineConfig, RoutingRule, PricingTier, AuditLog, UserLink
from app.billing.models import Subscription
from app.mureka_health import format_report, probe_egress
from app.orchestrator.mureka import MurekaProvider
from app.orchestrator.selection import (
    seed_default_config,
    invalidate_config_cache,
    list_engines,
    list_routing,
    list_tiers,
    get_engine,
    get_tier,
)
from app.routers.auth import require_admin
from app.schemas import (
    AdminSongOut,
    AdminStatsOut,
    RejectReq,
    EngineConfigOut,
    EngineConfigCreate,
    EngineConfigUpdate,
    RoutingRuleOut,
    RoutingRuleCreate,
    RoutingRuleUpdate,
    PricingTierOut,
    PricingTierCreate,
    PricingTierUpdate,
    ConfigDashboardOut,
    AuditLogOut,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------- 辅助：脱敏输出 ----------
def _engine_out(e: EngineConfig) -> EngineConfigOut:
    return EngineConfigOut(
        provider=e.provider,
        display_name=e.display_name or None,
        enabled=bool(e.enabled),
        is_default=bool(e.is_default),
        api_base=e.api_base or None,
        api_key_set=bool(e.api_key),
        cost_per_song_cny=float(e.cost_per_song_cny or 0),
        priority=int(e.priority or 0),
        notes=e.notes or None,
    )


# ---------- 歌曲审核（既有） ----------
def _to_admin_out(song: Song, email: str | None) -> AdminSongOut:
    return AdminSongOut(
        id=song.id,
        title=song.title,
        custom_id=song.custom_id or None,
        style=song.style or None,
        prompt=song.prompt or None,
        lyric=song.lyric or None,
        audio_url=song.audio_url or None,
        cover_url=song.cover_url or None,
        status=song.status,
        make_instrumental=song.make_instrumental,
        task_type=song.task_type,
        model_version=song.model_version or None,
        duration=song.duration,
        parent_song_id=song.parent_song_id,
        moderation_status=song.moderation_status,
        moderation_note=song.moderation_note or None,
        is_public=bool(song.is_public),
        play_count=int(song.play_count or 0),
        is_owner=False,
        content_category=song.content_category or None,
        engine_name=song.engine_name or None,
        cost_cny=float(song.cost_cny) if song.cost_cny is not None else None,
        price_cny=float(song.price_cny) if song.price_cny is not None else None,
        margin_cny=float(song.margin_cny) if song.margin_cny is not None else None,
        tier_key=song.tier_key or None,
        created_at=song.created_at.isoformat() if song.created_at else "",
        user_email=email,
    )


@router.get("/songs", response_model=list[AdminSongOut])
def list_songs(
    moderation_status: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    q = db.query(Song, User.email).join(User, User.id == Song.user_id, isouter=True)
    if moderation_status:
        q = q.filter(Song.moderation_status == moderation_status)
    if status:
        q = q.filter(Song.status == status)
    rows = q.order_by(Song.id.desc()).limit(limit).offset(offset).all()
    return [_to_admin_out(song, email) for song, email in rows]


@router.post("/songs/{song_id}/approve", response_model=AdminSongOut)
def approve_song(
    song_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    song.moderation_status = "approved"
    song.moderation_note = ""
    db.commit()
    db.refresh(song)
    user = db.get(User, song.user_id) if song.user_id else None
    return _to_admin_out(song, user.email if user else None)


@router.post("/songs/{song_id}/reject", response_model=AdminSongOut)
def reject_song(
    song_id: int,
    body: RejectReq,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    song.moderation_status = "rejected"
    song.moderation_note = body.note or ""
    # [SEC] 2026-09-13:驳回必须**同时撤销公开**，否则该曲只是从广场列表里消失，
    # 按 id 直连仍然可读歌词/prompt、订阅者仍可下载完整音频，甚至可以被他人
    # cover/remix（`_spawn_from_parent` 只判 `is_public or owner`）洗成一首新歌
    # 重新上架——审核闭环被完全绕过。
    song.is_public = False
    db.commit()
    db.refresh(song)
    user = db.get(User, song.user_id) if song.user_id else None
    return _to_admin_out(song, user.email if user else None)


@router.get("/stats", response_model=AdminStatsOut)
def stats(
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    total = db.query(func.count(Song.id)).scalar() or 0
    by_status = {
        k: v for k, v in db.query(Song.status, func.count(Song.id)).group_by(Song.status).all()
    }
    by_moderation = {
        k: v
        for k, v in db.query(Song.moderation_status, func.count(Song.id))
        .group_by(Song.moderation_status)
        .all()
    }
    total_cost = float(db.query(func.coalesce(func.sum(Song.cost_cny), 0)).scalar() or 0)
    total_revenue = float(db.query(func.coalesce(func.sum(Song.price_cny), 0)).scalar() or 0)
    total_margin = float(db.query(func.coalesce(func.sum(Song.margin_cny), 0)).scalar() or 0)
    return AdminStatsOut(
        total=total,
        by_status=by_status,
        by_moderation=by_moderation,
        total_cost_cny=round(total_cost, 4),
        total_revenue_cny=round(total_revenue, 4),
        total_margin_cny=round(total_margin, 4),
    )


# ---------- 后台控制：Mureka 实时账单对账（不计费） ----------
@router.get("/billing")
async def billing_reconciliation(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    """Mureka 实时账单 vs 本站估算成本 对账。

    - 实时调用 GET /v1/account/billing（**不计费**），取余额/累计充值/累计消费/并发上限；
    - 与本站 songs 表累计估算成本(estimated_cost_cny) 对照，发现「估算成本」与「实际扣费」的偏差。
    - 注意：live.total_spending 为美元分；estimated_cost_cny 为人民币元，二者币种不同，仅量级参考。
    """
    estimated_cost_cny = round(
        float(db.query(func.coalesce(func.sum(Song.cost_cny), 0)).scalar() or 0), 4
    )
    result: dict = {
        "provider": "mureka",
        "configured": bool(settings.mureka_api_key),
        "estimated_cost_cny": estimated_cost_cny,
        "live": None,
        "delta_note": "live.total_spending 为美元分；estimated_cost_cny 为人民币元，币种不同仅量级参考",
    }
    if settings.mureka_api_key:
        try:
            provider = MurekaProvider()
            result["live"] = await provider.get_billing()
        except Exception as e:  # 账单查询失败不阻断其他管理功能
            result["live_error"] = str(e)
    return result


# ---------- Mureka 出口自检（P0-CRIT：地域封锁「部署即见」，不必等用户报障） ----------
@router.get("/mureka/egress")
async def mureka_egress(_: None = Depends(require_admin)):
    """探测**当前部署出口**到 Mureka 的连通性 / 授权 / 地域（免费，不计费）。

    典型用法：部署到中国香港等目标环境后，打开本端点即可判定出口是否可用。
    `kind` 语义（见 app/mureka_health.py）：
      ok        出口可用且 key 有效（200）
      region    **403 地域封锁** —— 该出口不在 Mureka 允许区域，须换区域或走出口代理
      auth      401 —— API Key 无效/过期
      param     400 / rate_limit 429 / server 5xx / network 网络层失败
      disabled  未配置 MUREKA_API_KEY
    ⚠️ 本端点只反映**当前这台机器**的出口；开发机探测成功不代表生产出口可用。
    """
    result = await probe_egress()
    result["report"] = format_report(result)
    # 启动期自检模式（off=未启用 / warn=仅告警 / strict=封锁时拒绝启动）
    result["preflight_mode"] = settings.mureka_preflight_mode
    return result


# ---------- 后台控制：引擎 ----------
@router.get("/engines", response_model=list[EngineConfigOut])
def get_engines(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return [_engine_out(e) for e in list_engines(db)]


@router.get("/engines/{provider}", response_model=EngineConfigOut)
def get_engine_endpoint(
    provider: str, db: Session = Depends(get_db), _: None = Depends(require_admin)
):
    e = get_engine(db, provider)
    if not e:
        raise HTTPException(status_code=404, detail="引擎不存在")
    return _engine_out(e)


@router.get("/audit", response_model=list[AuditLogOut])
def list_audit(
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
    limit: int = 200,
):
    """管理后台审计日志（最近 limit 条，倒序）。viewer 亦可只读。"""
    rows = (
        db.query(AuditLog).order_by(AuditLog.id.desc()).limit(max(1, min(int(limit), 1000))).all()
    )
    return [
        AuditLogOut(
            id=r.id,
            method=r.method,
            path=r.path,
            status_code=r.status_code,
            actor_token_prefix=r.actor_token_prefix,
            actor_role=r.actor_role,
            client_ip=r.client_ip,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.get("/user-links")
def list_user_links(
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
    subscriber_id: str | None = None,
    music_user_id: int | None = None,
    limit: int = 200,
):
    """跨域账号桥接只读查询（viewer 亦可，仅只读）。

    返回 user_links 映射 + 其绑定的音乐侧账号/订阅状态。**绝不返回 PII 原文**：
    email 仅以「存在性标记」形式出现（music_email_present），档位/订阅状态为派生态。
    双向查找：
    - subscriber_id：按 FLOW 订阅者查其绑定的音乐账号。
    - music_user_id：反向查该音乐账号绑定的 FLOW 订阅者。
    不传过滤：返回最近 limit 条映射（默认 200，封顶 500）。
    """
    q = db.query(UserLink)
    if subscriber_id:
        q = q.filter(UserLink.flow_subscriber_id == subscriber_id)
    if music_user_id is not None:
        q = q.filter(UserLink.music_user_id == music_user_id)
    links = q.order_by(UserLink.id.desc()).limit(max(1, min(int(limit), 500))).all()

    out = []
    for link in links:
        user = db.get(User, link.music_user_id) if link.music_user_id else None
        sub = None
        if user is not None:
            sub = (
                db.query(Subscription)
                .filter(Subscription.user_id == user.id)
                .order_by(Subscription.id.desc())
                .first()
            )
        out.append(
            {
                "flow_subscriber_id": link.flow_subscriber_id,
                "music_user_id": link.music_user_id,
                "music_email_present": bool(user and user.email),
                "music_tier_key": user.tier_key if user else None,
                "subscription": (
                    {
                        "tier_key": sub.tier_key,
                        "provider": sub.provider,
                        "status": sub.status,
                        "current_period_end": (
                            sub.current_period_end.isoformat() if sub.current_period_end else None
                        ),
                    }
                    if sub
                    else None
                ),
                "linked_at": link.created_at.isoformat() if link.created_at else None,
            }
        )
    return out


@router.post("/engines", response_model=EngineConfigOut, status_code=201)
def create_engine(
    body: EngineConfigCreate, db: Session = Depends(get_db), _: None = Depends(require_admin)
):
    if db.get(EngineConfig, body.provider):
        raise HTTPException(status_code=409, detail="引擎已存在")
    e = EngineConfig(
        provider=body.provider,
        display_name=body.display_name or "",
        enabled=body.enabled,
        is_default=body.is_default,
        api_base=body.api_base or "",
        api_key="",  # 明文不落库，构造后由 set_api_key 加密
        cost_per_song_cny=body.cost_per_song_cny if body.cost_per_song_cny is not None else 0,
        priority=body.priority if body.priority is not None else 0,
        notes=body.notes or "",
    )
    e.set_api_key(body.api_key)  # GAP-007: api_key 静态加密存储
    db.add(e)
    db.commit()
    db.refresh(e)
    invalidate_config_cache()
    return _engine_out(e)


@router.put("/engines/{provider}", response_model=EngineConfigOut)
def update_engine(
    provider: str,
    body: EngineConfigUpdate,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    e = get_engine(db, provider)
    if not e:
        raise HTTPException(status_code=404, detail="引擎不存在")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k == "api_key":
            if v is None or v == "":
                continue  # 空表示不改
            e.set_api_key(v)  # GAP-007: 加密存储
        else:
            setattr(e, k, v)
    db.commit()
    db.refresh(e)
    invalidate_config_cache()
    return _engine_out(e)


@router.delete("/engines/{provider}")
def delete_engine(provider: str, db: Session = Depends(get_db), _: None = Depends(require_admin)):
    e = get_engine(db, provider)
    if not e:
        raise HTTPException(status_code=404, detail="引擎不存在")
    db.delete(e)
    db.commit()
    invalidate_config_cache()
    return {"ok": True}


# ---------- 后台控制：路由规则（分类 -> 引擎） ----------
@router.get("/routing-rules", response_model=list[RoutingRuleOut])
def get_routing_rules(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return [
        RoutingRuleOut(
            id=r.id,
            category=r.category,
            provider=r.provider,
            priority=int(r.priority or 0),
            enabled=bool(r.enabled),
        )
        for r in list_routing(db)
    ]


@router.post("/routing-rules", response_model=RoutingRuleOut, status_code=201)
def create_routing_rule(
    body: RoutingRuleCreate, db: Session = Depends(get_db), _: None = Depends(require_admin)
):
    r = RoutingRule(
        category=body.category, provider=body.provider, priority=body.priority, enabled=body.enabled
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    invalidate_config_cache()
    return RoutingRuleOut(
        id=r.id,
        category=r.category,
        provider=r.provider,
        priority=int(r.priority or 0),
        enabled=bool(r.enabled),
    )


@router.put("/routing-rules/{rule_id}", response_model=RoutingRuleOut)
def update_routing_rule(
    rule_id: int,
    body: RoutingRuleUpdate,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    r = db.get(RoutingRule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="路由规则不存在")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(r, k, v)
    db.commit()
    db.refresh(r)
    invalidate_config_cache()
    return RoutingRuleOut(
        id=r.id,
        category=r.category,
        provider=r.provider,
        priority=int(r.priority or 0),
        enabled=bool(r.enabled),
    )


@router.delete("/routing-rules/{rule_id}")
def delete_routing_rule(
    rule_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)
):
    r = db.get(RoutingRule, rule_id)
    if not r:
        raise HTTPException(status_code=404, detail="路由规则不存在")
    db.delete(r)
    db.commit()
    invalidate_config_cache()
    return {"ok": True}


# ---------- 后台控制：计费套餐 ----------
@router.get("/pricing-tiers", response_model=list[PricingTierOut])
def get_pricing_tiers(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    return [
        PricingTierOut(
            id=t.id,
            tier_key=t.tier_key,
            tier_name=t.tier_name or None,
            engine_provider=t.engine_provider or None,
            price_cny=float(t.price_cny or 0),
            credits_per_song=int(t.credits_per_song or 1),
            model_version=t.model_version or None,
            description=t.description or None,
            enabled=bool(t.enabled),
        )
        for t in list_tiers(db)
    ]


@router.post("/pricing-tiers", response_model=PricingTierOut, status_code=201)
def create_pricing_tier(
    body: PricingTierCreate, db: Session = Depends(get_db), _: None = Depends(require_admin)
):
    if get_tier(db, body.tier_key):
        raise HTTPException(status_code=409, detail="套餐已存在")
    t = PricingTier(
        tier_key=body.tier_key,
        tier_name=body.tier_name or "",
        engine_provider=body.engine_provider,
        price_cny=body.price_cny if body.price_cny is not None else 0,
        credits_per_song=body.credits_per_song if body.credits_per_song is not None else 1,
        model_version=body.model_version or "",
        description=body.description or "",
        enabled=body.enabled,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return PricingTierOut(
        id=t.id,
        tier_key=t.tier_key,
        tier_name=t.tier_name or None,
        engine_provider=t.engine_provider or None,
        price_cny=float(t.price_cny or 0),
        credits_per_song=int(t.credits_per_song or 1),
        model_version=t.model_version or None,
        description=t.description or None,
        enabled=bool(t.enabled),
    )


@router.put("/pricing-tiers/{tier_key}", response_model=PricingTierOut)
def update_pricing_tier(
    tier_key: str,
    body: PricingTierUpdate,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    t = get_tier(db, tier_key)
    if not t:
        raise HTTPException(status_code=404, detail="套餐不存在")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(t, k, v)
    db.commit()
    db.refresh(t)
    return PricingTierOut(
        id=t.id,
        tier_key=t.tier_key,
        tier_name=t.tier_name or None,
        engine_provider=t.engine_provider or None,
        price_cny=float(t.price_cny or 0),
        credits_per_song=int(t.credits_per_song or 1),
        model_version=t.model_version or None,
        description=t.description or None,
        enabled=bool(t.enabled),
    )


@router.delete("/pricing-tiers/{tier_key}")
def delete_pricing_tier(
    tier_key: str, db: Session = Depends(get_db), _: None = Depends(require_admin)
):
    t = get_tier(db, tier_key)
    if not t:
        raise HTTPException(status_code=404, detail="套餐不存在")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---------- 后台控制：总览 + 成本报表 + 恢复默认 ----------
@router.get("/config/dashboard", response_model=ConfigDashboardOut)
def config_dashboard(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    engines = [_engine_out(e) for e in list_engines(db)]
    routing = [
        RoutingRuleOut(
            id=r.id,
            category=r.category,
            provider=r.provider,
            priority=int(r.priority or 0),
            enabled=bool(r.enabled),
        )
        for r in list_routing(db)
    ]
    tiers = [
        PricingTierOut(
            id=t.id,
            tier_key=t.tier_key,
            tier_name=t.tier_name or None,
            engine_provider=t.engine_provider or None,
            price_cny=float(t.price_cny or 0),
            credits_per_song=int(t.credits_per_song or 1),
            description=t.description or None,
            enabled=bool(t.enabled),
        )
        for t in list_tiers(db)
    ]
    cost_by_engine = {
        row[0] or "unknown": float(row[1] or 0)
        for row in db.query(Song.engine_name, func.sum(Song.cost_cny))
        .group_by(Song.engine_name)
        .all()
    }
    cost_by_category = {
        row[0] or "unknown": float(row[1] or 0)
        for row in db.query(Song.content_category, func.sum(Song.cost_cny))
        .group_by(Song.content_category)
        .all()
    }
    revenue_by_tier = {
        row[0] or "free": float(row[1] or 0)
        for row in db.query(Song.tier_key, func.sum(Song.price_cny)).group_by(Song.tier_key).all()
    }
    margin_by_tier = {
        row[0] or "free": float(row[1] or 0)
        for row in db.query(Song.tier_key, func.sum(Song.margin_cny)).group_by(Song.tier_key).all()
    }
    revenue_by_category = {
        row[0] or "unknown": float(row[1] or 0)
        for row in db.query(Song.content_category, func.sum(Song.price_cny))
        .group_by(Song.content_category)
        .all()
    }
    margin_by_category = {
        row[0] or "unknown": float(row[1] or 0)
        for row in db.query(Song.content_category, func.sum(Song.margin_cny))
        .group_by(Song.content_category)
        .all()
    }
    total_cost = float(db.query(func.coalesce(func.sum(Song.cost_cny), 0)).scalar() or 0)
    total_revenue = float(db.query(func.coalesce(func.sum(Song.price_cny), 0)).scalar() or 0)
    total_margin = float(db.query(func.coalesce(func.sum(Song.margin_cny), 0)).scalar() or 0)
    margin_rate = (total_margin / total_revenue) if total_revenue > 0 else 0.0
    pending = (
        db.query(func.count(Song.id)).filter(Song.moderation_status == "pending").scalar() or 0
    )
    return ConfigDashboardOut(
        engines=engines,
        routing_rules=routing,
        pricing_tiers=tiers,
        cost_by_engine=cost_by_engine,
        cost_by_category=cost_by_category,
        revenue_by_tier=revenue_by_tier,
        margin_by_tier=margin_by_tier,
        revenue_by_category=revenue_by_category,
        margin_by_category=margin_by_category,
        total_cost_cny=round(total_cost, 4),
        total_revenue_cny=round(total_revenue, 4),
        total_margin_cny=round(total_margin, 4),
        margin_rate=round(margin_rate, 4),
        pending_moderation=int(pending),
    )


@router.post("/config/seed", response_model=ConfigDashboardOut)
def reseed_config(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    """恢复/补全默认配置（幂等；已存在的行不覆盖）。"""
    seed_default_config(db)
    return config_dashboard(db)
