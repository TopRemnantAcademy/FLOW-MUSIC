"""鉴权路由：注册 / 登录 / 当前用户依赖。"""

from typing import Optional
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.billing.service import enforce_subscription_validity
from app.db import get_db
from app.models import User, UserLink
from app.ratelimit import rate_limit_login, rate_limit_register, rate_limit_sync
from app.entitlements import resolve_limits, VALID_TIERS
from app.schemas import (
    FlowExchangeReq,
    FlowExchangeResp,
    FlowLinkReq,
    LoginReq,
    MeResp,
    RegisterReq,
    TokenResp,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# FLOW 同步 scope 常量：sync 专用最小权限令牌（🔴A 闭合）。
# 单一来源：flow.py 的三个 sync 端点与令牌签发（flow-token / flow-exchange）共用，
# 避免字符串漂移导致的静默 401。
SYNC_FLOW_SCOPE = "sync:flow"


def hash_password(p: str) -> str:
    return pwd.hash(p)


def verify_password(p: str, h: str) -> bool:
    return pwd.verify(p, h)


# [D37] 공개된 기본 서명 키 — 이 값으로 서명하면 **누구나 위조 토큰을 만들 수 있다**.
# config.py:11 의 기본값 "change-me-please" 가 그대로 운영에 쓰이는 것을 막는다.
# docker-compose.yml:45 가 `JWT_SECRET: ${JWT_SECRET:-change-me-please}` 로
# **기본값을 능동적으로 주입** 하고 있어, 환경변수 미설정 시 그대로 확정된다.
# 같은 파일의 `require_admin`(admin_token 미설정 → 503) 과 동일한 fail-closed 패턴을 따른다.
_INSECURE_JWT_SECRETS = frozenset(
    {"", "change-me-please", "changeme", "change-me", "secret", "test"}
)
_JWT_MISCONFIG_DETAIL = (
    "JWT 서명 키(jwt_secret) 가 설정되지 않았거나 공개된 기본값입니다. "
    "JWT_SECRET 환경변수에 충분한 엔트로피의 값을 설정하세요."
)


def _assert_jwt_secret_configured() -> None:
    """서명 키가 공개 기본값이면 503(fail-closed).

    ⚠ **서명(sign) 과 검증(verify) 양쪽에서 모두 호출** 해야 한다.
    서명만 막으면 공격자가 `change-me-please` 로 직접 위조한 토큰이
    그대로 검증을 통과한다 — 즉 편측 가드는 무의미하다.
    """
    if settings.jwt_secret.strip().lower() in _INSECURE_JWT_SECRETS:
        raise HTTPException(status_code=503, detail=_JWT_MISCONFIG_DETAIL)


def create_token(
    user_id: int,
    scope: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    """签发 JWT。默认全权限(登录/注册返回的普通令牌)。

    scope 给定时写入 scope 声明(最小权限令牌, 如 sync:flow)。
    expires_minutes 给定时覆盖默认寿命(用于短期交换令牌)。
    """
    exp_min = (
        expires_minutes if expires_minutes is not None else settings.access_token_expire_minutes
    )
    exp = datetime.now(timezone.utc) + timedelta(minutes=exp_min)
    payload: dict = {"sub": str(user_id), "exp": exp}
    if scope:
        payload["scope"] = scope
    # [SEC] 2026-09-13：aud/iss 声明加固（MED，防令牌跨服务误用/伪造）。
    payload["iss"] = settings.jwt_issuer
    payload["aud"] = settings.jwt_audience
    # [D37] 서명 전 fail-closed — 공개 기본값으로는 토큰을 발급하지 않는다.
    _assert_jwt_secret_configured()
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# ── [SEC] HttpOnly cookie 认证（2026-09-13 审计：localStorage 存 JWT/管理员密钥 = XSS 可直接窃取，
#    等价于全账号接管 / 后台接管。改为 HttpOnly+Secure+SameSite cookie，JS 不可读）──
def _set_auth_cookie(response: Response, token: str, request: Request) -> None:
    """登录/注册成功后把 JWT 写入 HttpOnly cookie。

    - httponly: JS 不可读 → 阻断 XSS 令牌窃取。
    - secure: 仅 HTTPS 下发（明文 HTTP 时不打 secure 标记，否则浏览器拒写，本地 dev 不可用）。
    - samesite: 默认 lax（同源 frontend/API 如 localhost:3000↔:8000 视为同站）；
      若生产 frontend 与 API 跨站部署，需将 cookie_samesite 设为 "none" 且必须 secure。
    - max_age 与 JWT 寿命一致；登出/过期由后端 delete_cookie 清除。
    """
    max_age = settings.access_token_expire_minutes * 60
    secure = settings.cookie_secure and request.url.scheme == "https"
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=secure,
        samesite=settings.cookie_samesite,
        max_age=max_age,
        path="/",
    )


def _extract_bearer(request: Request) -> str | None:
    """优先读 Authorization: Bearer 头（API 客户端 / sync scope 令牌），
    缺省时回退到 HttpOnly cookie（浏览器同源请求）。"""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:] or None
    return request.cookies.get(settings.auth_cookie_name)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    cred_exc = HTTPException(401, "无效或过期的凭证", headers={"WWW-Authenticate": "Bearer"})
    # [D37] 검증 전 fail-closed — 공개 기본값으로 위조된 토큰을 거부한다.
    _assert_jwt_secret_configured()
    token = _extract_bearer(request)
    if not token:
        raise cred_exc
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
        uid = int(payload.get("sub"))
        # 最小权限: scope 한정 토큰(sync:flow 등)은 전권한 엔드포인트 접근 불가.
        # scope 클레임이 있으면 get_current_user_scoped 전용으로 격리.
        if "scope" in payload:
            raise cred_exc
    except (JWTError, ValueError, TypeError):
        raise cred_exc
    user = db.get(User, uid)
    if not user:
        raise cred_exc
    # [SEC] 2026-09-13: 만료/취소된 구독의 권한을 **접근 시점에** 회수(lazy revocation).
    # 스케줄러가 없으므로 여기서 회수하지 않으면 한 번 결제한 계정이 영구 유료로 남는다
    # (entitlements.is_subscribed 은 user.tier_key 만 본다).
    enforce_subscription_validity(db, user)
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """읽기 전용 엔드포인트용 선택 인증.

    토큰이 없거나 유효하지 않으면 None 을 반환(401 아님) → 익명 접근 허용.
    ※ OAuth2PasswordBearer 를 Depends 로 쓰면 토큰 부재 시 자동 401(auto_error)이
      발생해 익명 접근이 막히므로, 여기선 Authorization 헤더를 직접 파싱해
      부재를 None 으로 처리한다(플로우 APP 광장 익명 유저가 이 경로를 씀).
    scope 한정 토큰(sync:flow 등)은 일반 사용자로 승격하지 않음(None).
    오직 읽기 전용(상세/가사) 엔드포인트에만 사용 — 쓰기/다운로드는 get_current_user.
    """
    token = _extract_bearer(request)
    if not token:
        return None
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
        if "scope" in payload:
            return None  # scope 한정 토큰은 일반 사용자로 취급하지 않음
        uid = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        return None
    user = db.get(User, uid)
    return user or None


