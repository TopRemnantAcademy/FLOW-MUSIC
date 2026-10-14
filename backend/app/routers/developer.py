"""GAP-022 开放平台路由：开发者凭证 + Webhook 订阅。

认证方式与其它路由**不同**：这里用 `Authorization: Bearer <flow_xxx>`（不透明
开发者 token），不是登录 JWT。两者在 `app/developer/tokens.py` 里彻底分开 ——
不共用 cookie、不共用解析路径，避免"浏览器登录一下就能调开放 API"这种权限串味。

三个 scope：
  · `songs:read`        —— 读自己的作品（本批暴露 `GET /api/developer/songs`）
  · `songs:write`       —— 触发生成（本批仅声明，端点未开放，见下）
  · `webhooks:manage`   —— 管理 Webhook 端点与投递记录

⚠ 本批**不新开生成端点**：`songs:write` 的语义是"允许调既有 /api/songs/generate"，
    而该端点已有自己的配额/限流/机审链路（songs.py）。在这里再包一层 = 同一件事
    两处实现，必然漂移。scope 先声明、先能签发，端点接线留给后续批次。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.developer import tokens as tok_svc
from app.developer.webhook_security import check_webhook_url
from app.models import DeveloperToken, User, WebhookDelivery, WebhookEndpoint
from app.ratelimit import rate_limit_developer
from app.routers.auth import get_current_user
from app.routers.songs import _to_out
from app.models import Song
from app.schemas import SongOut

router = APIRouter(prefix="/api/developer", tags=["developer"])


# ── 契约（Pydantic）─────────────────────────────────────────────────────────
class CreateTokenReq(BaseModel):
    name: str = Field(default="", max_length=64)
    scopes: list[str]
    # 天数而非绝对时间：客户端不该去算"现在是几点、UTC 偏移多少"。
    # None = 永不过期（吊销仍随时可用）。
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)


class TokenOut(BaseModel):
    """⚠ 只有 `CreateTokenResp` 含明文。本模型**永远不含** token / token_hash。"""

    id: int
    name: str
    token_hint: str
    scopes: list[str]
    revoked: bool
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    created_at: str


class CreateTokenResp(TokenOut):
    # 明文只此一次。之后任何端点都拿不到它 —— 丢了就吊销重建。
    token: str


class CreateWebhookReq(BaseModel):
    url: str = Field(max_length=512)
    # 留空 = 订阅全部事件（新建端点的人通常还没想好要哪些）。
    events: list[str] = []


class WebhookOut(BaseModel):
    id: int
    url: str
    events: list[str]
    active: bool
    created_at: str
    # ⚠ secret 刻意不下发（除了创建响应的那一次）：列表页反复回显签名密钥
    # 等于把它扩散到每一张截图/每个日志里。要重拿就重建端点。
    secret: Optional[str] = None


class DeliveryOut(BaseModel):
    id: int
    endpoint_id: int
    event: str
    attempts: int
    status_code: Optional[int] = None
    error: str = ""
    delivered: bool
    created_at: str


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _token_out(t: DeveloperToken, include_secret_unused=None) -> dict:
    return dict(
        id=t.id,
        name=t.name or "",
        token_hint=t.token_hint or "",
        scopes=tok_svc.parse_scopes(t.scopes),
        revoked=bool(t.revoked),
        expires_at=_iso(t.expires_at),
        last_used_at=_iso(t.last_used_at),
        created_at=_iso(t.created_at) or "",
    )


def _webhook_out(w: WebhookEndpoint, secret: Optional[str] = None) -> dict:
    return dict(
        id=w.id,
        url=w.url,
        events=[e.strip() for e in (w.events or "").split(",") if e.strip()],
        active=bool(w.active),
        created_at=_iso(w.created_at) or "",
        secret=secret,
    )


def _own_token(token_id: int, user: User, db: Session) -> DeveloperToken:
    """取本人 token；不存在或非本人 → 404。

    ⚠ 非本人也返回 **404 而非 403**：403 等于确认"这个 id 存在但不是你的"，
    攻击者可据此枚举全站 token id（资源存在性泄露，CWE-200）。
    """
    t = db.get(DeveloperToken, token_id)
    if t is None or t.user_id != user.id:
        raise HTTPException(404, "开发者 token 不存在")
    return t


def _own_webhook(endpoint_id: int, user: User, db: Session) -> WebhookEndpoint:
    w = db.get(WebhookEndpoint, endpoint_id)
    if w is None or w.user_id != user.id:
        raise HTTPException(404, "Webhook 端点不存在")
    return w


# ── 凭证管理（登录态：get_current_user；管理自己的凭证不需要开发者 token）──
@router.post("/tokens", response_model=CreateTokenResp)
def create_token(
    req: CreateTokenReq,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建开发者 token。响应里的明文 `token` **仅此一次**。

    认证用登录 JWT 而非开发者 token：初始化问题（要建 token 得先有 token）
    在这里就断掉了。
    """
    rate_limit_developer(str(user.id))
    scopes = tok_svc.normalize_scopes(req.scopes)
    raw, digest, hint = tok_svc.generate_token()
    expires_at = None
    if req.expires_in_days:
        from datetime import timedelta

        expires_at = datetime.now(timezone.utc) + timedelta(days=req.expires_in_days)
    t = DeveloperToken(
        user_id=user.id,
        name=(req.name or "").strip()[:64],
        token_hash=digest,
        token_hint=hint,
        scopes=",".join(scopes),
        expires_at=expires_at,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return CreateTokenResp(**_token_out(t), token=raw)


@router.get("/tokens", response_model=list[TokenOut])
def list_tokens(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(DeveloperToken)
        .filter(DeveloperToken.user_id == user.id)
        .order_by(DeveloperToken.created_at.desc(), DeveloperToken.id.desc())
        .all()
    )
    return [TokenOut(**_token_out(t)) for t in rows]


@router.delete("/tokens/{token_id}")
def revoke_token(
    token_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """吊销（软删，保留行以便审计"什么时候被吊销的"）。"""
    t = _own_token(token_id, user, db)
    t.revoked = True
    db.commit()
    return {"status": "revoked", "id": t.id}


# ── Webhook 管理 ────────────────────────────────────────────────────────────
@router.post("/webhooks", response_model=WebhookOut)
def create_webhook(
    req: CreateWebhookReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """注册 Webhook 端点。URL 必须过 SSRF 校验，否则 400。"""
    rate_limit_developer(str(user.id))
    try:
        url = check_webhook_url(req.url)
    except ValueError as e:
        # 400 而非 422：这是**业务规则**拒绝（内网/非法 scheme），不是请求体格式错误。
        # SDK 应把 detail 直接展示给用户（"不允许指向内网地址"是可行动的提示）。
        raise HTTPException(400, str(e))
    existing = (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.user_id == user.id, WebhookEndpoint.url == url)
        .first()
    )
    if existing:
        # 幂等：重复注册同一 URL 不该造出两条（同一事件会投两次）。
        # 但**轮换 secret 必须显式要求**，所以这里不自动重生。
        raise HTTPException(409, "该 URL 已注册")
    import secrets as _secrets

    w = WebhookEndpoint(
        user_id=user.id,
        url=url,
        secret=_secrets.token_urlsafe(32),
        events=",".join(e.strip() for e in req.events if e.strip()),
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    # secret 只在这一个响应里出现（之后要重拿就重建端点）。
    return WebhookOut(**_webhook_out(w, secret=w.secret))


@router.get("/webhooks", response_model=list[WebhookOut])
def list_webhooks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.user_id == user.id)
        .order_by(WebhookEndpoint.created_at.desc(), WebhookEndpoint.id.desc())
        .all()
    )
    return [WebhookOut(**_webhook_out(w)) for w in rows]


@router.delete("/webhooks/{endpoint_id}")
def delete_webhook(
    endpoint_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """停用端点（`active=False`）。

    刻意**不删行**：webhook_deliveries 的 endpoint_id 是外键，删行会让投递历史
    失去归属；而"这条 webhook 什么时候被停用的"本身就是排查时最想知道的事。
    """
    w = _own_webhook(endpoint_id, user, db)
    w.active = False
    db.commit()
    return {"status": "disabled", "id": w.id}


@router.get("/webhooks/{endpoint_id}/deliveries", response_model=list[DeliveryOut])
def list_deliveries(
    endpoint_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """投递记录（倒序）。这是 webhook 失败唯一的可观测入口。"""
    _own_webhook(endpoint_id, user, db)
    rows = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.endpoint_id == endpoint_id)
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
        .limit(limit)
        .all()
    )
    return [
        DeliveryOut(
            id=r.id,
            endpoint_id=r.endpoint_id,
            event=r.event,
            attempts=r.attempts or 0,
            status_code=r.status_code,
            error=r.error or "",
            delivered=r.delivered_at is not None,
            created_at=_iso(r.created_at) or "",
        )
        for r in rows
    ]


# ── 开放的只读数据端点（演示 scope 生效，不新造业务逻辑）────────────────────
@router.get("/songs", response_model=list[SongOut])
def developer_list_songs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    token: DeveloperToken = Depends(tok_svc.require_scope(tok_svc.SCOPE_SONGS_READ)),
):
    """用开发者 token 读自己的作品。

    ⚠ 复用 `songs._to_out` 与 `get_current_user` 的门控语义（未订阅者只拿试听片段），
    不另写一套序列化 —— 序列化分叉 = 权益门控出现第二条出口。
    """
    rows = (
        db.query(Song)
        .filter(Song.user_id == token.user_id)
        .order_by(Song.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    owner = db.get(User, token.user_id)
    return [_to_out(s, owner) for s in rows]
