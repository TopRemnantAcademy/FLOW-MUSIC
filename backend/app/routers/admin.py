"""管理后台：歌曲审核 + 引擎/路由/计费后台控制（多引擎路由后台控制核心）。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
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
    UserLink,
    ContentReport,
)
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
from app.audit import log_moderation_action
from app.routers.auth import require_admin, _resolve_admin_role
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