def get_current_user_scoped(required_scope: str):
    """scope 限定 JWT 校验依赖工厂。

    sync:flow 等最小权限令牌만 특정 엔드포인트 접근 허용.
    scope 클레임 누락/불일치 시 401(fail-closed) —— 普通全权限令牌도 접근 불가.
    🔴A 闭合: FLOW APP 不再复用"同一 JWT", 而是持有音乐发放的 scope 限定交换令牌.
    """

    def _dep(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
        cred_exc = HTTPException(401, "无效或过期的凭证", headers={"WWW-Authenticate": "Bearer"})
        # [D37] scope 한정 토큰 검증 경로도 동일하게 fail-closed.
        _assert_jwt_secret_configured()
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=["HS256"],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
            )
            uid = int(payload.get("sub"))
            if payload.get("scope") != required_scope:
                raise cred_exc
        except (JWTError, ValueError, TypeError):
            raise cred_exc
        user = db.get(User, uid)
        if not user:
            raise cred_exc
        return user

    return _dep


# 密码最小长度（防弱口令；业务规则，不依赖外部配置）
PASSWORD_MIN_LENGTH = 8


@router.post("/register", response_model=TokenResp)
def register(req: RegisterReq, request: Request, response: Response, db: Session = Depends(get_db)):
    # [SEC] 2026-09-13: 등록은 익명 엔드포인트 — IP 기반 제한이 없으면 스크립트가
    # 계정을 대량 생성해 계정별 무료 일일 쿼터를 N 배로 증폭할 수 있다.
    rate_limit_register(request.client.host if request.client else "unknown")
    if len(req.password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=400, detail=f"密码至少 {PASSWORD_MIN_LENGTH} 位")
    # [perf 2026-09-10] 先计算 bcrypt hash（~300ms×GIL）再触 DB：
    # 旧版先 `db.query(...).first()` 检查存在会立即 checkout 一个连接，而 hash 在下一步才计算，
    # 期间连接被持有 300ms+ 不释放 → 100 并发注册时池被 30+ 个注册同时占满（仅 50 池 → 频繁超时）。
    # 先 hash 再 query：把纯 CPU 工作挪到连接外，连接持有时间压到毫秒级。
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=409, detail="邮箱已注册")
    hashed = hash_password(req.password)
    user = User(email=req.email, hashed_password=hashed)
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_token(user.id)
    # [SEC] 2026-09-13：JWT 写入 HttpOnly cookie（不再返回给 JS 存 localStorage）。
    _set_auth_cookie(response, token, request)
    return TokenResp(access_token=token)


