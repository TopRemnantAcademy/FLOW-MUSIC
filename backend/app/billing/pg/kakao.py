"""KakaoPay 단건결제 provider (KRW) — 官方契约(실증 완료).

实证来源(2026-09-04, 카카오페이 개발자센터 공식 문서 본문):
  - 단건 결제    : https://developers.kakaopay.com/docs/payment/online/single-payment
                   (본문 HTML: GET https://developers.kakaopay.com/api/v1/documents/single-payment/html)
  - REST API 공통: https://developers.kakaopay.com/docs/payment/online/reference
                   (본문 HTML: .../api/v1/documents/restapi/html)

=== 확정된 공식 사실(추정 아님) ===
1) 호스트: `open-api.kakaopay.com` (공식: "호스트 도메인은 open-api.kakaopay.com 으로 호출해야
   합니다. 개발환경은 지원하고 있지 않습니다.") → **샌드박스 호스트는 존재하지 않는다**.
   ⚠ 구 카카오디벨로퍼스(kapi.kakao.com/v1/payment/*) 는 공식 종료됨
     (개발자센터 공지: "카카오디벨로퍼스를 통한 카카오페이 API 제공이 종료되었습니다").
2) 경로: `POST /online/v1/payment/ready`, `POST /online/v1/payment/approve`.
3) 인증 헤더: `Authorization: SECRET_KEY ${SECRET_KEY}` (공식 표기 그대로).
   테스트 결제는 CID `"TC0ONETIME"` + `"Secret key(dev)"` 로 호출 가능.
4) 바디 인코딩: **`Content-Type: application/json` + JSON 객체**.
   (공식 REST API 문서: "파라미터가 JSON 객체로 표현된 경우 Content-type: application/json;charset=UTF-8")
5) `pg_token` 전달 방식 — 공식 원문:
   "사용자가 카카오톡 결제 화면에서 결제 수단을 선택하고 비밀번호 인증까지 마치면, 결제 대기 화면은
    결제 준비 API 요청 시 전달받은 approval_url 에 pg_token 파라미터를 붙여 대기화면을
    approval_url 로 redirect 합니다."
   approve 파라미터 표 원문: "결제승인 요청을 인증하는 토큰 사용자 결제 수단 선택 완료 시,
    approval_url로 redirection 해줄 때 pg_token을 query string으로 전달"
   → 즉 **approval_url 의 query string 으로 `pg_token` 이 실려 온다** (프론트가 읽어 서버로 전달).
6) 에러 응답: `{"error_code": <Integer>, "error_message": "<String>"}` (HTTP 4xx/5xx 와 함께).
7) TLS 1.2 만 지원(1.3 미지원).

=== 본 구현이 의도적으로 하는 것 ===
- ready 요청에서 `vat_amount` 를 **보내지 않는다**: 공식 표에서 X(선택)이며 미전송 시
  "(상품총액 - 상품 비과세 금액)/11" 로 자동 계산된다. 0 으로 고정하면 부가세를 0원으로
  신고하게 되므로, 공식 기본 동작(자동 계산)에 맡긴다.
- approve 요청에 `total_amount` 를 실어 보낸다: 공식 표에서 X(선택)이나
  "결제 준비 API 요청과 일치해야 함" 으로 명시되어 있어, 카카오 서버가 금액을 대조하게 하는
  서버측 바인딩 역할을 한다(금액 조작 방어).
- `kakao_pay_auth_scheme` 은 하위 호환용으로만 남긴다. 기본값은 공식 형태인 `SECRET_KEY`.

⚠ 카카오페이 단건결제는 approve 응답이 곧 최종 결과이므로 **webhook 이 없다**.
   따라서 verify_webhook 은 명시적으로 미지원을 알리고, 승격은
   POST /api/billing/kakao/approve 라우트가 서버측 approve 성공 후에만 수행한다.
"""

from __future__ import annotations

from app.billing.pg._common import (
    ProviderNotConfigured,
    amount_krw,
    append_query,
    post,
    require_currency,
)
from app.billing.provider import PaymentProvider, WebhookEvent
from app.config import settings

# 공식 경로(호스트 open-api.kakaopay.com). 구 kapi.kakao.com 의 /v1/... 아님.
READY_PATH = "/online/v1/payment/ready"
APPROVE_PATH = "/online/v1/payment/approve"

# 공식 approval_url / cancel_url / fail_url 최대 길이(255자).
_MAX_URL_LEN = 255


def _auth_header() -> dict[str, str]:
    """공식 인증 헤더. scheme 은 공식 표기 `SECRET_KEY`(기본) / 레거시 `KakaoAK`(호환)."""
    cred = (settings.kakao_pay_credential or "").strip()
    scheme = (settings.kakao_pay_auth_scheme or "").strip() or "SECRET_KEY"
    if not cred:
        raise ProviderNotConfigured(
            "KAKAO_PAY_CREDENTIAL 未配置: 카카오페이 Secret key(또는 레거시 ADMIN_KEY) 를 설정해야 한다."
        )
    if not (settings.kakao_pay_cid or "").strip():
        raise ProviderNotConfigured(
            "KAKAO_PAY_CID 未配置: 가맹점 코드(테스트 TC0ONETIME) 를 설정하라."
        )
    if scheme not in ("SECRET_KEY", "KakaoAK"):
        raise ProviderNotConfigured(
            f"KAKAO_PAY_AUTH_SCHEME 无效: {scheme} (허용: SECRET_KEY | KakaoAK)"
        )
    return {
        "Authorization": f"{scheme} {cred}",
        # 공식: 파라미터가 JSON 객체이므로 application/json. (form-urlencoded 아님)
        "Content-type": "application/json;charset=UTF-8",
    }


