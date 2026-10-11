"""计费/支付路由 (GAP-004).

流程:
  POST /api/billing/checkout            → 创建 PaymentOrder + 返回收银入口(checkout_url)
  POST /api/billing/orders/{id}/confirm → 属主确认支付(manual/运维对账模式), 触发升档
  POST /api/billing/webhook/{provider}  → 供应商异步回调(验签后落库升档): stripe | wechat | alipay | paypal
  POST /api/billing/kakao/approve       → KakaoPay 단건결제 최종 승인(pg_token) 후 승격
  POST /api/billing/naver/approve       → NaverPay 서버 승인(paymentId) 후 승격
  GET  /api/billing/orders             → 我的订单
  GET  /api/billing/subscription       → 我的当前订阅
  GET  /api/billing/providers          → 可用支付方式(前端选择器用: flow/settle/币种约束)

终态取得方式分两类(source of truth = provider.WEBHOOK_PROVIDERS / APPROVE_PROVIDERS):
  webhook 型(stripe/wechat/alipay) —— 供应商异步回调验签成功才是升档依据.
  approve 型(kakao/naver)          —— 无 webhook, 服务端同步 approve 调用成功才是升档依据.
两类都绝不以客户端自述作为升档依据.

HTTP 语义(便于运维一眼定位):
  400 请求侧问题(币种不受理/金额 0/未知 provider/验签失败/**实收金额与订单不符**)
  402 供应商明确回复"未支付成功"
  409 订单未预创建 或 已支付(幂等)
  502 供应商上游错误(非 2xx / 非 JSON)
  503 凭据/端点/SDK 未就绪(ProviderNotConfigured)
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.billing import service
from app.billing.models import PaymentOrder
from app.billing.pg._common import allowed_currencies
from app.billing.provider import (
    APPROVE_PROVIDERS,
    SUPPORTED_PROVIDERS,
    WEBHOOK_PROVIDERS,
    ProviderNotConfigured,
    get_provider,
)
from app.config import settings
from app.db import get_db
from app.models import User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/billing", tags=["billing"])

# webhook 型 PG 的验签材料来源(签名头名 / 共享密钥). 值取自各家官方规范:
#   stripe — Stripe-Signature 头 + whsec_ 共享密钥(HMAC-SHA256)
#   wechat — Wechatpay-Signature 头(平台公钥验签) + APIv3 密钥(用于 resource AES-256-GCM 解密)
#   alipay — 签名在 form body 的 sign 字段里(无签名头); 用支付宝公钥验签, 无共享密钥
#   paypal — 验签材料分散在 5 个头里(见下), 这里只登记"签名"头用于存在性检查;
#            provider 内部再把 5 个头一起回传给 PayPal 的 verify-webhook-signature 端点.
_WEBHOOK_SIG_HEADER: dict[str, str] = {
    "stripe": "Stripe-Signature",
    "wechat": "Wechatpay-Signature",
    "alipay": "",
    "paypal": "PAYPAL-TRANSMISSION-SIG",
}

# 需要共享密钥才能验签/解密的 PG(缺失 → 503, 绝不放行未验签回调).
# alipay 不在此列: 它用支付宝公钥验签, 公钥缺失由 provider 内部抛 ProviderNotConfigured.
_SECRET_REQUIRED: frozenset[str] = frozenset({"stripe", "wechat", "paypal"})


def _webhook_secret(provider: str) -> str:
    if provider == "stripe":
        return settings.stripe_webhook_secret or ""
    if provider == "wechat":
        return settings.wechat_pay_api_v3_key or ""
    if provider == "paypal":
        # 官方 verify-webhook-signature 的必填字段: webhook_id(Developer Portal 里的 webhook ID).
        return settings.paypal_webhook_id or ""
    return ""  # alipay: 公钥验签, 无共享密钥


def _ack(provider: str, payload: dict):
    """按各家官方规范回 ack —— 回错了供应商会持续重投(噪音/重复处理风险).

    alipay: 必须返回纯文本 "success"(官方响应值说明: fail=重试, success=不重试).
    wechat: 官方 native_notify.md 2.2 —— 验签通过应答 200 或 204 即视为成功, "无需返回应答报文";
            非 2xx 才重投, 且应答报文只在失败时有定义({"code":"FAIL"}). 这里带 JSON 体不影响
            判定(微信只看状态码), 保留是为了运维可读性.
    stripe: 任意 2xx 即视为已接收(响应体不参与判定).
    """
    if provider == "alipay":
        return PlainTextResponse("success")
    if provider == "wechat":
        return {"code": "SUCCESS", **payload}
    return payload


class CheckoutReq(BaseModel):
    tier_key: str
    idempotency_key: str | None = None
    currency: str | None = None  # CNY(默认) | USD | KRW


@router.post("/checkout")
def checkout(
    req: CheckoutReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if settings.billing_mode == "live" and settings.billing_provider == "manual":
        raise HTTPException(status_code=400, detail="manual provider disabled in live mode")
    try:
        order = service.create_order(
            db,
            user=user,
            tier_key=req.tier_key,
            idempotency_key=req.idempotency_key,
            currency=req.currency,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 校验 provider 已注册: 防止 billing_provider 拼错(如 "bitcoin"/"kakaopay") → 500.
    # get_provider 对未知名抛 KeyError, 这里转成干净的 400(已注册的 6 家见 SUPPORTED_PROVIDERS).
    try:
        prov = get_provider(order.provider)
    except KeyError:
        raise HTTPException(
            status_code=400, detail=f"unsupported billing provider: {order.provider}"
        )
    try:
        result = prov.create_checkout(
            order=order, user=user, return_url=settings.billing_return_url or ""
        )
    except ProviderNotConfigured as e:
        # 凭据/端点/SDK 未就绪 → 503. 必须在 RuntimeError 之前捕获(它是 RuntimeError 子类).
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        # 币种不受理(如 kakao 只收 KRW) / 金额为 0 等请求侧问题.
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # 供应商返回非 2xx / 非 JSON / 响应缺必要字段 → 上游不可用.
        raise HTTPException(status_code=502, detail=str(e))
    order.provider_order_id = result["provider_order_id"]
    db.commit()
    resp = {
        "order_id": order.id,
        "checkout_url": result["checkout_url"],
        "amount_cny": float(order.amount_cny),
        "amount": float(order.amount),
        "currency": order.currency,
        "tier_key": order.tier_key,
        "provider": order.provider,
    }
    params = result.get("provider_params")
    if params:
        # kakao 모바일 딥링크 / naver JS SDK 파라미터 / wechat Native QR 등.
        # checkout_url 하나로 표현할 수 없는 PG 별 handoff 정보를 프론트에 그대로 넘긴다.
        resp["provider_params"] = params
    return resp


@router.post("/orders/{order_id}/confirm")
def confirm_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Manual/运维确认支付(线下收款后升档)。仅订单属主可调用。

    ⚠ 仅 manual provider 的订单允许此端点升档 —— 外部/approve/webhook 型 provider
       (stripe/wechat/alipay/kakao/naver) 的终态必须来自各自供应商侧确认
       (webhook 验签成功 / approve 同步成功), 绝不允许用本端点"代确认"绕过硬支付.
       否则会出现: 微信 checkout_url 是 weixin:// 开头(非 http) → 前端误走 manual 确认
       → 未付款却升档的免费升档漏洞. 这里在服务端再锁一道.
    """
    order = service.get_order(db, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="order not found")
    if order.provider != "manual":
        # 非 manual 订单走 confirm 端点 = 未付款升档尝试 → 拒绝(400, 请求侧错误).
        raise HTTPException(
            status_code=400,
            detail=(
                f"order {order_id} belongs to provider {order.provider}, "
                f"not manual; finalize via its provider webhook/approve flow"
            ),
        )
    if order.status == "paid":
        raise HTTPException(status_code=409, detail="already paid")
    ref = order.provider_order_id or f"manual_{order.id}"
    evt = service.apply_payment_event(
        db, provider=order.provider, provider_order_id=ref, status="paid"
    )
    return {"status": "paid", "tier_key": evt["tier_key"], "order_id": order_id}