@router.post("/login", response_model=TokenResp)
def login(req: LoginReq, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit_login(request.client.host if request.client else "unknown")
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token = create_token(user.id)
    # [SEC] 2026-09-13：JWT 写入 HttpOnly cookie。
    _set_auth_cookie(response, token, request)
    return TokenResp(access_token=token)


# ── Google OAuth ──────────────────────────────────────────────────────────────


class GoogleAuthReq(BaseModel):
    """Google ID 토큰 (클라이언트가 Google Identity Services 로 받아 전송)."""

    id_token: str


@router.post("/google", response_model=TokenResp)
def auth_google(
    req: GoogleAuthReq, request: Request, response: Response, db: Session = Depends(get_db)
):
    """Google ID 토큰 검증 → 회원가입/로그인.

    클라이언트가 Google Identity Services 로 받은 ID 토큰을 전송하면
    백엔드에서 Google tokeninfo API 로 검증 후 User 생성/조회, HttpOnly cookie 세팅.
    """
    if not settings.google_client_id:
        raise HTTPException(503, "Google 登录未启用（GOOGLE_CLIENT_ID 未配置）")
    # CCM auth 라우트는 전부 동기 def 로 작성(sync SQLAlchemy 세션)。
    # Google tokeninfo 호출도 동기 httpx.Client 로 처리(await 사용 금지)。
    import httpx as _httpx

    try:
        with _httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": req.id_token},
            )
        if resp.status_code != 200:
            raise HTTPException(401, "Google 令牌验证失败")
        token_data = resp.json()
    except _httpx.HTTPError as exc:
        raise HTTPException(502, f"Google 验证服务连接失败: {exc}")

    _allowed_ids = {c.strip() for c in settings.google_client_id.split(",") if c.strip()}
    if token_data.get("aud") not in _allowed_ids:
        raise HTTPException(401, "令牌目标(aud)不匹配")
    _iss = token_data.get("iss")
    if _iss not in ("https://accounts.google.com", "accounts.google.com"):
        raise HTTPException(401, "令牌颁发者(iss)无效")
    email = (token_data.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(400, "无法从 Google 账户获取邮箱")
    ev = token_data.get("email_verified")
    email_verified = ev is True or str(ev).lower() == "true"
    if not email_verified:
        raise HTTPException(403, "邮箱未通过 Google 验证")
    name = token_data.get("name") or email.split("@")[0]

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, hashed_password="")
        db.add(user)
        db.commit()
        db.refresh(user)
    token = create_token(user.id)
    # [SEC] 2026-09-13：JWT 写入 HttpOnly cookie。
    _set_auth_cookie(response, token, request)
    return TokenResp(access_token=token)


@router.get("/google/config")
def auth_google_config():
    """公开端点：前端据此决定是否显示 Google 登录按钮。"""
    return {
        "enabled": bool(settings.google_client_id),
        "client_id": settings.google_client_id or "",
    }


def _resolve_admin_role(token: str | None) -> str | None:
    """返回令牌角色：super(主管理员) / viewer(只读) / None(无效)。"""
    if not token:
        return None
    if settings.admin_token and hmac.compare_digest(token, settings.admin_token):
        return "super"
    if settings.admin_viewer_token and hmac.compare_digest(token, settings.admin_viewer_token):
        return "viewer"
    return None


