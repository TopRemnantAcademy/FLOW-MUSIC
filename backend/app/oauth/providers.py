"""OAuth provider 注册表：端点、scope、凭据读取与 enabled 判定。

三个 provider 的差异（这正是无法用「一套通用 OAuth」糊过去的原因）：
- **Google**：标准 OIDC（`openid email profile`），userinfo 返回 `email_verified`。
- **Kakao**：非 OIDC 的 `/v1/user/me`，邮箱不在基本 scope 内（需 `account_email`，
  且要在 Kakao 后台申请「邮箱」权限）；**Client Secret 可选**（后台可关闭）。
- **Naver**：`/v1/nid/me`，响应恒定包一层 `{"resultcode","message","response":{...}}`，
  且 `email` 是否返回取决于后台「이메일」제공 동의항목 설정。

所以「取邮箱」这一步必须由各自 parser 负责，不能假设统一字段路径。
"""

from dataclasses import dataclass
from typing import Mapping

from app.config import settings


@dataclass(frozen=True)
class OAuthProvider:
    """单个 OAuth provider 的静态描述 + 运行时凭据。"""

    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    client_id: str
    client_secret: str
    #: 允许**空 Client Secret**（Kakao：secret 是可选增强，关闭时仅校验 client_id）。
    allow_empty_secret: bool = False
    #: 授权 URL 是否需要 `scope` 参数（Naver 不需要，权限在后台登记）。
    send_scope: bool = True

    @property
    def enabled(self) -> bool:
        """该 provider 是否可用于登录（凭据齐备）。

        - client_id 必填（任何 provider）。
        - client_secret：仅当 `allow_empty_secret=False` 时必填。
        - ⚠ Kakao 空 Secret 是**官方允许的合法配置**，不得视作「未配置」。
        """
        if not (self.client_id or "").strip():
            return False
        if self.allow_empty_secret:
            return True
        return bool((self.client_secret or "").strip())


def _google() -> OAuthProvider:
    return OAuthProvider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
        client_id=settings.google_client_id,
        client_secret=settings.oauth_google_client_secret,
    )


def _kakao() -> OAuthProvider:
    return OAuthProvider(
        name="kakao",
        authorize_url="https://kauth.kakao.com/oauth/authorize",
        token_url="https://kauth.kakao.com/oauth/token",
        userinfo_url="https://kapi.kakao.com/v1/user/me",
        # account_email 属「추가 동의항목」，需 Kakao 后台开通；未开通时 Kauth 会忽略它，
        # 于是 userinfo 无 email → 走 `{sub}@kakao.oauth.local` 合成地址分支（不抢占真实邮箱）。
        scope="profile_nickname account_email",
        client_id=settings.oauth_kakao_client_id,
        client_secret=settings.oauth_kakao_client_secret,
        allow_empty_secret=True,
    )


def _naver() -> OAuthProvider:
    return OAuthProvider(
        name="naver",
        authorize_url="https://nid.naver.com/oauth2.0/authorize",
        token_url="https://nid.naver.com/oauth2.0/token",
        userinfo_url="https://openapi.naver.com/v1/nid/me",
        # Naver 的权限在应用后台登记，授权 URL 的 scope 参数无效/可省略。
        scope="",
        client_id=settings.oauth_naver_client_id,
        client_secret=settings.oauth_naver_client_secret,
        send_scope=False,
    )


#: 注册表工厂。**每次访问重新读取 settings**（而非 import 时快照），
#: 否则测试里 monkeypatch settings 后 enabled 恒为旧值 —— 这类「import-time 快照」
#: 在本仓库已经有前车之鉴（见 GAP-004 的 TIER_LIMITS free 配额注释）。
_FACTORIES: Mapping[str, callable] = {
    "google": _google,
    "kakao": _kakao,
    "naver": _naver,
}

PROVIDER_NAMES = tuple(_FACTORIES)


def get_provider(name: str) -> OAuthProvider | None:
    """按名取 provider；未知名返回 None（调用方转 302 ?oauth=0&reason=...，不 500）。"""
    factory = _FACTORIES.get((name or "").strip().lower())
    return factory() if factory else None


def all_providers() -> dict[str, OAuthProvider]:
    """全部 provider（含未启用的），供 /providers 状态端点与测试使用。"""
    return {name: factory() for name, factory in _FACTORIES.items()}


def build_authorize_url(provider: OAuthProvider, state: str, redirect_uri: str) -> str:
    """拼装授权页 URL（用户 302 过去的那一条）。

    ⚠ 参数用 urlencode 而非 f-string 拼接：redirect_uri 含 `://` 与 `/`，
    裸拼会被 provider 当作额外路径分隔解析 → 回跳地址与登记值不匹配 → invalid_grant。
    """
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    if provider.send_scope and provider.scope:
        params["scope"] = provider.scope
    return f"{provider.authorize_url}?{urlencode(params)}"
