"""OAuth 编排与**账号合并策略**。

合并策略是本功能唯一的「安全决策点」，规则写死在这里并被测试逐条锁定：

1. provider 明确 `email_verified=true` **且** 有邮箱
   → 允许与既有同邮箱账号合并（即登录到该账号），并在其 `oauth_provider/sub` 未绑定
     时补绑（次登录免流程）。
2. 邮箱未验证 / provider 未返回邮箱
   → **绝不**与既有账号合并。落合成地址 `{sub}@{provider}.oauth.local`，
     作为独立账号存在。合成域用 `.oauth.local`（RFC 6761 保留 TLD）——
     真实邮件域不可能解析到它，故不存在「抢占真实地址」的可能。
3. 同一 `(provider, sub)` 二次登录 → 命中唯一约束，直接登录（幂等）。

⚠ 为什么「未验证邮箱不能合并」值得单独一条：
  若允许，Kakao/Naver 上任何人都能把自己的账号邮箱填成别人的真实邮箱
  （provider 不强验证时），随后 OAuth 登录即**接管**该邮箱的既有账号（含余额/曲库）。
  这是 OAuth 集成最经典的账号接管漏洞（CWE-287）。宁可为少数用户多建一个账号，
  也不容许一次错误合并。

`{sub}@{provider}.oauth.local` 的另一重作用：`users.email` 是 `unique + not null`，
合成地址让「无邮箱用户」也能拿到合法唯一 email，无需放宽 DB 约束。
"""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.oauth import tokens
from app.oauth.providers import OAuthProvider, build_authorize_url, get_provider
from app.oauth.state import issue_state, verify_state

#: 无法取得可信邮箱时落地的合成邮箱域（RFC 6761 保留 TLD，绝不可能与真实域名冲突）。
SYNTHETIC_EMAIL_DOMAIN = "oauth.local"


def synthetic_email(provider: str, sub: str) -> str:
    """`{sub}@{provider}.oauth.local` —— 无可信邮箱时的**不抢占**占位地址。"""
    return f"{sub}@{provider}.{SYNTHETIC_EMAIL_DOMAIN}"


def redirect_uri_for(provider_name: str) -> str:
    """该 provider 的回跳地址（必须与 provider 控制台登记值逐字一致）。"""
    base = (settings.oauth_redirect_base_url or "").strip().rstrip("/")
    if not base:
        # 未配 base 时退回相对路径：provider 侧会拒（invalid redirect_uri），
        # 但至少路由/测试在无配置下仍可运行，且错误是显式可诊断的。
        return f"/api/auth/oauth/{provider_name}/callback"
    return f"{base}/api/auth/oauth/{provider_name}/callback"


def start_oauth(provider_name: str) -> str:
    """签发 state 并返回授权页 URL（调用方 302 过去）。

    provider 未知或未配置凭据 → `ValueError`（路由层转 302 ?oauth=0，不 500：
    对终端用户而言「这个按钮没配好」与「登录失败」是同一类结果，不该看到栈）。
    """
    provider = get_provider(provider_name)
    if provider is None:
        raise ValueError(f"未知的 provider: {provider_name}")
    if not provider.enabled:
        raise ValueError(f"{provider.name} 登录未启用（凭据未配置）")
    state = issue_state(provider.name)
    return build_authorize_url(provider, state, redirect_uri_for(provider.name))


def complete_oauth(
    db: Session, provider_name: str, code: str, state: str
) -> User:
    """完成授权码登录，返回 User。任一环节失败抛 `OAuthLoginError`。"""
    provider = get_provider(provider_name)
    if provider is None:
        raise OAuthLoginError("unknown_provider")
    if not provider.enabled:
        raise OAuthLoginError("provider_disabled")
    if not code:
        raise OAuthLoginError("missing_code")
    # ① state 校验（签名/寿命/iss/aud/provider 绑定）。先做这一步再换令牌 ——
    #    state 不过就没必要把授权码送出去（少一次凭据外发）。
    try:
        verify_state(state, provider.name)
    except Exception as exc:  # StateError 及任何解码异常统一出口
        raise OAuthLoginError("state_invalid") from exc
    # ② 换令牌 + 取 userinfo（唯一出网段，测试在此 monkeypatch）。
    try:
        token_body = tokens.exchange_code(provider, code, redirect_uri_for(provider.name))
        info = tokens.fetch_userinfo(provider, str(token_body["access_token"]))
    except tokens.OAuthExchangeError as exc:
        raise OAuthLoginError("exchange_failed") from exc
    except tokens.OAuthUserinfoError as exc:
        raise OAuthLoginError("userinfo_failed") from exc

    sub = (info.get("sub") or "").strip()
    if not sub:
        # 无 sub = 无法稳定标识用户。此时**不能**退化成「按邮箱找账号」——
        # 那正是绕过合并策略的后门。
        raise OAuthLoginError("missing_sub")
    return _resolve_user(db, provider, sub, info)


