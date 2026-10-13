"""管理后台：歌曲审核 + 引擎/路由/计费后台控制（多引擎路由后台控制核心）。"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.config import settings
from app.models import (
    Song,
    User,
    EngineConfig,
    RoutingRule,
    PricingTier,
    AuditLog,
    FeatureFlag,
    UserLink,
    ContentReport,
)
from app.billing.models import PaymentOrder, Subscription
from app.billing.service import apply_refund_event, _utcnow
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
from app.audit import log_moderation_action
from app.routers.auth import require_admin, get_current_user, _resolve_admin_role
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
    ModerationQueueItem,
    BatchModerationReq,
    BatchModerationOut,
    ModerationStatsOut,
    ReportOut,
    ReportResolveReq,
    AdminUserOut,
    AdminUsersOut,
    BanUserReq,
    ChangeTierReq,
    ChangeRoleReq,
    RefundReq,
    SettlementOut,
    FeatureFlagOut,
    FeatureFlagCreate,
    FeatureFlagUpdate,
    FeatureFlagsMineOut,
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


# ---------- GAP-016：审核动作审计 + 风险分级 ----------
def _actor(request) -> tuple[str, str]:
    """取当前 admin 的 (role, token_prefix)，用于审计。"""
    token = request.headers.get("X-Admin-Token") or request.cookies.get(settings.admin_cookie_name)
    role = _resolve_admin_role(token) or "none"
    return role, (token or "")[:6]


def _risk_level(song: Song) -> tuple[str, list[str]]:
    """审核队列**风险分级**：由列值推导的可解释等级，不引入玄学打分。

    规则（命中越高风险）：
      · high   音频机审未通过（moderation_note 含音频判定）/ 举报中的曲目
               —— 类为高危，人审须先看；
      · medium 文本机审曾命中（moderation_note 非空）或未走自动通过（pending 且无 note）；
      · low    其余（多为等待常规确认）。
    ⚠ 这里刻意不读 ContentReport 表（列表接口不该为每行再查一次举报），
      举报叠加的风险在 `moderation_stats` 里以 reports_open 单独呈现。
    """
    note = (song.moderation_note or "").strip()
    factors: list[str] = []
    level = "low"
    if note and ("音频" in note or "ASR" in note.upper()):
        level = "high"
        factors.append("音频机审未通过")
    if song.status == "failed":
        level = "high" if level == "high" else "medium"
        factors.append("生成失败")
    if note:
        if level == "low":
            level = "medium"
        factors.append("存在审核备注")
    if song.moderation_status == "pending" and not note:
        factors.append("等待人工确认")
    if not factors:
        factors.append("常规")
    return level, factors


def _to_queue_item(song: Song, email: str | None) -> ModerationQueueItem:
    level, factors = _risk_level(song)
    base = _to_admin_out(song, email)
    return ModerationQueueItem(**base.model_dump(), risk_level=level, risk_factors=factors)


_RISK_RANK = {"high": 0, "medium": 1, "low": 2}


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
        license=song.license or "non_commercial",
        created_at=song.created_at.isoformat() if song.created_at else "",
        user_email=email,
    )


@router.get("/songs", response_model=list[AdminSongOut])
def list_songs(
    moderation_status: str | None = None,
    status: str | None = None,
    include_trashed: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """作品清单。默认**不含回收站**（GAP-011 软删）。

    include_trashed=True 才会列出已软删的歌 —— 这个开关的存在理由是运营需要
    「这首歌到底还在不在」的答案，而用户侧的删除不该让它在后台凭空消失。
    """
    q = db.query(Song, User.email).join(User, User.id == Song.user_id, isouter=True)
    if not include_trashed:
        q = q.filter(Song.deleted_at.is_(None))
    if moderation_status:
        q = q.filter(Song.moderation_status == moderation_status)
    if status:
        q = q.filter(Song.status == status)
    rows = q.order_by(Song.id.desc()).limit(limit).offset(offset).all()
    return [_to_admin_out(song, email) for song, email in rows]


@router.post("/songs/{song_id}/approve", response_model=AdminSongOut)
def approve_song(
    song_id: int,
    request: Request,
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
    role, prefix = _actor(request)
    log_moderation_action(
        action="approve",
        target_type="song",
        target_id=song.id,
        actor_role=role,
        actor_token_prefix=prefix,
        path=f"/api/admin/songs/{song_id}/approve",
    )
    user = db.get(User, song.user_id) if song.user_id else None
    return _to_admin_out(song, user.email if user else None)


@router.post("/songs/{song_id}/reject", response_model=AdminSongOut)
def reject_song(
    song_id: int,
    body: RejectReq,
    request: Request,
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
    role, prefix = _actor(request)
    log_moderation_action(
        action="reject",
        target_type="song",
        target_id=song.id,
        actor_role=role,
        actor_token_prefix=prefix,
        detail=(body.note or "").strip(),
        path=f"/api/admin/songs/{song_id}/reject",
    )
    user = db.get(User, song.user_id) if song.user_id else None
    return _to_admin_out(song, user.email if user else None)


# ---------- GAP-016：审核队列增强（风险分级 / 批量 / 统计） ----------
_MAX_BATCH = 200  # 单次批量上限：防一次点掉全库（也是 UI 一次选择的上限）


@router.get("/moderation/queue", response_model=list[ModerationQueueItem])
def moderation_queue(
    risk_level: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """人审队列：默认只看 `pending`，按**风险分级**排序（high → medium → low）。

    `risk_level` 可筛选（high/medium/low）。分级依据见 `_risk_level` 的可解释规则。
    排序在 Python 侧做（队列本来只取 pending，规模可控；把打分塞进 SQL 反而难读）。
    """
    q = (
        db.query(Song, User.email)
        .join(User, User.id == Song.user_id, isouter=True)
        .filter(Song.moderation_status == "pending", Song.deleted_at.is_(None))
    )
    rows = q.order_by(Song.id.desc()).all()
    items = [_to_queue_item(song, email) for song, email in rows]
    if risk_level:
        items = [i for i in items if i.risk_level == risk_level]
    items.sort(key=lambda i: (_RISK_RANK.get(i.risk_level, 9), -i.id))
    start = max(0, offset)
    return items[start : start + max(1, min(int(limit), 200))]


@router.post("/moderation/batch", response_model=BatchModerationOut)
def batch_moderate(
    body: BatchModerationReq,
    action: str = "approve",
    request: Request = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """批量批准/驳回（`?action=approve|reject`）。

    · 每个变更曲目落一条 AuditLog（审核动作可审计，Req: #84）。
    · 已终态（approved/rejected）的曲目**跳过**而非静默覆盖：批量操作不该
      悄悄推翻别人已经做出的判定。
    · 驳回同样撤销公开（与单条 reject 一致，见上）。
    """
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 仅支持 approve|reject")
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    if len(body.ids) > _MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"单次批量上限 {_MAX_BATCH} 条")
    if action == "reject" and not (body.note or "").strip():
        # 驳回必须给理由：无理由驳回让作者/运营无从申诉与追溯。
        raise HTTPException(status_code=400, detail="批量驳回必须提供 note 原因")

    role, prefix = _actor(request) if request is not None else ("none", "")
    approved = rejected = skipped = 0
    changed: list[int] = []
    for sid in dict.fromkeys(body.ids):  # 去重且保持顺序
        song = db.get(Song, sid)
        if not song or song.deleted_at is not None:
            skipped += 1
            continue
        if song.moderation_status != "pending":
            skipped += 1
            continue
        if action == "approve":
            song.moderation_status = "approved"
            song.moderation_note = ""
            approved += 1
            changed.append(song.id)
            log_moderation_action(
                action="approve",
                target_type="song",
                target_id=song.id,
                actor_role=role,
                actor_token_prefix=prefix,
                detail="批量批准",
                path="/api/admin/moderation/batch",
            )
        else:
            song.moderation_status = "rejected"
            song.moderation_note = (body.note or "").strip()
            song.is_public = False
            rejected += 1
            changed.append(song.id)
            log_moderation_action(
                action="reject",
                target_type="song",
                target_id=song.id,
                actor_role=role,
                actor_token_prefix=prefix,
                detail=(body.note or "").strip(),
                path="/api/admin/moderation/batch",
            )
    db.commit()
    return BatchModerationOut(approved=approved, rejected=rejected, skipped=skipped, ids=changed)


@router.get("/moderation/stats", response_model=ModerationStatsOut)
def moderation_stats(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    """审核效率统计：队列积压 + 风险分布 + 举报计数。"""
    pending_rows = (
        db.query(Song)
        .filter(Song.moderation_status == "pending", Song.deleted_at.is_(None))
        .all()
    )
    by_risk = {"high": 0, "medium": 0, "low": 0}
    for s in pending_rows:
        level, _ = _risk_level(s)
        by_risk[level] = by_risk.get(level, 0) + 1
    by_mod = {
        k: v
        for k, v in db.query(Song.moderation_status, func.count(Song.id))
        .filter(Song.deleted_at.is_(None))
        .group_by(Song.moderation_status)
        .all()
    }
    reports_open = (
        db.query(func.count(ContentReport.id)).filter(ContentReport.status == "open").scalar() or 0
    )
    reports_resolved = (
        db.query(func.count(ContentReport.id))
        .filter(ContentReport.status != "open")
        .scalar()
        or 0
    )
    return ModerationStatsOut(
        pending=len(pending_rows),
        approved=int(by_mod.get("approved", 0)),
        rejected=int(by_mod.get("rejected", 0)),
        by_risk=by_risk,
        reports_open=int(reports_open),
        reports_resolved=int(reports_resolved),
    )


# ---------- GAP-016：举报 / DMCA 处置（人工下架流程） ----------
def _report_out(r: ContentReport, title: str | None = None) -> ReportOut:
    return ReportOut(
        id=r.id,
        song_id=r.song_id,
        reporter_user_id=r.reporter_user_id,
        reporter_email=r.reporter_email or None,
        reason=r.reason,
        detail=r.detail or None,
        status=r.status,
        resolution=r.resolution or None,
        resolved_by=r.resolved_by or None,
        resolved_at=r.resolved_at.isoformat() if r.resolved_at else None,
        created_at=r.created_at.isoformat() if r.created_at else None,
        song_title=title,
    )


@router.get("/reports", response_model=list[ReportOut])
def list_reports(
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """举报列表（可按 status=open|resolved|dismissed 过滤）。"""
    q = db.query(ContentReport, Song.title).join(Song, Song.id == ContentReport.song_id, isouter=True)
    if status:
        q = q.filter(ContentReport.status == status)
    rows = (
        q.order_by(ContentReport.id.desc())
        .limit(max(1, min(int(limit), 500)))
        .offset(max(0, offset))
        .all()
    )
    return [_report_out(r, title) for r, title in rows]


@router.post("/reports/{report_id}/resolve", response_model=ReportOut)
def resolve_report(
    report_id: int,
    body: ReportResolveReq,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """处置举报：`takedown`（下架曲目 + 撤销公开 + 标记驳回）或 `rejected`（举报不成立）。

    人工下架流程的闭环：举报 → 管理员判定 → （若下架）曲目失去公开可见性
    且被标记 rejected，避免「从广场消失但直连仍在」的老问题（同 reject_song 的 SEC 注释）。
    """
    if body.resolution not in ("takedown", "rejected"):
        raise HTTPException(status_code=400, detail="resolution 仅支持 takedown|rejected")
    rep = db.get(ContentReport, report_id)
    if not rep:
        raise HTTPException(status_code=404, detail="举报不存在")
    role, prefix = _actor(request)

    song = db.get(Song, rep.song_id)
    if body.resolution == "takedown":
        if song is not None:
            song.is_public = False
            song.moderation_status = "rejected"
            song.moderation_note = (body.note or "").strip() or "举报成立，人工下架"
        rep.status = "resolved"
    else:
        rep.status = "dismissed"
    rep.resolution = body.resolution
    rep.resolved_by = f"{role}:{prefix}"
    rep.resolved_at = func.now()
    db.commit()
    db.refresh(rep)

    log_moderation_action(
        action="report_resolve",
        target_type="report",
        target_id=rep.id,
        actor_role=role,
        actor_token_prefix=prefix,
        detail=f"{body.resolution}:{(body.note or '').strip()}",
        path=f"/api/admin/reports/{report_id}/resolve",
    )
    if song is not None and body.resolution == "takedown":
        log_moderation_action(
            action="takedown",
            target_type="song",
            target_id=song.id,
            actor_role=role,
            actor_token_prefix=prefix,
            detail=(body.note or "").strip(),
            path=f"/api/admin/reports/{report_id}/resolve",
        )
    title = db.query(Song.title).filter(Song.id == rep.song_id).scalar()
    return _report_out(rep, title)


@router.get("/moderation/health")
def moderation_health(_: None = Depends(require_admin)):
    """审核链路就绪状态：文本词库 / 音频 ASR / 云审核配置（GAP-016）。

    运维价值：`enable_audio_moderation=True` 但 ASR 未配置时，音频机审**fail-closed**
    拦下所有音频（不是放行）。这个端点把该状态直接暴露出来，避免「开了开关却没接
    ASR」在生产里被误读成「审核已生效」。
    """
    from app import moderation

    return moderation.describe_state()


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


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-023 运营扩展：用户管理 / 财务结算导出 / 特性开关
#
# 三条共同原则（贯穿本段落）：
#   1. **写动作必审计**：封禁/改档/改角色/退款/开关变更全部落 AuditLog
#      （结构化字段 action/target_type/target_id/detail），不是只靠 middleware 的
#      访问日志 —— "谁在什么时候把谁封了" 必须能独立查出，而不是在 200 条 GET 里翻。
#   2. **不返回凭据材料**：admin 看得到邮箱（运营需要），但任何哈希/密钥/token 一律不出。
#   3. **viewer 只读**：变更类接口由 main.admin_rbac_audit 中间件对 viewer 直接 403
#      （既有机制，不在这里重复实现 RBAC）。
# ═══════════════════════════════════════════════════════════════════════════════

MAX_CSV_ROWS = 10000  # 导出上限：CSV 由 HTTP 一次性下发，不是流式；无上限会打爆内存


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _admin_action(
    request: Request,
    *,
    action: str,
    target_type: str,
    target_id: int,
    detail: str = "",
) -> None:
    """写一条运营动作审计（GAP-023）。

    复用 `app.audit.log_moderation_action`：它的语义是"结构化 admin 动作入 audit_logs"，
    与 GAP-016 的审核动作同表同构 —— 另起一张 operation_logs 会让"这个 admin 都干过什么"
    变成两次查询。
    """
    role, prefix = _actor(request)
    path = str(request.url.path)
    ip = request.client.host if request.client else ""
    log_moderation_action(
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_role=role,
        actor_token_prefix=prefix,
        detail=detail,
        path=path,
        client_ip=ip,
    )


def _csv_response(rows: list[list], header: list[str], filename: str):
    """CSV 下载响应。

    ⚠ 必须加 UTF-8 BOM：Excel（尤其中文版）默认按本地代码页打开无 BOM 的 UTF-8，
      中文列名与用户邮箱会显示成乱码 —— 这张表是给运营/财务在 Excel 里看的，
      乱码等于导出不可用。

    `Content-Disposition` 用 ASCII 文件名（filename=）+ UTF-8 文件名（filename*）双写：
      只写中文名会被部分客户端截断成乱码文件；只写 ASCII 名则中文名丢失。
    """
    import csv
    import io as _io

    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    body = "\ufeff" + buf.getvalue()
    ascii_name = "".join(c if c.isascii() and (c.isalnum() or c in "-_.") else "_" for c in filename)
    from urllib.parse import quote

    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


def _parse_csv_ids(raw: str | None) -> list[int]:
    """"1,2,3" → [1,2,3]（跳过非法段，见 app/feature_flags.py:_parse_ids 的同一取舍）。"""
    out: list[int] = []
    for part in (raw or "").replace(" ", "").split(","):
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


# ---------- GAP-023 · 用户管理 ----------
@router.get("/users", response_model=AdminUsersOut)
def list_users(
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
    q: str | None = None,
    tier_key: str | None = None,
    banned: bool | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """用户列表（搜索 + 筛选 + 分页）。

    `q` 同时匹配邮箱与 id：运营手上通常只有一个信息（要么"用户报的邮箱"，
    要么"某条日志里的 user_id"），分成两个参数会让人先猜再试。

    返回总条数（total）+ 当前页（items）：前端分页需要总数才能显示第几页/共几页，
    只回 items 的接口在超过一页时必然要做一次"有没有下一页"的试探请求。
    """

    query = db.query(User)
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        cond = User.email.ilike(like)
        if term.isdigit():
            cond = or_(cond, User.id == int(term))
        query = query.filter(cond)
    if tier_key:
        # tier_key 为空/none 时等价于 free（未订阅），这是运营口径里的"免费用户"。
        if tier_key == "free":
            query = query.filter(or_(User.tier_key.is_(None), User.tier_key == "", User.tier_key == "free"))
        else:
            query = query.filter(User.tier_key == tier_key)
    if banned is not None:
        query = query.filter(User.is_banned.is_(True) if banned else User.is_banned.is_(False))

    total = query.count()
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    users = query.order_by(User.id.desc()).limit(limit).offset(offset).all()
    ids = [u.id for u in users]

    # 曲数 / 实付金额用两条聚合一次取全，而不是每行再查一次（N+1）。
    song_counts = dict(
        db.query(Song.user_id, func.count(Song.id)).filter(Song.user_id.in_(ids or [0])).group_by(Song.user_id).all()
    )
    paid_map = dict(
        db.query(PaymentOrder.user_id, func.sum(PaymentOrder.amount_cny))
        .filter(PaymentOrder.user_id.in_(ids or [0]), PaymentOrder.status == "paid")
        .group_by(PaymentOrder.user_id)
        .all()
    )
    items = [
        AdminUserOut(
            id=u.id,
            email=u.email,
            tier_key=u.tier_key or None,
            role="user",  # 角色由 token 层承担（见 ChangeRoleReq 注释）
            email_verified=bool(u.email_verified),
            mfa_enabled=bool(u.mfa_enabled),
            is_banned=bool(u.is_banned),
            banned_at=_iso(u.banned_at),
            ban_reason=u.ban_reason or None,
            locked_until=_iso(u.locked_until),
            failed_logins=int(u.failed_logins or 0),
            song_count=int(song_counts.get(u.id, 0)),
            paid_cny=round(float(paid_map.get(u.id) or 0), 2),
            created_at=_iso(u.created_at),
        )
        for u in users
    ]
    return AdminUsersOut(total=int(total), items=items)


@router.get("/users/{user_id}", response_model=AdminUserOut)
def get_user(user_id: int, db: Session = Depends(get_db), _: None = Depends(require_admin)):

    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    song_count = (
        db.query(func.count(Song.id)).filter(Song.user_id == u.id).scalar() or 0
    )
    paid = (
        db.query(func.coalesce(func.sum(PaymentOrder.amount_cny), 0))
        .filter(PaymentOrder.user_id == u.id, PaymentOrder.status == "paid")
        .scalar()
        or 0
    )
    return AdminUserOut(
        id=u.id,
        email=u.email,
        tier_key=u.tier_key or None,
        role="user",
        email_verified=bool(u.email_verified),
        mfa_enabled=bool(u.mfa_enabled),
        is_banned=bool(u.is_banned),
        banned_at=_iso(u.banned_at),
        ban_reason=u.ban_reason or None,
        locked_until=_iso(u.locked_until),
        failed_logins=int(u.failed_logins or 0),
        song_count=int(song_count),
        paid_cny=round(float(paid), 2),
        created_at=_iso(u.created_at),
    )


@router.post("/users/{user_id}/ban", response_model=AdminUserOut)
def ban_user(
    user_id: int,
    body: BanUserReq,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """封禁用户：标记 + 清会话凭据材料（同时失效现有登录态与全部待用 token）。

    ⚠ 本产品**没有**会话表 / jti 黑名单（GAP-044 未做），所以"踢下线"只能靠
      `jwt_secret` 轮换所依赖的同一件事：让令牌解不出来。这里改用**最小可行动作**：
        · 清 `reset_token` / `verify_token`（封禁账号不该还能走找回密码流程）；
        · 清 `mfa_secret` 并把 `mfa_enabled=False`（封禁期间二次认证凭据无留存必要）；
      已签发的 JWT 在过期前**仍然有效**（最长 24h）—— 这是既有架构的已知边界，
      不在这里假装解决（真要即时吊销需要 GAP-044 的 jti 黑名单，属另一任务）。
      所以封禁的**主**作用在登录入口（见 routers/auth.py 的封禁判定）而非当前会话。
    """
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    reason = (body.reason or "").strip()
    if not reason:
        # 无理由封禁在申诉/审计时无法自证 —— 与审核驳回必须填理由同一条规矩。
        raise HTTPException(status_code=400, detail="封禁必须填写理由")
    already = bool(u.is_banned)
    u.is_banned = True
    u.banned_at = _utcnow()
    u.ban_reason = reason[:500]
    u.reset_token = None
    u.verify_token = None
    u.mfa_secret = None
    u.mfa_enabled = False
    db.commit()
    db.refresh(u)
    _admin_action(
        request,
        action="user_ban",
        target_type="user",
        target_id=u.id,
        detail=f"{'重复封禁' if already else '封禁'}：{reason}",
    )
    return get_user(u.id, db=db, _=None)


@router.post("/users/{user_id}/unban", response_model=AdminUserOut)
def unban_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """解封用户。理由留痕为"解封"本身（不需要额外说明，动作即事实）。"""
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    was = bool(u.is_banned)
    prev_reason = u.ban_reason or ""
    u.is_banned = False
    u.banned_at = None
    u.ban_reason = ""
    # 解封同时清掉登录失败锁定：被无理由封过的账号若还卡在锁定里，用户会以为"解封没用"。
    u.failed_logins = 0
    u.locked_until = None
    db.commit()
    db.refresh(u)
    _admin_action(
        request,
        action="user_unban",
        target_type="user",
        target_id=u.id,
        detail=f"解封（原状态={'已封禁' if was else '本就未封'}，原理由={prev_reason[:200]}）",
    )
    return get_user(u.id, db=db, _=None)


@router.post("/users/{user_id}/tier", response_model=AdminUserOut)
def change_user_tier(
    user_id: int,
    body: ChangeTierReq,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """改套餐（客服补偿 / 商务对账 / 误扣回退）。

    ⚠ 合法档位取自 `entitlements.VALID_TIERS`（单一真相源），不在这里重列一份：
      重列必然在新增档位时漏改，而漏改的后果是"后台改不了这个新档位"。
    """
    from app.entitlements import VALID_TIERS

    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    tier = (body.tier_key or "").strip().lower()
    if tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"未知套餐 {tier!r}（可选：{', '.join(VALID_TIERS)}）")
    before = u.tier_key or "free"
    if before == tier:
        raise HTTPException(status_code=409, detail=f"用户已是 {tier}，无需变更")
    u.tier_key = None if tier == "free" else tier
    db.commit()
    db.refresh(u)
    _admin_action(
        request,
        action="user_tier_change",
        target_type="user",
        target_id=u.id,
        detail=f"{before} → {tier}",
    )
    return get_user(u.id, db=db, _=None)


@router.post("/users/{user_id}/role", response_model=AdminUserOut)
def change_user_role(
    user_id: int,
    body: ChangeRoleReq,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """角色调整：**显式拒绝**，而不是假装成功。

    本产品的管理员身份是**共享静态密钥**（`admin_token` / `admin_viewer_token`，
    见 config.py），`User` 表里没有 role 列，也没有 AdminUser 表 —— 换言之
    "某用户的角色"这个东西在当前架构里根本不存在。若这里接受 role=admin 并
    返回 200，运维会以为"已经给了他后台权限"，而实际上什么都没有发生
    （真正的门是那两个 token）。所以这里返回 501 + 明确的替代做法。

    真正的角色体系（AdminUser 表 + 每管理员账号 + 权限分离）是规划中的
    GAP-006 完整形态/GAP-140，属独立任务，不在本次范围内偷做一半。
    """
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    role = (body.role or "").strip().lower()
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="role 只能是 user 或 admin")
    raise HTTPException(
        status_code=501,
        detail=(
            "当前架构无「用户角色」概念：管理员身份是共享密钥(admin_token/admin_viewer_token)，"
            "存储在两处并轮换，与 users 表无关。要授予后台权限，请轮换 ADMIN_TOKEN / "
            "ADMIN_VIEWER_TOKEN 环境变量并分发给对应人员（不要写进 git）。"
            "完整的每管理员账号体系（AdminUser + 权限分离）属 GAP-006 完整形态，另行排期。"
        ),
    )


# ---------- GAP-023 · 财务：结算 / 退款 / 发票导出（CSV） ----------
def _order_filters(q, status: str | None, provider: str | None, user_id: int | None, since=None):
    if status:
        q = q.filter(PaymentOrder.status == status)
    if provider:
        q = q.filter(PaymentOrder.provider == provider)
    if user_id is not None:
        q = q.filter(PaymentOrder.user_id == int(user_id))
    if since is not None:
        q = q.filter(PaymentOrder.created_at >= since)
    return q


@router.get("/settlement", response_model=SettlementOut)
def settlement(
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
    days: int | None = None,
):
    """财务结算汇总（收入侧），与 `/admin/billing`（成本侧对账）互补。

    `days` 缺省 = 全量累计。给窗口是因为财务对账通常是"本月的"，
    而 `created_at >= now - N days` 这条边界必须在**服务端**算（见下方注释）。
    """
    from app.billing.models import Subscription

    q = db.query(PaymentOrder)
    since = None
    if days and int(days) > 0:
        since = _utcnow() - timedelta(days=int(days))
        q = q.filter(PaymentOrder.created_at >= since)

    by_status: dict[str, int] = {}
    for st, cnt in q.with_entities(PaymentOrder.status, func.count(PaymentOrder.id)).group_by(PaymentOrder.status).all():
        by_status[st or "unknown"] = int(cnt)

    # 金额以 `amount_cny`（结算等价人民币）求和，**不**用 `amount`（本币面值）：
    # 多币种订单直接相加是本币混算，得到的数字没有任何财务含义。
    paid_amount = float(
        q.filter(PaymentOrder.status == "paid")
        .with_entities(func.coalesce(func.sum(PaymentOrder.amount_cny), 0))
        .scalar()
        or 0
    )
    refunded_amount = float(
        q.filter(PaymentOrder.status == "refunded")
        .with_entities(func.coalesce(func.sum(PaymentOrder.amount_cny), 0))
        .scalar()
        or 0
    )
    by_provider = {
        row[0] or "unknown": round(float(row[1] or 0), 2)
        for row in q.filter(PaymentOrder.status == "paid")
        .with_entities(PaymentOrder.provider, func.sum(PaymentOrder.amount_cny))
        .group_by(PaymentOrder.provider)
        .all()
    }
    by_tier = {
        row[0] or "unknown": round(float(row[1] or 0), 2)
        for row in q.filter(PaymentOrder.status == "paid")
        .with_entities(PaymentOrder.tier_key, func.sum(PaymentOrder.amount_cny))
        .group_by(PaymentOrder.tier_key)
        .all()
    }

    total_cost = float(db.query(func.coalesce(func.sum(Song.cost_cny), 0)).scalar() or 0)
    total_revenue = float(db.query(func.coalesce(func.sum(Song.price_cny), 0)).scalar() or 0)
    total_margin = float(db.query(func.coalesce(func.sum(Song.margin_cny), 0)).scalar() or 0)
    return SettlementOut(
        currency="CNY",
        order_count=int(sum(by_status.values())),
        paid_count=by_status.get("paid", 0),
        refunded_count=by_status.get("refunded", 0),
        pending_count=by_status.get("pending", 0),
        failed_count=by_status.get("failed", 0),
        paid_amount_cny=round(paid_amount, 2),
        refunded_amount_cny=round(refunded_amount, 2),
        net_amount_cny=round(paid_amount - refunded_amount, 2),
        by_provider=by_provider,
        by_tier=by_tier,
        active_subscriptions=int(
            db.query(func.count(Subscription.id)).filter(Subscription.status == "active").scalar() or 0
        ),
        total_songs=int(db.query(func.count(Song.id)).scalar() or 0),
        total_song_cost_cny=round(total_cost, 4),
        total_song_revenue_cny=round(total_revenue, 4),
        total_song_margin_cny=round(total_margin, 4),
    )


@router.get("/settlement/export")
def settlement_export(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
    status: str | None = None,
    provider: str | None = None,
    user_id: int | None = None,
    days: int | None = None,
    limit: int = MAX_CSV_ROWS,
):
    """结算明细 CSV 导出（运维/财务对账用）。

    每一行 = 一笔订单的**事实**（币种/面值/折人民币/状态/时间），不做任何汇总或
    换算推断 —— 汇总在 `/settlement`，CSV 的职责是"原始明细，可被审计核对"。
    """

    q = db.query(PaymentOrder)
    if days and int(days) > 0:
        q = q.filter(PaymentOrder.created_at >= _utcnow() - timedelta(days=int(days)))
    q = _order_filters(q, status, provider, user_id)
    rows = q.order_by(PaymentOrder.id.desc()).limit(max(1, min(int(limit), MAX_CSV_ROWS))).all()

    emails = dict(
        db.query(User.id, User.email).filter(User.id.in_([r.user_id for r in rows] or [0])).all()
    )
    data = [
        [
            o.id,
            o.user_id,
            # 邮箱是**运营定位账号的必需信息**（对账时"这笔钱是谁付的"必须能答），
            # 但它只出现在 admin 鉴权后的下载里，不进任何公开接口。
            emails.get(o.user_id, ""),
            o.provider,
            o.tier_key,
            o.status,
            o.currency,
            float(o.amount or 0),
            float(o.amount_cny or 0),
            o.provider_order_id or "",
            _iso(o.paid_at) or "",
            _iso(o.created_at) or "",
        ]
        for o in rows
    ]
    _admin_action(
        request,
        action="settlement_export",
        target_type="settlement",
        target_id=0,
        detail=f"导出订单明细 {len(data)} 行（status={status or 'all'} provider={provider or 'all'}）",
    )
    return _csv_response(
        data,
        [
            "order_id",
            "user_id",
            "user_email",
            "provider",
            "tier_key",
            "status",
            "currency",
            "amount",
            "amount_cny",
            "provider_order_id",
            "paid_at",
            "created_at",
        ],
        f"settlement-{_utcnow().strftime('%Y%m%d-%H%M%S')}.csv",
    )


@router.get("/invoices/export")
def invoices_export(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
    days: int | None = None,
):
    """发票台账 CSV：**仅已实收**订单（status=paid）一行一张票。

    ⚠ 这不是"开票操作"，是**开票台账**：本产品未接税务/开票系统，无法生成带
      发票号的合规电子发票。这里输出的是财务去开票系统录入所需的字段集，
      并在响应里以 `invoice_status=to_issue` 明确标注"待开" —— 不臆造票号。

    与 `/settlement/export` 的区别（两者不是重复）：
      · settlement = 全部订单（含 pending/failed/refunded），对账用；
      · invoices   = 只有付过钱的，开票用。混在一起财务会把未付款的也算进开票额。
    """

    q = db.query(PaymentOrder).filter(PaymentOrder.status == "paid")
    if days and int(days) > 0:
        q = q.filter(PaymentOrder.created_at >= _utcnow() - timedelta(days=int(days)))
    rows = q.order_by(PaymentOrder.id.desc()).limit(MAX_CSV_ROWS).all()
    emails = dict(
        db.query(User.id, User.email).filter(User.id.in_([r.user_id for r in rows] or [0])).all()
    )
    data = [
        [
            o.id,
            o.user_id,
            emails.get(o.user_id, ""),
            o.tier_key,
            o.currency,
            float(o.amount or 0),
            float(o.amount_cny or 0),
            _iso(o.paid_at) or "",
            "to_issue",  # 明确"待开票"，不伪造发票号
        ]
        for o in rows
    ]
    _admin_action(
        request,
        action="invoices_export",
        target_type="invoice",
        target_id=0,
        detail=f"导出发票台账 {len(data)} 行",
    )
    return _csv_response(
        data,
        [
            "order_id",
            "user_id",
            "user_email",
            "tier_key",
            "currency",
            "amount",
            "amount_cny",
            "paid_at",
            "invoice_status",
        ],
        f"invoices-{_utcnow().strftime('%Y%m%d-%H%M%S')}.csv",
    )


@router.post("/orders/{order_id}/refund")
def refund_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
    body: RefundReq | None = None,
):
    """标记订单退款 + 撤销订阅权限（**本地账务动作**，不调用任何支付网关）。

    ⚠ 边界必须说清（拒绝幻觉）：
      · 本端点**不发起**真实退款。供应商侧的钱要退，仍需在 Stripe/KakaoPay 等
        后台操作，或等其 `charge.refunded` 回调打到 `/api/billing/webhook/{provider}`。
      · 本端点的用途是"钱已退（或已决定退），把本站的账与权限同步过来" ——
        否则会出现"钱退了、会员还在"的账实不符，这正是运营最常踩的坑。
      · 若供应商随后回调同一笔，`apply_refund_event` 是幂等的（已 refunded 不二次改写），
        两条路径不会互相打架。
    """

    order = db.get(PaymentOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    if order.status == "refunded":
        raise HTTPException(status_code=409, detail="该订单已退款（幂等），无需重复操作")

    reason = (body.reason if body else None) or ""
    # 复用 billing 的退款闭环：订单置 refunded + 订阅 cancelled + 权限降回 free。
    # 不在这里重写那套状态机 —— 重复实现必然与 webhook 路径分叉。
    res = apply_refund_event(db, provider=order.provider, provider_order_id=order.provider_order_id)
    if not res.get("found"):
        # provider_order_id 为空的订单（manual 模式常见）无法被上面的解析链命中，
        # 退化为"按订单本身退款"：同样的终态，只是不走 provider 反查。
        order.status = "refunded"
        fallback_sub = db.query(Subscription).filter(Subscription.user_id == order.user_id).first()
        if fallback_sub is not None:
            fallback_sub.status = "cancelled"
            fallback_sub.cancel_at_period_end = False
            fallback_sub.current_period_end = _utcnow()
        from app.billing.service import _revoke_entitlement, FREE_TIER

        _revoke_entitlement(db, user_id=order.user_id)
        db.commit()
        res = {
            "found": True,
            "order_id": order.id,
            "user_id": order.user_id,
            "tier_key": FREE_TIER,
            "resolved_by": "order_id_fallback",
        }
    _admin_action(
        request,
        action="order_refund",
        target_type="order",
        target_id=order.id,
        detail=f"退款 order={order.id} user={order.user_id} 金额={float(order.amount_cny or 0)} {order.currency}"
        + (f" 原因={reason}" if reason else ""),
    )
    return {"ok": True, "order_id": order.id, "status": "refunded", "detail": res}


# ---------- GAP-023 · 特性开关（灰度发布基础） ----------
def _flag_out(f: FeatureFlag) -> FeatureFlagOut:
    return FeatureFlagOut(
        id=f.id,
        key=f.key,
        description=f.description or "",
        enabled=bool(f.enabled),
        rollout=int(f.rollout or 0),
        allow_user_ids=f.allow_user_ids or "",
        deny_user_ids=f.deny_user_ids or "",
        min_tier=f.min_tier or "",
        updated_at=_iso(f.updated_at),
        created_at=_iso(f.created_at),
    )


@router.get("/feature-flags", response_model=list[FeatureFlagOut])
def list_feature_flags(db: Session = Depends(get_db), _: None = Depends(require_admin)):
    """全部开关（含关闭的）—— admin 需要看到"有哪些开关存在"。"""
    return [_flag_out(f) for f in db.query(FeatureFlag).order_by(FeatureFlag.key).all()]


@router.post("/feature-flags", response_model=FeatureFlagOut, status_code=201)
def create_feature_flag(
    body: FeatureFlagCreate,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """新建开关。默认关闭（enabled=False / rollout=0）—— 建开关不该顺手放量。"""
    from app.feature_flags import normalize_key

    try:
        key = normalize_key(body.key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if db.query(FeatureFlag).filter(FeatureFlag.key == key).first():
        raise HTTPException(status_code=409, detail=f"开关 {key} 已存在")
    rollout = int(body.rollout or 0)
    if not 0 <= rollout <= 100:
        raise HTTPException(status_code=400, detail="rollout 必须在 0–100 之间")
    f = FeatureFlag(
        key=key,
        description=(body.description or "")[:255],
        enabled=bool(body.enabled),
        rollout=rollout,
        allow_user_ids=(body.allow_user_ids or "")[:2000],
        deny_user_ids=(body.deny_user_ids or "")[:2000],
        min_tier=(body.min_tier or "").strip().lower()[:32],
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    _admin_action(
        request,
        action="feature_flag_create",
        target_type="feature_flag",
        target_id=f.id,
        detail=f"{f.key} enabled={bool(f.enabled)} rollout={f.rollout}",
    )
    return _flag_out(f)


@router.put("/feature-flags/{key}", response_model=FeatureFlagOut)
def update_feature_flag(
    key: str,
    body: FeatureFlagUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
):
    """修改开关。detail 里记录**变更前后**的值 —— 灰度事故复盘时，
    "当时 rollout 是多少"比"改过"重要得多。"""
    from app.feature_flags import normalize_key

    try:
        k = normalize_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    f = db.query(FeatureFlag).filter(FeatureFlag.key == k).first()
    if not f:
        raise HTTPException(status_code=404, detail="开关不存在")

    before = f"enabled={bool(f.enabled)} rollout={f.rollout} min_tier={f.min_tier or '-'} " \
             f"allow={len(_parse_csv_ids(f.allow_user_ids))} deny={len(_parse_csv_ids(f.deny_user_ids))}"
    data = body.model_dump(exclude_unset=True, exclude_none=True)
    if "rollout" in data:
        rollout = int(data["rollout"])
        if not 0 <= rollout <= 100:
            raise HTTPException(status_code=400, detail="rollout 必须在 0–100 之间")
        f.rollout = rollout
    if "enabled" in data:
        f.enabled = bool(data["enabled"])
    if "description" in data:
        f.description = str(data["description"])[:255]
    if "allow_user_ids" in data:
        f.allow_user_ids = str(data["allow_user_ids"])[:2000]
    if "deny_user_ids" in data:
        f.deny_user_ids = str(data["deny_user_ids"])[:2000]
    if "min_tier" in data:
        f.min_tier = str(data["min_tier"]).strip().lower()[:32]
    db.commit()
    db.refresh(f)
    after = f"enabled={bool(f.enabled)} rollout={f.rollout} min_tier={f.min_tier or '-'} " \
            f"allow={len(_parse_csv_ids(f.allow_user_ids))} deny={len(_parse_csv_ids(f.deny_user_ids))}"
    _admin_action(
        request,
        action="feature_flag_update",
        target_type="feature_flag",
        target_id=f.id,
        detail=f"{f.key} {before} → {after}",
    )
    return _flag_out(f)


@router.delete("/feature-flags/{key}")
def delete_feature_flag(
    key: str, request: Request, db: Session = Depends(get_db), _: None = Depends(require_admin)
):
    """删除开关（= 对所有人生效地关闭）。用于"功能已全量/已下线，开关使命结束"。"""
    from app.feature_flags import normalize_key

    try:
        k = normalize_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    f = db.query(FeatureFlag).filter(FeatureFlag.key == k).first()
    if not f:
        raise HTTPException(status_code=404, detail="开关不存在")
    fid = f.id
    db.delete(f)
    db.commit()
    _admin_action(
        request,
        action="feature_flag_delete",
        target_type="feature_flag",
        target_id=fid,
        detail=f"删除开关 {k}",
    )
    return {"ok": True, "key": k}


@router.get("/feature-flags/{key}/preview")
def preview_feature_flag(
    key: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
    user_id: int | None = None,
    tier_key: str | None = None,
):
    """判定预览：**不落库**，回答"按现在的配置，这个用户会看到吗？"

    灰度期最常问的问题就是这个，而靠读 enable/rollout 字段心算
    （hash 取模 + 名单 + 档位门槛三层）几乎必错。这里直接给结论 + 逐层依据。
    """
    from app.feature_flags import normalize_key, is_enabled, _parse_ids, _in_rollout, _meets_tier

    try:
        k = normalize_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    f = db.query(FeatureFlag).filter(FeatureFlag.key == k).first()
    if not f:
        raise HTTPException(status_code=404, detail="开关不存在")

    steps = {
        "in_deny_list": bool(user_id is not None and int(user_id) in _parse_ids(f.deny_user_ids)),
        "in_allow_list": bool(user_id is not None and int(user_id) in _parse_ids(f.allow_user_ids)),
        "platform_enabled": bool(f.enabled),
        "meets_min_tier": _meets_tier(f.min_tier or "", tier_key),
        "in_rollout": _in_rollout(f.key, user_id, int(f.rollout or 0)),
    }
    return {
        "key": f.key,
        "user_id": user_id,
        "tier_key": tier_key,
        "enabled": is_enabled(f, user_id=user_id, tier_key=tier_key),
        "steps": steps,
        # 优先级与其他判定同一份实现（见 app/feature_flags.py 模块头），此处只是回显。
        "rule": "deny 名单 → allow 名单 → enabled × min_tier × rollout",
    }


# ---------- GAP-023 · 前端读取（任一登录用户，非 admin） ----------
# ⚠ 路径刻意**不**用 `/feature-flags/mine`：它与 `/feature-flags/{key}` 的
#   GET 形态在同级路径上撞车（{key} 会把 "mine" 当成一个 key 吃掉，谁先声明谁赢）。
#   用一个不会被误解成 key 的路径，把"路由顺序"这个隐性依赖直接消灭。
@router.get("/feature-flags-mine", response_model=FeatureFlagsMineOut)
def my_feature_flags(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前用户**已生效**的开关集合（灰度下发入口）。

    ⚠ 路径 `/api/admin/feature-flags-mine` 落在 admin 前缀下（沿用同一个 APIRouter），
      但语义是**面向终端用户**的读取端点，因此鉴权用 `get_current_user`（普通 JWT）
      而不是 `require_admin`。

    为什么不做成"任意登录用户可读的公开端点"：灰度名单/比例属于运营内部信息，
    下发**结论**（这个功能对你可见）即可，不下发**规则**（你被灰度是因为你在名单里）。
    """
    from app.feature_flags import resolve_for_user

    return FeatureFlagsMineOut(flags=resolve_for_user(db, user.id, getattr(user, "tier_key", None)))
