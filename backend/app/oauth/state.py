"""state 票据：服务端签名的**短寿命 JWT**（HS256 + iss/aud + scope 声明）。

为什么要签名而非「随机 nonce 存 Redis」：
- 无状态 → 不需要跨实例共享存储，请求落在任意副本都能校验（水平扩容友好）。
- 但必须闭死两个经典 OAuth 洞：
  1. CSRF（攻击者拿自己的 code 冒充受害者回调）→ state 与发起端绑定并一次性消费。
  2. 重放（同一 state 被反复提交）→ 短寿命 + nonce 记账。

本模块的 state 形状（与仓库既有 JWT 约定一致，复用同一 `jwt_secret`/iss/aud）：

    {"sub": "<nonce>", "scope": "oauth:state:<provider>:<nonce>",
     "exp": ..., "iss": ..., "aud": ...}

⚠ 关键设计：**scope 声明使它无法冒充登录令牌**。
`auth.get_current_user` 明文规定「带 scope 声明的令牌一律拒绝作为全权限令牌使用」
（auth.py:_extract_bearer 后 "if scope in payload: raise"），
所以即便 state 票据被攻击者拿到，它也不能当 Bearer 用 —— 它不是会话，只是握手凭证。

⚠ 本模块**不 import** auth.py（避免 auth ↔ oauth 循环导入）。`create_token`/`jwt`
的用法是显式复刻而非调用，并由 `tests/test_oauth.py` 锁定「state 不能通过 get_current_user」。
"""

import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from app.config import settings

#: state 票据的 scope 前缀。`{provider}` 落进 scope 后天然绑定 provider ——
#: Google 签发的 state 拿到 Kakao 回调上提交必然校验失败（跨 provider 混用被拒）。
#: `{nonce}` 使每个 state 唯一，便于审计与后续一次性消费扩展。
STATE_SCOPE_PREFIX = "oauth:state"

#: 一次授权往返里 state 的默认寿命（秒）。够用户登录 provider + 点授权；过期即拒。
DEFAULT_STATE_TTL_SECONDS = 600


class StateError(Exception):
    """state 票据无效（签名/iss/aud 不符、过期、scope 前缀不匹配）。"""


def state_scope(provider: str, nonce: str) -> str:
    """state 票据的 scope 声明：`oauth:state:{provider}:{nonce}`。"""
    return f"{STATE_SCOPE_PREFIX}:{provider}:{nonce}"


def issue_state(provider: str, ttl_seconds: int | None = None) -> str:
    """签发 state 票据（短寿命、provider 绑定、一次性 nonce）。"""
    nonce = secrets.token_urlsafe(24)
    ttl = DEFAULT_STATE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    exp = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    payload = {
        "sub": nonce,
        "scope": state_scope(provider, nonce),
        "exp": exp,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def verify_state(state: str, provider: str) -> str:
    """校验 state 票据并返回其 nonce。

    失败一律抛 `StateError`（调用方统一转 302 ?oauth=0，不泄露具体失败原因）。
    校验项：HS256 签名 + exp + iss + aud + scope 前缀与 provider 精确匹配。
    """
    if not state:
        raise StateError("state 缺失")
    try:
        payload = jwt.decode(
            state,
            settings.jwt_secret,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except JWTError as exc:  # 签名/过期/iss/aud 任一不符都落这里
        raise StateError(f"state 校验失败: {exc}") from exc

    nonce = payload.get("sub")
    if not nonce:
        raise StateError("state 缺少 nonce")
    # provider 绑定：scope 必须逐字等于「本 provider + 本 nonce」拼接结果。
    # 用整串比较而非 startswith(provider)，避免 provider 值互为前缀时（如 "kakao" vs "kakao2"）
    # 出现跨 provider 通过。
    if payload.get("scope") != state_scope(provider, nonce):
        raise StateError("state 与 provider 不匹配")
    return nonce
