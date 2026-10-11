"""本地 PG 公共工具: 币种约束 / 金额换算 / 同步 HTTP。

设计原则(与 GAP-004 一致, 拒绝幻觉):
- 每家 PG 只支持本国货币 —— 币种不符立即失败, 绝不用臆造汇率替用户换算.
- 凭据缺失 → ProviderNotConfigured(路由转 503), 与"支付被拒"严格区分, 便于运维定位.
- 供应商返回非 2xx / 非 JSON → 带响应体前 500 字的 RuntimeError, 不静默当成功.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

# ProviderNotConfigured 定义在 app.billing.provider(StripeProvider 也要用), 这里再导出,
# 使 pg.* 各家实现只需从本模块取一处公共工具集.
from app.billing.provider import ProviderNotConfigured
from app.config import settings

__all__ = [
    "PROVIDER_CURRENCY",
    "HTTP_TIMEOUT",
    "APPROVE_HTTP_TIMEOUT",
    "ProviderNotConfigured",
    "allowed_currencies",
    "ZERO_DECIMAL_CURRENCIES",
    "require_currency",
    "amount_major",
    "amount_krw",
    "amount_cny_fen",
    "amount_cny_yuan_str",
    "out_trade_no",
    "post",
    "append_query",
]

# 各 PG 结算币种硬约束(来自各家官方受理范围, 非推测).
# 单币种 PG 只有一种; PayPal 例外 —— 它是**多币种** PG, 集合见 allowed_currencies().
PROVIDER_CURRENCY: dict[str, str] = {
    "kakao": "KRW",
    "naver": "KRW",
    "wechat": "CNY",
    "alipay": "CNY",
    "paypal": "USD",  # 多币种 PG 的**默认/展示**币种, 实际受理集合见 allowed_currencies()
}

# 官方零小数位币种(developer.paypal.com/reference/currency-codes:
#   "HUF/JPY/TWD — Zero-digit currency — no decimal places or fractions").
# 放在公共层: 金额核对(service)与 PayPal provider 都要用, 避免 service → pg.paypal 的反向依赖.
ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset({"HUF", "JPY", "TWD"})

# 单币种 PG 的受理集合(由 PROVIDER_CURRENCY 派生, 单一 source of truth).
_SINGLE_CURRENCY: dict[str, frozenset[str]] = {
    k: frozenset({v}) for k, v in PROVIDER_CURRENCY.items() if k != "paypal"
}


def allowed_currencies(provider: str) -> frozenset[str]:
    """该 PG 可受理的币种集合。PayPal 从配置读取(见 config.paypal_currencies)。"""
    if provider == "paypal":
        raw = (getattr(settings, "paypal_currencies", "") or "").strip()
        if raw:
            return frozenset(c.strip().upper() for c in raw.split(",") if c.strip())
        return frozenset({"USD"})
    return _SINGLE_CURRENCY.get(provider, frozenset())


HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

# 네이버페이 결제 승인(apply) 전용 타임아웃 —— 공식 주의사항 준수:
# "최종 결제 승인이 완료되기까지 시간이 걸리므로 timeout을 60초로 설정해야 합니다."
# (출처: https://docs.pay.naver.com/docs/onetime-payment/payment/apply)
# 기본 30초(read) 로 두면 정상 승인 중인데도 클라이언트가 먼저 끊어 "결제했는데 실패" 가 된다.
APPROVE_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)


def require_currency(provider: str, order) -> str:
    """校验订单币种在该 PG 的受理币种集合内, 返回该币种。

    单币种 PG(kakao/naver/wechat/alipay)集合只有一个元素, 语义与改动前完全一致;
    PayPal 是多币种 PG, 集合来自 PAYPAL_CURRENCIES(见 allowed_currencies)。
    """
    allowed = allowed_currencies(provider)
    if not allowed:
        raise ProviderNotConfigured(f"未定义 {provider} 的受理币种")
    got = (getattr(order, "currency", "") or "").upper()
    if got not in allowed:
        raise ValueError(
            f"{provider} provider 仅受理 {'/'.join(sorted(allowed))} 结算, 当前订单币种为 {got or '(空)'}"
        )
    return got


def amount_major(order) -> float:
    """订单金额(主单位: 元 / 원)。无效或非正数 → ValueError。"""
    try:
        amt = float(order.amount)
    except (TypeError, ValueError):
        raise ValueError("order.amount 无效, 无法创建收银台")
    if amt <= 0:
        raise ValueError("订单金额为 0, 无需走收银台")
    return amt


def amount_krw(order) -> int:
    """KRW 无小数位(원 为最小单位) → 取整。"""
    v = int(round(amount_major(order)))
    if v <= 0:
        raise ValueError("KRW 金额取整后为 0, 无法下单")
    return v


def amount_cny_fen(order) -> int:
    """微信支付 amount.total 单位为分。"""
    v = int(round(amount_major(order) * 100))
    if v <= 0:
        raise ValueError("CNY 金额换算为分后为 0, 无法下单")
    return v


def amount_cny_yuan_str(order) -> str:
    """支付宝 total_amount 为元, 两位小数字符串。"""
    return f"{amount_major(order):.2f}"


def out_trade_no(order) -> str:
    """商户订单号: 微信要求 6~32 位, 支付宝 <=64 位, 且商户内唯一。

    PaymentOrder.id 自增即唯一; 固定前缀 + 10 位零填充 → 15 位, 满足两家长度约束.
    """
    return f"music{int(order.id):010d}"


def post(
    url: str,
    *,
    headers: dict[str, str],
    json_body: Any | None = None,
    form: dict[str, Any] | None = None,
    timeout: httpx.Timeout | None = None,
) -> dict:
    """同步 POST + JSON 解析。

    provider.create_checkout 是同步方法(FastAPI 对同步路由用线程池执行), 故用 httpx.Client
    而非 AsyncClient —— 避免在同步上下文里手搓事件循环.

    timeout: PG 별로 공식 권고가 다른 경우에만 넘긴다(예: 네이버페이 승인 60초).
    """
    with httpx.Client(timeout=timeout or HTTP_TIMEOUT) as c:
        resp = c.post(url, headers=headers, json=json_body, data=form)
    if resp.status_code >= 400:
        raise RuntimeError(f"{url} 返回 HTTP {resp.status_code}: {resp.text[:500]}")
    if not resp.content:
        return {}
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError(f"{url} 返回非 JSON 响应: {resp.text[:500]}")


def append_query(base: str, **params: Any) -> str:
    """在 URL 后追加 query(自动判断 ? / &)。base 为空时返回相对路径, 便于本地联调。"""
    from urllib.parse import urlencode

    qs = urlencode({k: v for k, v in params.items() if v is not None})
    if not base:
        return f"?{qs}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{qs}"