def require_admin(request: Request) -> None:
    """管理后台鉴权：校验 X-Admin-Token 头 **或** flow_admin HttpOnly cookie。

    - admin_token 未配置 → 503（功能未启用），而非 500（避免暗示服务端 bug）。
    - 接受 super(admin_token) 与 viewer(admin_viewer_token) 两种令牌；
      viewer 仅可访问只读接口，变更类接口由 main.admin_rbac_audit 中间件返回 403。
    - 令牌用恒定时间比较（hmac.compare_digest），防时序侧信道。
    - [SEC] 2026-09-13：管理员密钥原存 localStorage（XSS 可窃取 = 全后台接管），
      现改为登录时写入 HttpOnly cookie（/api/auth/admin-login），此处同步支持头与 cookie。
    """
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="管理后台未启用（admin_token 未配置）")
    token = request.headers.get("X-Admin-Token") or request.cookies.get(settings.admin_cookie_name)
    if _resolve_admin_role(token) is None:
        raise HTTPException(status_code=401, detail="无效的管理密钥")


@router.get("/me", response_model=MeResp)
def me(user: User = Depends(get_current_user)) -> MeResp:
    """返回当前登录用户（id + email），供前端展示用户名。"""
    return MeResp(id=user.id, email=user.email)


class AdminLoginReq(BaseModel):
    key: str


@router.post("/logout")
def logout(response: Response):
    """前端登出：清除 flow_auth cookie（HttpOnly，JS 不可自清，必须由后端删除）。"""
    response.delete_cookie(settings.auth_cookie_name, path="/")
    return {"status": "ok"}


@router.post("/admin-login")
def admin_login(req: AdminLoginReq, request: Request, response: Response):
    """管理员登录：校验共享密钥后写入 flow_admin HttpOnly cookie。

    ⚠ 管理员密钥是**共享静态密钥**（非每管理员独立账号），故此处仅做有效性校验，
      不改写任何用户态。cookie 值即该密钥本身，后端 require_admin 同时接受头与 cookie。
    """
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="管理后台未启用（admin_token 未配置）")
    role = _resolve_admin_role(req.key)
    if role is None:
        raise HTTPException(status_code=401, detail="无效的管理密钥")
    secure = settings.cookie_secure and request.url.scheme == "https"
    response.set_cookie(
        key=settings.admin_cookie_name,
        value=req.key,
        httponly=True,
        secure=secure,
        samesite=settings.cookie_samesite,
        max_age=settings.admin_cookie_max_age_seconds,
        path="/",
    )
    return {"role": role, "status": "ok"}


@router.post("/admin-logout")
def admin_logout(response: Response):
    """管理员登出：清除 flow_admin cookie。"""
    response.delete_cookie(settings.admin_cookie_name, path="/")
    return {"status": "ok"}


class SubscribeReq(BaseModel):
    tier_key: str


