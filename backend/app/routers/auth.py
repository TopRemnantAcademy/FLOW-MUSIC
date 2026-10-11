"""鉴权路由：注册 / 登录 / 当前用户依赖 / GAP-002 邮箱验证·密码重置·MFA。"""

import hashlib
import secrets
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
from app.ratelimit import (
    rate_limit_forgot_password,
    rate_limit_login,
    rate_limit_register,
    rate_limit_resend_verification,
    rate_limit_reset_password,
    rate_limit_sync,
    rate_limit_verify,
)
from app.entitlements import resolve_limits, VALID_TIERS
from app.schemas import (
    FlowExchangeReq,
    FlowExchangeResp,
    FlowLinkReq,
    ForgotPasswordReq,
    LoginReq,
    MeResp,
    MfaCodeReq,
    MfaDisableReq,
    MfaSetupResp,
    RegisterReq,
    ResetPasswordReq,
    StatusResp,
    TokenResp,
    VerifyEmailReq,
)
from app.security import totp
from app.services import email as email_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# FLOW 同步 scope 常量：sync 专用最小权限令牌（🔴A 闭合）。
# 单一来源：flow.py 的三个 sync 端点与令牌签发（flow-token / flow-exchange）共用，
# 避免字符串漂移导致的静默 401。
SYNC_FLOW_SCOPE = "sync:flow"


# ── GAP-002 헬퍼 ─────────────────────────────────────────────────────────────
# token 은 **sha256 요약만** DB 에 저장한다. 평문은 이메일 링크에만 존재하므로
# DB 백업/로그가 유출돼도 재사용 가능한 token 을 얻을 수 없다.
# (평문 저장 = 단 한 번의 DB 유출이 전 계정 탈취로 직결된다.)


