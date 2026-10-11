"""NaverPay provider (KRW) — 프론트 JS SDK handoff + 서버 승인.

공식 문서로 실증된 사실만 코드에 반영한다(추정/환각 금지).
실증일: 2026-09-04 / 출처: 네이버페이 개발자센터(공식)

  [SDK · 결제 인증 창]
  https://docs.pay.naver.com/docs/onetime-payment/payment/payment-auth-window
   · SDK 스크립트(공식 고정 주소, 반드시 이 링크로 적용):
       <script src="https://nsp.pay.naver.com/sdk/js/naverpay.min.js"></script>
   · Naver.Pay.create({ clientId(필수), chainId(필수), mode, payType, openType, onAuthorize })
       - mode: development | production (기본 production)
       - payType: 일반결제 SDK 는 "normal" 로 설정 (기본값 normal)
   · oPay.open({ merchantPayKey(필수), productName(필수), productCount(필수),
                 totalPayAmount(필수), taxScopeAmount(필수), taxExScopeAmount(필수),
                 returnUrl(필수), productItems(필수), merchantUserKey, ... })
       ⚠ productCount / productItems 는 **필수**. 없으면 결제창이 열리지 않는다.
   · productItem 원소 필수 필드: categoryType, categoryId, uid, name, count
       - 공식 상품유형 표에서 디지털 컨텐츠 = categoryType "PRODUCT" / categoryId "DIGITAL_CONTENT"
         (출처: https://docs.pay.naver.com/docs/more-information/product-item)
   · returnUrl 리다이렉트 (공식 응답 예):
       성공: Redirect: {your-returnUrl}?resultCode=Success&paymentId={네이버페이 결제번호}
       실패: Redirect: {your-returnUrl}?resultCode={resultCode}&resultMessage={...}&reserveId={...}
     → 즉 **paymentId 는 resultCode=Success 일 때만 온다**. resultCode 검증 없이
       paymentId 만 읽으면 취소(UserCancel) 건을 승인 시도하게 된다.

  [서버 승인 API]
  https://docs.pay.naver.com/docs/onetime-payment/payment/apply
   · POST https://{API 도메인}/naverpay-partner/naverpay/payments/v2.2/apply/payment
   · 호스트(공식): 개발 dev-pay.paygate.naver.com / 운영 pay.paygate.naver.com
     (출처: https://docs.pay.naver.com/docs/common/url-format)
   · Content-Type: application/x-www-form-urlencoded, body: paymentId (필수, 최대 50바이트)
   · 헤더: X-Naver-Client-Id / X-Naver-Client-Secret / X-NaverPay-Chain-Id /
           X-NaverPay-Idempotency-Key
   · timeout 60초 필수(공식 주의사항)
   · 응답: { code, message, body: { paymentId, detail: { totalPayAmount, ... } } }
     code 값: Success | Fail | InvalidMerchant | TimeExpired | AlreadyOnGoing |
              AlreadyComplete | OwnerAuthFail | BankMaintenance | NotEnoughAccountBalance |
              MaintenanceOngoing | FaultCheckOngoing

  [보안 가이드 — 필수]
  https://docs.pay.naver.com/docs/more-information/security
   · "3단계: 최종 결제 결과 안내 전 확인(필수)" —— 승인 응답의
     **totalPayAmount(필수)** 를 가맹점 기대값과 비교 검증해야 한다.
     → 본 모듈은 approve() 에서 이 검증을 수행하고 불일치 시 승격을 막는다.

⚠ 네이버페이도 승인 응답이 최종 결과이므로 webhook 은 사용하지 않는다
   (verify_webhook → NotImplementedError, 승격은 서버 승인 성공 후에만).
"""

from __future__ import annotations

import re

from app.billing.pg._common import (
    APPROVE_HTTP_TIMEOUT,
    ProviderNotConfigured,
    amount_krw,
    append_query,
    post,
    require_currency,
)
from app.billing.provider import PaymentProvider, WebhookEvent
from app.config import settings

# 공식 API 호스트(https://docs.pay.naver.com/docs/common/url-format).
_API_HOSTS = {
    "development": "https://dev-pay.paygate.naver.com",
    "production": "https://pay.paygate.naver.com",
}
# 공식 승인 API 경로(v2.2). 파트너 계약 도메인을 쓰는 경우 NAVER_PAY_APPLY_URL 로 덮어쓴다.
_APPLY_PATH = "/naverpay-partner/naverpay/payments/v2.2/apply/payment"

# 공식 SDK 스크립트 주소(프론트가 그대로 사용).
SDK_SCRIPT_URL = "https://nsp.pay.naver.com/sdk/js/naverpay.min.js"


def _require_sdk_config() -> tuple[str, str, str]:
    client_id = (settings.naver_pay_client_id or "").strip()
    chain_id = (settings.naver_pay_chain_id or "").strip()
    mode = (settings.naver_pay_mode or "").strip() or "development"
    if not client_id:
        raise ProviderNotConfigured("NAVER_PAY_CLIENT_ID 未配置.")
    if not chain_id:
        raise ProviderNotConfigured("NAVER_PAY_CHAIN_ID 未配置.")
    if mode not in ("development", "production"):
        raise ProviderNotConfigured(f"NAVER_PAY_MODE 无效: {mode} (허용: development | production)")
    return client_id, chain_id, mode


def _apply_url(mode: str) -> str:
    """서버 승인 API 전체 URL. 계약 도메인 오버라이드가 있으면 그것을 우선한다."""
    override = (settings.naver_pay_apply_url or "").strip()
    if override:
        return override
    return f"{_API_HOSTS[mode]}{_APPLY_PATH}"