def _base() -> str:
    """API 호스트. 공식값 `https://open-api.kakaopay.com`(샌드박스 호스트 없음)."""
    return (settings.kakao_pay_api_base or "https://open-api.kakaopay.com").rstrip("/")


class KakaoPayProvider(PaymentProvider):
    name = "kakao"

    def create_checkout(self, *, order, user, return_url: str) -> dict:
        require_currency("kakao", order)
        total = amount_krw(order)
        headers = _auth_header()
        ret = (return_url or settings.billing_return_url or "").rstrip("/")
        # 공식 확인: 카카오가 approval_url 뒤에 `pg_token` 을 **query string** 으로 붙여 리다이렉트한다.
        # order_id 를 미리 실어 두면 회귀 시 어느 주문을 승인할지 복원할 수 있다.
        approval = append_query(ret, provider="kakao", order_id=order.id)
        cancel = append_query(ret, provider="kakao", order_id=order.id, canceled=1)
        fail = append_query(ret, provider="kakao", order_id=order.id, failed=1)
        for name_, u in (("approval_url", approval), ("cancel_url", cancel), ("fail_url", fail)):
            if len(u) > _MAX_URL_LEN:
                raise ValueError(
                    f"{name_} 가 공식 최대 길이 {_MAX_URL_LEN}자를 초과했다({len(u)}자): "
                    "BILLING_RETURN_URL 을 짧게 유지하라."
                )
        body = {
            "cid": settings.kakao_pay_cid.strip(),
            "partner_order_id": str(order.id),
            # 공식: 최대 100자, 실명/휴대폰/이메일 등 개인정보 전송 불가 → 내부 숫자 id 만 사용.
            "partner_user_id": str(order.user_id),
            "item_name": f"구독 플랜 · {order.tier_key}"[:100],
            "quantity": 1,  # 공식: Integer
            "total_amount": total,  # 공식: Integer
            "tax_free_amount": 0,
            "approval_url": approval,
            "cancel_url": cancel,
            "fail_url": fail,
            # vat_amount 는 의도적으로 미전송 → 공식 자동 계산((총액 - 비과세)/11) 에 맡긴다.
        }
        # 공식: JSON 바디(application/json). form 으로 보내면 카카오가 파라미터를 읽지 못한다.
        data = post(f"{_base()}{READY_PATH}", headers=headers, json_body=body)
        tid = data.get("tid")
        redirect = data.get("next_redirect_pc_url") or data.get("next_redirect_mobile_url")
        if not tid or not redirect:
            raise RuntimeError(f"KakaoPay ready 응답에 tid/redirect 가 없음: {data}")
        return {
            "checkout_url": redirect,
            "provider_order_id": str(tid),
            # 모바일 딥링크/앱 스킴도 함께 노출(프론트가 디바이스별로 선택 가능).
            "provider_params": {
                "next_redirect_mobile_url": data.get("next_redirect_mobile_url"),
                "next_redirect_app_url": data.get("next_redirect_app_url"),
                "android_app_scheme": data.get("android_app_scheme"),
                "ios_app_scheme": data.get("ios_app_scheme"),
            },
        }

    def approve(self, *, order, pg_token: str) -> dict:
        """최종 승인. 성공 응답만 승격 근거가 된다(HTTP 4xx/5xx → RuntimeError).

        tid 는 ready 단계에서 PaymentOrder.provider_order_id 로 저장해 둔 값.
        """
        if not pg_token:
            raise ValueError("pg_token 이 없다: approval_url 리다이렉트 쿼리에서 그대로 전달하라.")
        tid = (order.provider_order_id or "").strip()
        if not tid:
            raise ValueError("주문에 tid(provider_order_id) 가 없다: ready 단계를 먼저 수행하라.")
        headers = _auth_header()
        body = {
            "cid": settings.kakao_pay_cid.strip(),
            "tid": tid,
            "partner_order_id": str(order.id),
            "partner_user_id": str(order.user_id),
            "pg_token": pg_token,
            # 공식 표에선 선택(X)이나 "결제 준비 API 요청과 일치해야 함" → 서버측 금액 바인딩.
            "total_amount": amount_krw(order),
        }
        return post(f"{_base()}{APPROVE_PATH}", headers=headers, json_body=body)

    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        headers: dict[str, str] | None = None,
    ) -> WebhookEvent:
        raise NotImplementedError(
            "KakaoPay 단건결제는 webhook 이 없다(approve 응답이 최종 결과). "
            "승격은 POST /api/billing/kakao/approve 를 사용하라."
        )
