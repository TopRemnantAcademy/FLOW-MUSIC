"""授权码交换 + 用户信息解析 —— 本项目唯一的 OAuth 出网点。

**为什么把出网单独成模块**：`tests/test_oauth.py` 只需 monkeypatch 本模块的
`exchange_code` / `fetch_userinfo` 两个函数，即可覆盖全部 22 例而不触碰网络。
若出网散落在 service 里，测试就得打 httpx mock 或依赖外网（脆弱 + 慢）。

HTTP 客户端选型：同步 `httpx.Client`。理由 —— 本仓库的 auth 路由全是同步 def
（sync SQLAlchemy 会话），在同步路由里用 async client 需要 await，反而要改路由签名；
既有 `auth_google`（ID 令牌腿）已确立「auth 路由内用同步 httpx」的先例。
"""

from typing import Any, Mapping

import httpx

from app.config import settings
from app.oauth.providers import OAuthProvider

#: 出网时的 User-Agent（部分 provider 对空 UA 有风控）。
_UA = "flow-music-oauth/1.0"


class OAuthExchangeError(Exception):
    """授权码交换失败（provider 返回非 200 / 缺 access_token）。"""


class OAuthUserinfoError(Exception):
    """用户信息获取失败，或响应缺少必需字段（sub）。"""


def _client() -> httpx.Client:
    """构造出网客户端。

    `trust_env=False`：**显式忽略 HTTP(S)_PROXY**。OAuth 交换体里带授权码与
    client_secret，走环境代理等于把凭据交给代理方；且本仓库有 SSRF 出口策略
    （见 D34 egress 守卫），不经代理是既定姿态。
    """
    return httpx.Client(timeout=settings.oauth_http_timeout, trust_env=False, headers={"User-Agent": _UA})


def exchange_code(provider: OAuthProvider, code: str, redirect_uri: str) -> Mapping[str, Any]:
    """用授权码换 access_token，返回原始 JSON。

    Kakao/Naver 的 token 端点是 `application/x-www-form-urlencoded`（不是 JSON body），
    故用 `data=` 而非 `json=` —— 用错会得到 400，且报错信息不会提示编码问题。
    `client_secret` 为空时**不发该字段**（Kakao 关闭 secret 的官方配置下，
    发空串会被判 `invalid_client`）。
    """
    payload: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": provider.client_id,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    secret = (provider.client_secret or "").strip()
    if secret:
        payload["client_secret"] = secret
    try:
        with _client() as client:
            resp = client.post(provider.token_url, data=payload)
    except httpx.HTTPError as exc:
        raise OAuthExchangeError(f"令牌端点连接失败: {exc}") from exc
    if resp.status_code != 200:
        # 不回传响应体全文：可能含 provider 侧诊断与部分凭据。
        raise OAuthExchangeError(f"令牌端点返回 {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:  # 非 JSON（网关 HTML 错误页等）
        raise OAuthExchangeError("令牌端点返回非 JSON") from exc
    if not body.get("access_token"):
        raise OAuthExchangeError("令牌端点未返回 access_token")
    return body


def fetch_userinfo(provider: OAuthProvider, access_token: str) -> Mapping[str, Any]:
    """取用户信息，返回**已归一化**的 `{sub, email, email_verified}`。

    归一化是必要的：三家响应形状互不相同（见 providers.py 模块注释），
    若把原始 JSON 透传给 service，邮箱合并策略就得逐 provider 分支
    —— 合并策略属安全决策，不该被 provider 格式差异污染。
    """
    try:
        with _client() as client:
            resp = client.get(
                provider.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise OAuthUserinfoError(f"用户信息端点连接失败: {exc}") from exc
    if resp.status_code != 200:
        raise OAuthUserinfoError(f"用户信息端点返回 {resp.status_code}")
    try:
        raw = resp.json()
    except ValueError as exc:
        raise OAuthUserinfoError("用户信息端点返回非 JSON") from exc

    parser = _PARSERS.get(provider.name)
    if parser is None:
        raise OAuthUserinfoError(f"未知 provider: {provider.name}")
    return parser(raw)


def _parse_google(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Google OIDC userinfo：`{sub, email, email_verified}``。

    ⚠ Google 的 `email_verified` 可能是**字符串** "true"（OIDC 规范里是 bool，
    但老接口/tokeninfo 返回字符串），故显式做真假归一 —— 只认 True 与 "true"，
    其余（含缺失、null、False、"false"）一律视为未验证。
    """
    email = (raw.get("email") or "").strip().lower()
    ev = raw.get("email_verified")
    return {
        "sub": str(raw.get("sub") or "").strip(),
        "email": email,
        "email_verified": ev is True or str(ev).lower() == "true",
    }


def _parse_kakao(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Kakao `/v1/user/me`：`{id, kakao_account:{email, is_email_verified}}`。

    - sub 用顶层 `id`（数字，全局唯一）—— 不用 nickname（可改、可重复）。
    - 邮箱未开通权限时 `kakao_account` 可能整体缺失或 `email` 为空 → 交给 service
      落合成地址，而不是在这里造一个真假值。
    """
    account = raw.get("kakao_account") or {}
    email = (account.get("email") or "").strip().lower()
    return {
        "sub": str(raw.get("id") or "").strip(),
        "email": email,
        # Kakao 字段名是 is_email_verified（不是 email_verified）。
        "email_verified": account.get("is_email_verified") is True,
    }


def _parse_naver(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Naver `/v1/nid/me`：`{resultcode, message, response:{id, email}}`。

    ⚠ 恒定多包一层 `response`；`resultcode` 非 "00" 视为失败
    （Naver 200 也可能带 `"024"` 之类的业务错误码）。
    """
    if str(raw.get("resultcode") or "00") != "00":
        raise OAuthUserinfoError(f"Naver userinfo 业务错误码: {raw.get('resultcode')}")
    body = raw.get("response") or {}
    return {
        "sub": str(body.get("id") or "").strip(),
        "email": (body.get("email") or "").strip().lower(),
        # Naver 不返回 email_verified 字段（verified 由平台方在后台保证），
        # 故保守取 False —— 未明确 verified 的邮箱不得用于同邮箱合并。
        "email_verified": False,
    }


_PARSERS = {
    "google": _parse_google,
    "kakao": _parse_kakao,
    "naver": _parse_naver,
}
