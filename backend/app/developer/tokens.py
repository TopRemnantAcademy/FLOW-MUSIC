"""开发者凭证：签发 / 校验 / scope 判定。

与 `routers/auth.py` 的关系：这里是**并行的第二条认证路径**，不是对它的改造。
两者共用同一套「明文只出现一次、库里只存摘要」的原则（auth.py:_hash_token），
但校验入口完全不同 —— 开发者在 `Authorization: Bearer <dev_token>` 里带的是
**不透明随机串**，不是 JWT。刻意不做成 JWT：JWT 是自包含的，天然无法即时吊销，
而「泄露可即时吊销」正是本凭证的核心诉求（Issue #90 验收项）。

token 明文形态：`flow_` 前缀 + `secrets.token_urlsafe(32)`。
前缀不是装饰 —— 它让 DLP / 日志脱敏 / GitHub secret scanning 能一眼认出这串东西
是凭证（对比：裸 base64 会被当成任意数据放过）。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DeveloperToken, User

# 明文前缀（可辨识性 > 短一点）。校验时按整个 token 的 sha256 比对，前缀不参与语义。
TOKEN_PREFIX = "flow_"

# scope 白名单。刻意用**字符串常量集合**而不是枚举类型：
# 开放平台的 scope 会随能力增长，枚举每加一项都要动迁移/DB 类型；
# 这里是应用层校验，未知 scope 一律拒绝（fail-closed），加新能力只需加常量。
SCOPE_SONGS_READ = "songs:read"
SCOPE_SONGS_WRITE = "songs:write"
SCOPE_WEBHOOKS_MANAGE = "webhooks:manage"
VALID_SCOPES: frozenset[str] = frozenset(
    {SCOPE_SONGS_READ, SCOPE_SONGS_WRITE, SCOPE_WEBHOOKS_MANAGE}
)

# 单枚 token 的 scope 数量上限：防止一串超长 scope 撑爆 String/Text 与日志。
MAX_SCOPES = 8


def hash_token(raw: str) -> str:
    """明文 → sha256 十六进制摘要（64 字符）。与 auth.py:_hash_token 同算法。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_token() -> tuple[str, str, str]:
    """返回 `(明文, 摘要, hint)`。

    明文只在此刻存在一次（调用方负责放进创建响应，**绝不落库、绝不写日志**）。
    hint = 摘要前 8 位 —— 列表页用来辨认"这是在 CI 里那枚"，无法逆推出明文。
    """
    raw = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    digest = hash_token(raw)
    return raw, digest, digest[:8]


def parse_scopes(text: Optional[str]) -> list[str]:
    """`"a, b ,,"` → `["a", "b"]`（去空、去重、保序）。"""
    out: list[str] = []
    for item in (text or "").split(","):
        s = item.strip()
        if s and s not in out:
            out.append(s)
    return out


def normalize_scopes(requested: Iterable[str] | None) -> list[str]:
    """校验并规范化请求的 scope 列表；含未知项即 400（fail-closed）。

    为什么是拒绝而不是"过滤掉未知项"：静默过滤会让调用方以为拿到的是它要的权限，
    直到某个 API 返回 403 才发现其中一半没生效。宁可创建时就报错。
    """
    items = parse_scopes(",".join(requested or []))
    unknown = [s for s in items if s not in VALID_SCOPES]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"未知 scope: {', '.join(unknown)}；可用: {', '.join(sorted(VALID_SCOPES))}",
        )
    if not items:
        raise HTTPException(status_code=400, detail="至少需要一个 scope")
    if len(items) > MAX_SCOPES:
        raise HTTPException(status_code=400, detail=f"scope 数量超过上限 {MAX_SCOPES}")
    return items


def has_scope(token: DeveloperToken, scope: str) -> bool:
    return scope in parse_scopes(token.scopes)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite 会把 tz-aware 落成 naive 再吐回来 —— 比较前必须归一化。

    不做这一步的后果不是崩溃而是**静默错判**：naive 与 aware 比较在 Python 里
    直接 TypeError，被 except 吞掉后就成了"这个 token 永远有效"。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def token_is_usable(tok: DeveloperToken, now: Optional[datetime] = None) -> bool:
    """未吊销 且 未过期。"""
    if bool(tok.revoked):
        return False
    exp = _as_utc(tok.expires_at)
    if exp is None:
        return True
    return exp > (now or datetime.now(timezone.utc))


def _extract_token(request: Request) -> Optional[str]:
    """只认 Authorization: Bearer —— **刻意不读 auth cookie**。

    读 cookie 会让「开发者 API」悄悄复用浏览器登录态：那样浏览器里点一下就能
    用别人的账号调开放 API，而 scope 限定形同虚设（cookie 里是**全权限** JWT）。
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def resolve_developer_token(raw: str, db: Session) -> Optional[DeveloperToken]:
    """明文 token → DeveloperToken 行（不可用/不存在返回 None，不抛异常）。

    一次查 `token_hash` 而非遍历比对：明文熵 256 位，摘要不可逆，
    按索引精确命中即可，没有时序侧信道（对比密码需要 bcrypt 慢哈希）。
    """
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    tok = (
        db.query(DeveloperToken)
        .filter(DeveloperToken.token_hash == hash_token(raw))
        .first()
    )
    if tok is None or not token_is_usable(tok):
        return None
    return tok


def get_developer(request: Request, db: Session = Depends(get_db)) -> DeveloperToken:
    """FastAPI 依赖：认证开发者凭证本身（不要求任何 scope）。

    401 而非 403：凭证无效/已吊销 = 没通过认证，客户端该换凭证；
    403 留给"凭证有效但 scope 不够"（见 require_scope）—— 两者混用会让 SDK
    无法判断该重试还是该让用户去申请权限。
    """
    raw = _extract_token(request)
    if not raw:
        raise HTTPException(401, "缺少开发者 token", headers={"WWW-Authenticate": "Bearer"})
    tok = resolve_developer_token(raw, db)
    if tok is None:
        raise HTTPException(401, "开发者 token 无效、已吊销或已过期")
    # 封禁账号的凭证必须同步失效：不查这一下，运营封了人但 token 还能调 API。
    user = db.get(User, tok.user_id)
    if user is None or bool(getattr(user, "is_banned", False)):
        raise HTTPException(401, "开发者 token 所属账号不可用")
    return tok


def require_scope(scope: str):
    """依赖工厂：要求凭证持有指定 scope，否则 403。

    403 且 detail 里点名缺哪个 scope —— SDK 与文档能据此告诉用户"去勾选这个权限"。
    """

    def _dep(tok: DeveloperToken = Depends(get_developer)) -> DeveloperToken:
        if not has_scope(tok, scope):
            raise HTTPException(403, f"当前 token 缺少 scope: {scope}")
        return tok

    return _dep