def _resolve_user(db: Session, provider: OAuthProvider, sub: str, info: dict) -> User:
    """按合并策略定位/创建账号。

    查找顺序即优先级（**先身份后邮箱**）：
      1. (oauth_provider, oauth_sub) 命中 → 就是本人，直接返回。
      2. email_verified=true 且有邮箱 → 允许与同邮箱账号合并。
      3. 否则落合成地址。
    """
    # ① 身份优先：绑定关系比邮箱更可信（邮箱可能被 provider 侧变更）。
    bound = (
        db.query(User)
        .filter(User.oauth_provider == provider.name, User.oauth_sub == sub)
        .first()
    )
    if bound is not None:
        return bound

    email = (info.get("email") or "").strip().lower()
    verified = bool(info.get("email_verified"))
    mergeable = bool(email) and verified

    if mergeable:
        existing = db.query(User).filter(User.email == email).first()
        if existing is not None:
            # 合并：登录到既有账号，并补绑 identity（下次走 ① 快路径）。
            # ⚠ 既有账号已绑定**其它** provider/sub → 拒绝。
            #   users.email 唯一，但一个账号可能被多个 provider 绑定；
            #   若该账号已绑 google/x，再来 kakao/y 请求合并，等于用第二个 provider
            #   的「同邮箱」断言覆盖首个绑定关系 —— 共享邮箱（企业别名/转发）场景下
            #   这是账号接管面。要求已登录用户显式走「绑定」流程（另开话题），此处不隐式改绑。
            if existing.oauth_provider and (
                existing.oauth_provider != provider.name or existing.oauth_sub != sub
            ):
                raise OAuthLoginError("identity_already_linked")
            if not existing.oauth_provider:
                existing.oauth_provider = provider.name
                existing.oauth_sub = sub
                _commit(db)
            return existing

    fallback_email = email if mergeable else synthetic_email(provider.name, sub)
    user = User(
        email=fallback_email,
        # OAuth 账号无本地密码：存空串而非 bcrypt 占位 ——
        # `verify_password(p, "")` 对 bcrypt 上下文直接返回 False（passlib 不抛异常），
        # 故「空 hash 账号无法用密码登录」成立，与既有 Google 腿（auth.py）保持一致。
        hashed_password="",
        oauth_provider=provider.name,
        oauth_sub=sub,
    )
    db.add(user)
    try:
        _commit(db)
    except IntegrityError:
        # 并发首登：同一 (provider,sub) 两个请求同时走到这里，一个插入成功、
        # 另一个撞唯一约束。回滚后按 ① 重查即得胜者，对用户表现为「登录成功」。
        db.rollback()
        raced = (
            db.query(User)
            .filter(User.oauth_provider == provider.name, User.oauth_sub == sub)
            .first()
        )
        if raced is None:
            raise OAuthLoginError("email_conflict")
        return raced
    db.refresh(user)
    return user


def _commit(db: Session) -> None:
    db.commit()


class OAuthLoginError(Exception):
    """OAuth 登录失败。`reason` 是**面向用户的粗粒度原因码**（不含内部细节）。

    原因码白名单（路由层只允许这些值进 URL）：
      - state_invalid        state 无效/过期/跨 provider
      - missing_code         回调缺 code（用户拒绝授权也走这里）
      - exchange_failed      授权码换令牌失败
      - userinfo_failed      取用户信息失败
      - missing_sub          provider 未返回可用的用户标识
      - identity_already_linked  目标账号已绑定其它 OAuth 身份（拒绝隐式改绑）
      - email_conflict       并发/唯一约束冲突且无法恢复
      - unknown_provider / provider_disabled  配置问题
    ⚠ 绝不把异常原文塞进 URL：它会出现在浏览器历史/Referer/日志里，
      也可能泄露 provider 侧诊断或凭据片段。
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def oauth_callback_redirect(ok: bool, reason: str | None = None) -> str:
    """构造回前端的落点 URL。

    成功 → `{frontend}?oauth=1`
    失败 → `{frontend}?oauth=0&reason={reason}`
    reason 只允许 OAuthLoginError 白名单值；未知值归一为 `failed`（防注入/防泄露）。
    """
    from urllib.parse import urlencode

    base = (settings.oauth_frontend_redirect_url or "/account").strip()
    params = {"oauth": "1" if ok else "0"}
    if not ok:
        params["reason"] = reason if reason in ALLOWED_REASONS else "failed"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params)}"


#: 允许出现在回跳 URL 里的 reason 值（白名单，见 OAuthLoginError 文档）。
ALLOWED_REASONS = frozenset(
    {
        "state_invalid",
        "missing_code",
        "exchange_failed",
        "userinfo_failed",
        "missing_sub",
        "identity_already_linked",
        "email_conflict",
        "unknown_provider",
        "provider_disabled",
        "failed",
    }
)