@router.post("/subscribe")
def subscribe(
    req: SubscribeReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """[GAP-004 mock] 실제 PG 결제 미연동 — 결제 완료를 시뮬레이션해 티어만 전환.

    실 PG(Stripe/Kakao Pay/Naver Pay 등) 연동 시 이 엔드포인트는
    웹훅/결제완료 검증 핸들러로 교체되며, 그때는 결제 영수증 검증 후에만
    `user.tier_key` 를 설정한다. 현재는 권한 게이팅 로직을 E2E 로 검증하기 위한
    테스트/데모용이다.
    """
    # ⚠ live 모드에서는 410 으로 폐기 — 결제 우회(무료 직접 승급) 백도어 방지.
    #   실결제는 /api/billing/checkout → webhook/confirm 흐름을 따른다.
    if settings.billing_mode == "live":
        raise HTTPException(
            status_code=410,
            detail="직접 구독은 비활성화되었습니다. 결제 flow(/api/billing/checkout) 를 이용하세요.",
        )
    # 🔒 安全加固（2026-09-13 审计）：live 之外还需**显式**打开 mock 开关。
    # 否则默认部署下任意登录用户可自助升到 enterprise（绕过支付 = CWE-840）。
    if not settings.enable_mock_subscribe:
        raise HTTPException(
            status_code=403,
            detail="自助订阅未启用（测试专用）。请通过 /api/billing/checkout 完成真实支付。",
        )
    if req.tier_key not in VALID_TIERS:
        raise HTTPException(status_code=400, detail="unknown tier_key")
    user.tier_key = req.tier_key
    db.commit()
    db.refresh(user)
    return {"tier_key": user.tier_key, "limits": resolve_limits(db, user.tier_key)}


@router.post("/flow-link")
def flow_link(
    req: FlowLinkReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用户授权绑定 FLOW subscriber_id ↔ 本音乐账号(user_id)。

    幂等 upsert: 同一 subscriber_id 는 항상 동일 music_user_id 로 매핑.
    호출 전제: APP 이 음악 JWT(본 의존성 검증) 와 FLOW subscriber_id 를 동시 보유.
    실제 sync 호출은 flow-exchange 로 발급된 scope 한정 토큰을 사용.
    """
    # 🔒 安全加固（2026-09-13 审计，CWE-639 授权绕过）：
    # 原实现允许任意登录用户提交**他人**的 subscriber_id 并把绑定改写到自己账号
    # （随后 flow-exchange 会为攻击者签发该身份的 sync 令牌 = 身份劫持 + 扣费嫁祸）。
    # 现改为：已绑定到其它账号 → 409 拒绝；本账号已绑其它身份 → 409 拒绝。
    link = db.query(UserLink).filter_by(flow_subscriber_id=req.subscriber_id).first()
    if link is not None:
        if link.music_user_id != user.id:
            raise HTTPException(
                status_code=409,
                detail="该 FLOW 账号已绑定到其它音乐账号，无法重复绑定",
            )
        # 幂等：同一对映射重复调用直接返回成功
        return {"status": "linked", "subscriber_id": req.subscriber_id, "music_user_id": user.id}
    mine = db.query(UserLink).filter_by(music_user_id=user.id).first()
    if mine is not None:
        if mine.flow_subscriber_id != req.subscriber_id:
            raise HTTPException(
                status_code=409,
                detail="本音乐账号已绑定其它 FLOW 账号，请先解绑",
            )
        return {"status": "linked", "subscriber_id": req.subscriber_id, "music_user_id": user.id}
    db.add(UserLink(flow_subscriber_id=req.subscriber_id, music_user_id=user.id))
    db.commit()
    return {"status": "linked", "subscriber_id": req.subscriber_id, "music_user_id": user.id}


@router.post("/flow-token", response_model=FlowExchangeResp)
def flow_token(
    user: User = Depends(get_current_user),
    _rl: None = Depends(rate_limit_sync),
):
    """Web 自签腿：当前登录用户 → scope 限定(SYNC_FLOW_SCOPE) 短期 JWT。

    - 与 flow-exchange（FLOW 后端 server-to-server）并列的第二条签发腿：
      音乐站内 Web 前端持全权限 JWT，自签 **自己名下** 的 sync 专用令牌
      （Player「同步到 FLOW APP」按钮走此路径）。
    - 无越权面：sub 恒为调用者本人，scope+短寿命令能力严格收敛，
      纯粹是「窄化」而非「提权」。
    - 🔴A fail-closed 契约不变：sync 端点依旧拒绝全权限 JWT
      （test_flow_sync_rejects_full_token 锁定）；APK 的 flow-exchange
      交换腿（X-Flow-Internal-Token）原样保留。
    """
    token = create_token(
        user.id,
        scope=SYNC_FLOW_SCOPE,
        expires_minutes=settings.flow_exchange_token_expire_minutes,
    )
    return FlowExchangeResp(access_token=token)


@router.post("/flow-exchange", response_model=FlowExchangeResp)
def flow_exchange(
    req: FlowExchangeReq,
    x_flow_internal_token: str | None = Header(default=None, alias="X-Flow-Internal-Token"),
    db: Session = Depends(get_db),
):
    """FLOW 后端 server-to-server: subscriber_id → scope 한정(sync:flow) JWT。

    - 내부 FLOW_INTERNAL_TOKEN 으로 보호(상수시간 비교, 시퀀스 사이드채널 방지).
    - 미설정 시 503(기능 미활성).
    - 매핑(user_link) 없으면 404(바인딩 선행 필요).
    - 발급되는 JWT 는 sync 전용 —— 음악 생성/관리/결제 권한 0(최소 권한).
    """
    if not settings.flow_internal_token:
        raise HTTPException(
            status_code=503, detail="FLOW 令牌交换未启用（flow_internal_token 未配置）"
        )
    if not x_flow_internal_token or not hmac.compare_digest(
        x_flow_internal_token, settings.flow_internal_token
    ):
        raise HTTPException(status_code=401, detail="无效的内部密钥")
    link = db.query(UserLink).filter_by(flow_subscriber_id=req.subscriber_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="未找到 FLOW 账号绑定，请先绑定")
    token = create_token(
        link.music_user_id,
        scope=SYNC_FLOW_SCOPE,
        expires_minutes=settings.flow_exchange_token_expire_minutes,
    )
    return FlowExchangeResp(access_token=token)