def _product_uid(tier_key: str) -> str:
    """productItem.uid —— 공식 허용문자(영문/숫자/공백/`/`/`-`/`_`) 만 남긴다(최대 100바이트)."""
    cleaned = re.sub(r"[^A-Za-z0-9 /_-]", "_", f"music-plan-{tier_key or 'standard'}")
    return cleaned[:100]


class NaverPayProvider(PaymentProvider):
    name = "naver"

    def create_checkout(self, *, order, user, return_url: str) -> dict:
        require_currency("naver", order)
        total = amount_krw(order)
        client_id, chain_id, mode = _require_sdk_config()
        ret = (return_url or settings.billing_return_url or "").rstrip("/")
        # merchantPayKey: 가맹점 결제 고유키. PaymentOrder.id 가 결제 시도마다 새로 생성되므로 그대로 사용.
        pay_key = f"naver{int(order.id):010d}"
        landing = append_query(ret, provider="naver", order_id=order.id)
        product_name = f"구독 플랜 · {order.tier_key}"[:128]
        return {
            # SDK handoff: 프론트가 이 URL(자기 라우트) 에서 provider_params 로 oPay.open 을 호출한다.
            "checkout_url": landing,
            "provider_order_id": pay_key,
            "provider_params": {
                "sdk": "naverpay",
                # 공식 고정 스크립트 주소(https://pay.naver.com/... 아님 — 오타 시 SDK 로드 실패).
                "sdk_script": SDK_SCRIPT_URL,
                "mode": mode,
                # 공식: 일반결제 SDK 는 payType 을 normal 로 설정.
                "payType": "normal",
                "clientId": client_id,
                "chainId": chain_id,
                "merchantUserKey": str(order.user_id),
                "merchantPayKey": pay_key,
                "productName": product_name,
                # 공식 필수값(productCount/productItems). 누락 시 결제창이 열리지 않는다.
                "productCount": 1,
                "productItems": [
                    {
                        # 공식 상품유형 표: 디지털 컨텐츠 = PRODUCT / DIGITAL_CONTENT
                        "categoryType": "PRODUCT",
                        "categoryId": "DIGITAL_CONTENT",
                        "uid": _product_uid(getattr(order, "tier_key", "")),
                        "name": product_name,
                        "count": 1,
                    }
                ],
                "totalPayAmount": total,
                # 면세 상품(디지털 구독) 이 아니면 taxScopeAmount 에 전액. 부가세 정책은 가맹점 계약 기준.
                "taxScopeAmount": total,
                "taxExScopeAmount": 0,
                "returnUrl": landing,
            },
        }

    def approve(self, *, order, payment_id: str) -> dict:
        """서버 승인 + 공식 보안 검증(totalPayAmount 일치 확인).

        공식 보안 가이드 3단계(필수): 최종 결제 결과 안내 전에 응답의 totalPayAmount 가
        가맹점 기대값과 일치하는지 확인한다. 불일치는 금액 위/변조 가능성이 있으므로
        승격을 절대 진행하지 않는다(→ ValueError, 라우터에서 400).
        """
        if not payment_id:
            raise ValueError("paymentId 가 없다: 네이버페이 SDK 콜백 값을 그대로 전달하라.")
        client_id, chain_id, mode = _require_sdk_config()
        secret = (settings.naver_pay_secret_key or "").strip()
        if not secret:
            raise ProviderNotConfigured("NAVER_PAY_SECRET_KEY 未配置: 서버 승인에 필요하다.")
        url = _apply_url(mode)
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": secret,
            "X-NaverPay-Chain-Id": chain_id,
            # 멱등키: 같은 주문으로 재시도해도 중복 승인되지 않게 주문 id 로 고정.
            "X-NaverPay-Idempotency-Key": f"order-{order.id}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        # 공식 주의: 승인 완료까지 시간이 걸리므로 timeout 60초.
        data = post(
            url, headers=headers, form={"paymentId": payment_id}, timeout=APPROVE_HTTP_TIMEOUT
        )
        _verify_approved_amount(order, data)
        return data

    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        headers: dict[str, str] | None = None,
    ) -> WebhookEvent:
        raise NotImplementedError(
            "NaverPay 는 서버 승인 응답이 최종 결과이므로 webhook 을 쓰지 않는다. "
            "승격은 POST /api/billing/naver/approve 를 사용하라."
        )


def _verify_approved_amount(order, data: dict) -> None:
    """공식 보안 가이드 3단계(필수): 응답 totalPayAmount == 주문 금액.

    - 검증 위치: body.detail.totalPayAmount (공식 응답 구조)
    - 응답 형태를 알 수 없으면(=필드 없음) 단정하지 않고 통과시킨다. 승격 차단보다
      "결제했는데 승격 실패" 가 더 나쁜 결과이기 때문(모르는 형태를 실패로 단정 금지 원칙).
    """
    if not isinstance(data, dict):
        return
    body = data.get("body")
    detail = body.get("detail") if isinstance(body, dict) else None
    paid = None
    if isinstance(detail, dict) and detail.get("totalPayAmount") is not None:
        paid = detail.get("totalPayAmount")
    elif isinstance(body, dict) and body.get("totalPayAmount") is not None:
        paid = body.get("totalPayAmount")
    if paid is None:
        return
    try:
        paid_int = int(paid)
    except (TypeError, ValueError):
        raise ValueError(f"네이버페이 승인 응답 totalPayAmount 형식이 이상하다: {paid!r}")
    expected = amount_krw(order)
    if paid_int != expected:
        raise ValueError(
            f"네이버페이 승인 금액 불일치: 응답 totalPayAmount={paid_int}, 주문 금액={expected}. "
            "승격을 중단한다(금액 위/변조 가능성)."
        )
