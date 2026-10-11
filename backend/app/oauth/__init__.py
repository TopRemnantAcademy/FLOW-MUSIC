"""GAP-003 OAuth 授权码登录（Google / Kakao / Naver）。

分层（单一职责，便于单测全 mock 不出网）：
- `providers.py` : provider 注册表（端点/scope/凭据/enabled 判定）+ 授权 URL 构造。
- `state.py`     : state 票据 = 服务端签名短寿命 JWT（HS256 + iss/aud + scope claim）。
- `tokens.py`    : 授权码交换 + 用户信息解析（唯一出网点，测试直接 monkeypatch 本模块）。
- `service.py`   : 编排（校验 state → 换令牌 → 解析身份 → 邮箱合并策略 → 落库/签发）。
"""

from app.oauth.providers import PROVIDER_NAMES, OAuthProvider, all_providers, get_provider
from app.oauth.state import issue_state, verify_state
from app.oauth.service import (
    OAuthLoginError,
    complete_oauth,
    oauth_callback_redirect,
    start_oauth,
)

__all__ = [
    "PROVIDER_NAMES",
    "OAuthProvider",
    "all_providers",
    "get_provider",
    "issue_state",
    "verify_state",
    "OAuthLoginError",
    "complete_oauth",
    "oauth_callback_redirect",
    "start_oauth",
]