def _hash_token(raw: str) -> str:
    """평문 token → sha256 16진수 요약(64자)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _issue_token() -> tuple[str, str]:
    """(평문, 요약) 쌍 발급. secrets.token_urlsafe(32) = 256비트 엔트로피."""
    raw = secrets.token_urlsafe(32)
    return raw, _hash_token(raw)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite 는 tz-aware 값을 naive 로 돌려줄 수 있다 — 비교 전에 UTC 로 정규화.

    이 정규화를 빼먹으면 naive vs aware 비교에서 TypeError 가 나거나(운 좋은 경우)
    조용히 잘못된 만료 판정이 난다.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _locked_now(user: User) -> bool:
    """계정이 현재 잠금 상태인지. 잠금이 만료됐으면 즉시 해제(자동 복구)."""
    until = _as_utc(user.locked_until)
    if until is None:
        return False
    if until <= _now():
        # 만료된 잠금은 청소 — 여기서 지우지 않으면 해제 이후에도 잔존 값이 남는다.
        user.locked_until = None
        user.failed_logins = 0
        return False
    return True


def _register_failed_login(db: Session, user: User) -> None:
    """실패 누적 → 임계 도달 시 계정 잠금. 호출부가 commit 한다."""
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= settings.auth_max_failed_logins:
        user.locked_until = _now() + timedelta(minutes=settings.auth_lockout_minutes)
    db.commit()


def _clear_login_failures(db: Session, user: User) -> None:
    """로그인 성공(또는 비밀번호 재설정) 시 실패 카운터·잠금 초기화."""
    user.failed_logins = 0
    user.locked_until = None
    db.commit()


def _account_locked_detail() -> str:
    return "계정이 일시적으로 잠금되었습니다. 잠시 후 다시 시도하세요"


def _issue_verification(user: User, db: Session) -> Optional[str]:
    """인증 token 발급 + 저장 + 발송. 발송까지 성공하면 평문을 반환, 아니면 None.

    반환값이 평문인 이유: 개발 모드(메일 미구성)에서 응답으로 돌려줄 수 있게.
    ⚠ 평문을 DB 에 저장하지 않는다 — 요약만 저장.
    """
    raw, digest = _issue_token()
    user.verify_token = digest
    user.verify_sent_at = _now()
    db.commit()
    ok = email_service.send_verification_email(
        user.email, raw, settings.email_verify_token_expire_minutes
    )
    return raw if ok else None


def _send_verification(user: User, db: Session) -> Optional[str]:
    """인증 메일 발송(실패해도 예외를 올리지 않음 — 가입/재발송 흐름을 깨지 않게).

    단, 운영 의도(enable_email_delivery)에서 채널 미구성이면 503 으로 **크게 실패**한다 —
    "보냈다"고 거짓말하지 않기 위해. (ensure_delivery_available 참조)
    """
    email_service.ensure_delivery_available()
    try:
        return _issue_verification(user, db)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — 발송 실패가 가입을 실패시키면 안 된다
        import logging

        logging.getLogger(__name__).exception("[auth] 인증 메일 발송 실패 user_id=%s", user.id)
        return None


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
    # GAP-002: 가입 즉시 활성화하지 않는다(email_verified=False).
    # 미인증 계정은 로그인은 되지만 인증 유도 배너가 뜬다 — 서비스 진입 자체를 막지 않는
    # 이유는 "메일이 안 오면 계정이 영영 못 쓰는" 최악의 UX 를 피하기 위함이다.
    user = User(email=req.email, hashed_password=hashed, email_verified=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    # 인증 메일 발송 실패가 회원가입 자체를 실패시키지 않는다(사용자는 /resend-verification 로 재시도 가능).
    _send_verification(user, db)
    token = create_token(user.id)
    # [SEC] 2026-09-13：JWT 写入 HttpOnly cookie（不再返回给 JS 存 localStorage）。
    _set_auth_cookie(response, token, request)
    return TokenResp(access_token=token)


@router.post("/login", response_model=TokenResp)
def login(req: LoginReq, request: Request, response: Response, db: Session = Depends(get_db)):
    rate_limit_login(request.client.host if request.client else "unknown")
    user = db.query(User).filter(User.email == req.email).first()

    # [GAP-002] 계정 잠금은 **비밀번호 검증보다 먼저** 판정한다.
    # 나중에 판정하면, 공격자가 5번째 시도에서 우연히 정답을 맞추는 순간
    # 잠금 상태였음에도 로그인에 성공해 버린다(잠금이 무력화된다).
    if user and _locked_now(user):
        db.commit()  # 만료 잠금 청소 결과 반영
        raise HTTPException(status_code=401, detail=_account_locked_detail())

    if not user or not verify_password(req.password, user.hashed_password):
        if user:
            _register_failed_login(db, user)
        # 사용자 부재와 비밀번호 오류를 **구분하지 않는다**(계정 열거 방지).
        raise HTTPException(status_code=401, detail="邮箱或密码错误")

    # [GAP-002] MFA 활성 계정: TOTP 없이는 토큰을 발급하지 않는다.
    # ⚠ 쿠키도 쓰지 않는다 — "otp 없이 쿠키는 이미 심어져 있다"는 우회를 원천 차단.
    if user.mfa_enabled:
        if not req.otp:
            raise HTTPException(status_code=401, detail="两步验证码需要（otp）")
        if not user.mfa_secret or not totp.verify(
            user.mfa_secret, req.otp, valid_window=settings.mfa_totp_valid_window
        ):
            # OTP 오류도 실패로 누적한다 — MFA 가 2차 브루트포스 표면이 되지 않게.
            _register_failed_login(db, user)
            raise HTTPException(status_code=401, detail="两步验证码错误")

    _clear_login_failures(db, user)
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
    """返回当前登录用户（id + email + email_verified + mfa_enabled）。"""
    return MeResp(
        id=user.id,
        email=user.email,
        email_verified=bool(user.email_verified),
        mfa_enabled=bool(user.mfa_enabled),
    )


# ── GAP-002: 이메일 인증 / 비밀번호 재설정 / MFA ───────────────────────────────


@router.post("/verify", response_model=StatusResp)
def verify_email(
    req: VerifyEmailReq,
    request: Request,
    db: Session = Depends(get_db),
):
    """이메일 인증 token 소비 → email_verified=True.

    - token 은 요약 비교. 성공 시 token 즉시 소거(1회용 — 재사용 불가).
    - 만료(기본 24h 초과)는 400. 평문 token 은 이 시점에도 DB 에 없다.
    """
    rate_limit_verify(request.client.host if request.client else "unknown")
    digest = _hash_token(req.token)
    user = db.query(User).filter(User.verify_token == digest).first()
    if not user:
        raise HTTPException(status_code=400, detail="인증 링크가 유효하지 않거나 이미 사용되었습니다")
    sent_at = _as_utc(user.verify_sent_at)
    if sent_at and _now() - sent_at > timedelta(minutes=settings.email_verify_token_expire_minutes):
        # 만료 token 은 소거해 무한 재시도를 막는다(재발송으로 새 token 을 받아야 함).
        user.verify_token = None
        db.commit()
        raise HTTPException(status_code=400, detail="인증 링크가 만료되었습니다. 재발송해 주세요")
    user.email_verified = True
    user.verify_token = None  # 1회용
    db.commit()
    return StatusResp(status="verified")


@router.post("/resend-verification", response_model=StatusResp)
def resend_verification(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """인증 메일 재발송. 쿨다운 60초.

    - 이미 인증된 계정 → 400(불필요한 메일 폭주 방지).
    - 쿨다운 위반 → 429. (여기는 계정이 **확정**된 로그인 사용자라 429 를 써도
      계정 열거로 이어지지 않는다. /forgot-password 와 다른 이유.)
    """
    rate_limit_resend_verification(request.client.host if request.client else "unknown")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="이미 인증된 이메일입니다")
    sent_at = _as_utc(user.verify_sent_at)
    if sent_at and _now() - sent_at < timedelta(
        seconds=settings.auth_email_resend_cooldown_seconds
    ):
        raise HTTPException(status_code=429, detail="재발송은 잠시 후 다시 시도하세요")
    _send_verification(user, db)
    return StatusResp(status="sent")


@router.post("/forgot-password", response_model=StatusResp)
def forgot_password(
    req: ForgotPasswordReq,
    request: Request,
    db: Session = Depends(get_db),
):
    """비밀번호 재설정 메일 요청.

    ⚠ **항상 200** 을 돌려준다 — 계정 존재 여부를 노출하지 않기 위해(계정 열거 방지).
      · 미가입 이메일도 동일 응답·동일 상태코드.
      · 쿨다운 위반도 429 가 아니라 200 이다. 429 를 주면 "429 vs 200" 이 곧
        "가입됨 vs 미가입" 의 오라클이 된다 — 방어를 위해 안내성을 포기한다.
    ⚠ 메일 채널 미구성으로 503 을 던지지도 않는다(같은 이유로 존재성 오라클이 됨).
    """
    rate_limit_forgot_password(request.client.host if request.client else "unknown")
    user = db.query(User).filter(User.email == req.email).first()
    if user:
        sent_at = _as_utc(user.reset_sent_at)
        in_cooldown = sent_at and _now() - sent_at < timedelta(
            seconds=settings.auth_email_resend_cooldown_seconds
        )
        if not in_cooldown:
            raw, digest = _issue_token()
            user.reset_token = digest
            user.reset_sent_at = _now()
            db.commit()
            try:
                email_service.send_reset_email(
                    user.email, raw, settings.password_reset_token_expire_minutes
                )
            except Exception:  # noqa: BLE001 — 실패해도 존재성을 노출하지 않는다
                import logging

                logging.getLogger(__name__).exception(
                    "[auth] 재설정 메일 발송 실패 user_id=%s", user.id
                )
    return StatusResp(status="sent")


@router.post("/reset-password", response_model=StatusResp)
def reset_password(
    req: ResetPasswordReq,
    request: Request,
    db: Session = Depends(get_db),
):
    """재설정 token 소비 → 비밀번호 변경 + 잠금 해제.

    - token 은 요약 비교, 성공 시 소거(1회용). 만료(기본 30분) → 400.
    - 성공 시 failed_logins/locked_until 을 **0/None** 으로 초기화한다 —
      재설정은 본인 확인의 강한 증거이며, 잠긴 사용자가 스스로 복구할 유일한 길이다.
    """
    rate_limit_reset_password(request.client.host if request.client else "unknown")
    if len(req.new_password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(status_code=400, detail=f"密码至少 {PASSWORD_MIN_LENGTH} 位")
    digest = _hash_token(req.token)
    user = db.query(User).filter(User.reset_token == digest).first()
    if not user:
        raise HTTPException(status_code=400, detail="재설정 링크가 유효하지 않거나 이미 사용되었습니다")
    sent_at = _as_utc(user.reset_sent_at)
    if sent_at and _now() - sent_at > timedelta(
        minutes=settings.password_reset_token_expire_minutes
    ):
        user.reset_token = None
        db.commit()
        raise HTTPException(status_code=400, detail="재설정 링크가 만료되었습니다. 다시 요청해 주세요")
    user.hashed_password = hash_password(req.new_password)
    user.reset_token = None  # 1회용
    user.failed_logins = 0  # 재설정 = 본인 확인 → 잠금 해제
    user.locked_until = None
    db.commit()
    return StatusResp(status="password_reset")


@router.post("/mfa/setup", response_model=MfaSetupResp)
def mfa_setup(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """TOTP 바인딩 시작 — secret 발급·저장하되 **mfa_enabled 는 여전히 False**.

    setup(발급) → enable(코드 확인) 2단계로 분리한 이유:
    secret 발급만으로 활성화하면, 사용자가 QR 스캔에 실패한 순간 자신이 잠긴다
    (로그인에 OTP 를 요구하는데 앱에는 등록돼 있지 않은 상태).
    ⚠ secret 은 이 응답에서만 노출 — 이후 어떤 조회 경로도 없다(분실 시 재발급).
    """
    secret = totp.generate_secret()
    user.mfa_secret = secret
    user.mfa_enabled = False  # 재설정 시에도 비활성 — enable 로만 켜진다
    db.commit()
    return MfaSetupResp(
        secret=secret,
        otpauth_uri=totp.provisioning_uri(secret, user.email),
        enabled=False,
    )


@router.post("/mfa/enable", response_model=StatusResp)
def mfa_enable(
    req: MfaCodeReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """TOTP 코드 검증 통과 시에만 mfa_enabled=True (오등록 자기잠금 방지)."""
    if not user.mfa_secret:
        raise HTTPException(status_code=400, detail="먼저 MFA 설정을 시작하세요")
    if not totp.verify(
        user.mfa_secret, req.code, valid_window=settings.mfa_totp_valid_window
    ):
        raise HTTPException(status_code=400, detail="两步验证码错误")
    user.mfa_enabled = True
    db.commit()
    return StatusResp(status="mfa_enabled")


@router.post("/mfa/disable", response_model=StatusResp)
def mfa_disable(
    req: MfaDisableReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """비밀번호 재확인 후 MFA 해제 + secret 폐기.

    세션 탈취만으로 2차 인증을 **끌 수 있으면** MFA 는 무의미하다 → 비밀번호를 다시 요구한다.
    """
    if not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다")
    user.mfa_enabled = False
    user.mfa_secret = None
    db.commit()
    return StatusResp(status="mfa_disabled")


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