@router.get("/orders")
def my_orders(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.user_id == user.id)
        .order_by(PaymentOrder.id.desc())
        .all()
    )
    return [
        {
            "order_id": o.id,
            "tier_key": o.tier_key,
            "amount_cny": float(o.amount_cny),
            "amount": float(o.amount),
            "currency": o.currency,
            "status": o.status,
            "provider": o.provider,
            "created_at": str(o.created_at),
        }
        for o in rows
    ]


@router.get("/subscription")
def my_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sub = service.get_active_subscription(db, user.id)
    if sub is None:
        return {"active": False, "tier_key": user.tier_key}
    return {
        "active": sub.status == "active",
        "tier_key": sub.tier_key,
        "provider": sub.provider,
        "status": sub.status,
        "current_period_end": str(sub.current_period_end) if sub.current_period_end else None,
        "cancel_at_period_end": bool(sub.cancel_at_period_end),
    }


@router.get("/plans")
def plans(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """已启用套餐列表(真实价格 source of truth = DB pricing_tiers).

    含各币种标价(price_cny/price_usd/price_krw), 供前端在选择前展示价格;
    step-2 币种选择器据此显示对应币种金额, 而非硬编码.
    """
    return service.list_plans(db)


@router.get("/providers")
def providers(user: User = Depends(get_current_user)):
    """可用支付方式清单(前端支付方式选择器的 source of truth)。

    flow 决定前端拿到 checkout 响应后的动作:
      redirect  —— 直接跳 checkout_url(stripe/alipay/kakao)
      qr        —— 用 checkout_url(weixin://...) 渲染二维码(wechat)
      sdk       —— 用 provider_params 调用 JS SDK(naver)
      internal  —— 平台内确认入口(manual)
    settle 决定升档路径: webhook(异步回调) / approve(前端回跳后调 approve 端点) / confirm(属主确认).
    不回显任何密钥, 也不回显"是否已配置"(避免向外暴露部署状态).
    """
    flow = {
        "manual": "internal",
        "stripe": "redirect",
        "alipay": "redirect",
        "kakao": "redirect",
        "wechat": "qr",
        "naver": "sdk",
        "paypal": "redirect",
    }
    out = []
    for name in SUPPORTED_PROVIDERS:
        if name in WEBHOOK_PROVIDERS:
            settle = "webhook"
        elif name in APPROVE_PROVIDERS:
            settle = "approve"
        else:
            settle = "confirm"
        out.append(
            {
                "provider": name,
                "flow": flow.get(name, "redirect"),
                "settle": settle,
                # 该 PG 受理的结算币种(多币种 PG 返回列表).
                # 单一 source of truth = pg._common(与下单时的硬校验同一份数据).
                "currency": (
                    sorted(allowed_currencies(name))
                    if len(allowed_currencies(name)) > 1
                    else next(iter(allowed_currencies(name)), None)
                ),
                "currencies": sorted(allowed_currencies(name)),
                "approve_endpoint": (
                    f"/api/billing/{name}/approve" if name in APPROVE_PROVIDERS else None
                ),
                "is_default": name == settings.billing_provider,
            }
        )
    return {"providers": out, "default": settings.billing_provider, "mode": settings.billing_mode}


@router.post("/subscription/cancel")
def cancel_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用户自助取消订阅. 默认 cancel_at_period_end=True(当期仍可用);
    传 {"at_period_end": false} 立即取消. 无订阅 → 404."""
    try:
        return service.cancel_subscription(db, user=user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/subscription/reactivate")
def reactivate_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """撤销取消意向(当期结束前恢复). 无订阅 → 404."""
    try:
        return service.reactivate_subscription(db, user=user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/webhook/{provider}")
async def webhook(
    provider: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """webhook 型 PG 通用分发(stripe / wechat / alipay).

    各家差异只在"验签材料从哪来"; 验签通过后的落库分流完全共用一套逻辑:
      kind=subscription        → apply_subscription_event(dunning/续费/取消)
      status=refund_partial    → 仅 ack, 不动订单状态机, 不降档
      status=refunded          → apply_refund_event(订单 refunded + 订阅 cancelled + 降回 free)
      其它(paid/failed)        → apply_payment_event, 并按需同步订阅状态
    """
    if provider not in WEBHOOK_PROVIDERS:
        if provider in APPROVE_PROVIDERS:
            # 这两家没有异步回调. 若 notify_url 误配到这里, 明确指向正确端点, 绝不静默 200
            # (静默 200 会让运维以为回调正常, 而升档永远不发生).
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{provider} has no webhook (approve-flow provider); "
                    f"use POST /api/billing/{provider}/approve"
                ),
            )
        raise HTTPException(status_code=400, detail=f"unsupported provider: {provider}")

    raw = await request.body()
    sig_header = _WEBHOOK_SIG_HEADER.get(provider, "")
    sig = request.headers.get(sig_header, "") if sig_header else ""
    secret = _webhook_secret(provider)
    if provider in _SECRET_REQUIRED and not secret:
        raise HTTPException(status_code=503, detail=f"{provider} webhook secret not configured")

    try:
        evt = get_provider(provider).verify_webhook(
            raw_body=raw,
            signature=sig,
            secret=secret,
            # 微信 v3 的待验签串需要 Wechatpay-Timestamp/Nonce 两个头, 单个 signature 不够 → 下传全部头.
            headers=dict(request.headers),
        )
    except ProviderNotConfigured as e:
        # 平台公钥/APIv3 密钥缺失 —— 与"签名不对"严格区分(前者改配置, 后者查伪造).
        raise HTTPException(status_code=503, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError:
        # 验签失败 / 解密失败 / 缺必需头. 细节不回显给调用方(避免为伪造者提供探测信号).
        raise HTTPException(status_code=400, detail="invalid signature")

    try:
        if evt.kind == "subscription":
            # dunning/续费/取消: 靠 provider_subscription_id 匹配已存在订阅(无订单号)
            service.apply_subscription_event(
                db,
                provider=provider,
                provider_subscription_id=evt.provider_subscription_id or "",
                status=evt.subscription_status or "active",
                current_period_end=evt.period_end,
            )
        elif evt.status == "refund_partial":
            # 部分退款(善意补偿): 不动订单状态机, 不降档. ack 后由运维在账务侧核对.
            return _ack(provider, {"received": True, "ignored": "partial_refund"})
        elif evt.status == "refunded":
            # 退款闭环: 订单置 refunded + 订阅 cancelled + 权限降回 free.
            # Stripe charge.refunded 只带 customer(无 order_id/subscription), 故三条锚点全部下传;
            # wechat/alipay 的退款通知自带 out_trade_no, 走第一条锚点即可命中.
            res = service.apply_refund_event(
                db,
                provider=provider,
                provider_order_id=evt.provider_order_id or None,
                provider_customer_id=evt.provider_customer_id,
                provider_subscription_id=evt.provider_subscription_id,
                raw_event=evt.raw,
            )
            return _ack(provider, {"received": True, "refund": res})
        else:
            # 订单事件: 先落 PaymentOrder → 升档; 携带订阅号/客户号则同步写到 Subscription
            service.apply_payment_event(
                db,
                provider=provider,
                provider_order_id=evt.provider_order_id,
                status=evt.status,
                raw_event=evt.raw,
                provider_subscription_id=evt.provider_subscription_id,
                provider_customer_id=evt.provider_customer_id,
                # 官方要求核对实收金额(支付宝 02pa44 步骤5 / 微信 amount.total), 不符即忽略.
                paid_amount_minor=evt.paid_amount_minor,
            )
            if evt.provider_subscription_id and evt.subscription_status:
                service.apply_subscription_event(
                    db,
                    provider=provider,
                    provider_subscription_id=evt.provider_subscription_id,
                    status=evt.subscription_status,
                    current_period_end=evt.period_end,
                )
    except service.AmountMismatchError as e:
        # 金额与订单不符: 官方(支付宝 02pa44 步骤5)要求"务必忽略"。
        # 与 409 区分: 重投不会改变金额, 故用 400 明确拒绝, 避免供应商反复重投 15 次.
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        # 订单尚未预创建 → 409 拒绝, 防止伪造升档.
        # 409 也让供应商按其重投策略再试, 从而覆盖"回调早于 checkout 提交"的竞态.
        raise HTTPException(status_code=409, detail=str(e))
    return _ack(provider, {"received": True})


# ---------------------------------------------------------------------------
# approve 型 PG(kakao / naver): 无 webhook, 服务端同步승인 성공만이 승격 근거
# ---------------------------------------------------------------------------
class KakaoApproveReq(BaseModel):
    order_id: int
    pg_token: str


class NaverApproveReq(BaseModel):
    order_id: int
    payment_id: str


def _load_own_unpaid_order(
    db: Session, *, order_id: int, user: User, provider: str
) -> PaymentOrder:
    """取属主本人、指定 provider、且尚未支付的订单。不满足即拒绝(不泄露存在性)。"""
    order = service.get_order(db, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="order not found")
    if order.provider != provider:
        raise HTTPException(
            status_code=400,
            detail=f"order {order_id} belongs to provider {order.provider}, not {provider}",
        )
    if order.status == "paid":
        raise HTTPException(status_code=409, detail="already paid")
    return order


def _reject_if_error_payload(provider: str, data: dict) -> None:
    """승인 응답이 **명시적 실패**를 담고 있으면 승격을 막는다.

    두 PG 모두 실패는 HTTP 4xx 로 오고(_common.post 가 RuntimeError 로 승격),
    여기까지 온 것은 이미 2xx 이다. 그래도 200 바디에 실패를 담는 변형에 대비한 이중 방어.

    ⚠ 알 수 없는 응답 형태를 실패로 단정하지 않는다 —— "사용자는 결제했는데 승격 실패" 가
       최악의 결과이므로, 명시적 실패 신호(error_code / code!=Success)만 차단한다.
       (fail-closed 가 항상 안전하지는 않다는 원칙의 적용)
    """
    if not isinstance(data, dict):
        return
    for k in ("error_code", "errorCode"):
        if data.get(k):
            raise HTTPException(status_code=402, detail=f"{provider} approve failed: {data.get(k)}")
    code = data.get("code")
    if (
        isinstance(code, str)
        and code.strip()
        and code.strip().lower() not in ("success", "0", "ok")
    ):
        raise HTTPException(status_code=402, detail=f"{provider} approve failed: code={code}")


def _finalize_approved(db: Session, *, provider: str, order: PaymentOrder, data: dict) -> dict:
    """승인 성공 확정 후 승격(공통). raw_event 는 감사를 위해 응답 원문을 저장."""
    _reject_if_error_payload(provider, data)
    try:
        raw = json.dumps(data, ensure_ascii=False, default=str)[:20000]
    except (TypeError, ValueError):
        raw = None
    try:
        evt = service.apply_payment_event(
            db,
            provider=provider,
            provider_order_id=order.provider_order_id or "",
            status="paid",
            raw_event=raw,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {
        "status": "paid",
        "tier_key": evt["tier_key"],
        "order_id": order.id,
        "applied": evt["applied"],
    }


def _call_approve(provider: str, fn, **kwargs) -> dict:
    """provider.approve 호출 + 예외 → HTTP 상태 매핑(ProviderNotConfigured 를 먼저 잡을 것)."""
    try:
        return fn(**kwargs)
    except ProviderNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=f"{provider} approve error: {e}")


@router.post("/kakao/approve")
def kakao_approve(
    req: KakaoApproveReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """카카오페이 단건결제 최종 승인 → 승격.

    프론트는 approval_url 리다이렉트 쿼리의 `pg_token` 과 `order_id` 를 그대로 보낸다.
    서버가 카카오 approve API 를 직접 호출해 2xx 를 받은 경우에만 승격한다
    (클라이언트가 "결제했다"고 주장해도 그것만으로는 절대 승격하지 않음).
    """
    order = _load_own_unpaid_order(db, order_id=req.order_id, user=user, provider="kakao")
    prov = get_provider("kakao")
    data = _call_approve("kakao", prov.approve, order=order, pg_token=req.pg_token)
    return _finalize_approved(db, provider="kakao", order=order, data=data)


@router.post("/naver/approve")
def naver_approve(
    req: NaverApproveReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """네이버페이 서버 승인 → 승격.

    프론트 JS SDK 콜백의 `paymentId` 를 그대로 보낸다. 승인 API 전체 URL 은
    파트너 계약 문서 기준값이므로 코드에 박지 않고 NAVER_PAY_APPLY_URL 로 받는다
    (미설정 시 503 —— 조용히 통과시키지 않는다).
    """
    order = _load_own_unpaid_order(db, order_id=req.order_id, user=user, provider="naver")
    prov = get_provider("naver")
    data = _call_approve("naver", prov.approve, order=order, payment_id=req.payment_id)
    return _finalize_approved(db, provider="naver", order=order, data=data)
