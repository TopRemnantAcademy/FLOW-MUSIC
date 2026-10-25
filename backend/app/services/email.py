"""이메일 발송 채널 (GAP-002) — AWS SES via **boto3** (AWS 공식 SDK).

설계 원칙
1. **발송 링크를手搓하지 않는다** (2026-10-25 교체, 감사 결론): 종래 구현은
   stdlib `smtplib` 로 SMTP 서버에 직결했다 — 그것은 자체 메일 발송 스택
   (재시도/백오프/반송 처리 전무) 이라 禁手搓清单 「자체 SMTP」 항목에 해당.
   지금은 boto3 → SES: boto3 는 AWS 공식 SDK (requirements.txt 에 이미 선언,
   **추가 의존성 0**) 이며 재시도·전달 상태·반송(bounce) 처리를 내장한다.
   자격증명은 boto3 표준 체인 (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
   환경변수 또는 IAM Role) — 프로젝트 자용 자격증명 변수를 새로 만들지 않는다.
2. **조용한 "성공처럼 보임" 금지.** `ses_source` 미설정 = 개발 모드(로그만).
   운영(`enable_email_delivery=True`)인데 미설정이면 503 으로 **크게 실패**.
3. **비밀번호/자격증명은 절대 로그에 남기지 않는다.** 로그는 region/수신자/
   예외 클래스명만 (boto3 예외 메시지에 자격증명이 섞이지 않음을 전제하되,
   AccessKey 는 절대 직접 로그하지 않는다).
4. 본문/링크 생성(`*_email_body`)과 실제 I/O(`send_email`)를 분리 —
   테스트는 네트워크 없이 본문에서 token 을 추출해 "사용자가 실제로 밟는 경로"를
   검증한다 (boto3.client 를 monkeypatch 하는 fake 주입).
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def delivery_configured() -> bool:
    """실제 발송이 가능한 구성인지 (SES Source 주소가 채워졌는지)."""
    return bool(settings.ses_source.strip())


def ensure_delivery_available() -> None:
    """발송이 **불가능한데** 요청이 성공으로 보일 상황을 막는다(fail-closed).

    - enable_email_delivery=False (기본, 개발/CI): 채널이 없어도 로그만 남긴다.
    - enable_email_delivery=True (운영 의도): ses_source 미설정이면 503 —
      "메일 보냈다"고 거짓 응답하지 않는다.
    """
    if settings.enable_email_delivery and not delivery_configured():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="이메일 발송이 구성되지 않았습니다(SES_SOURCE 미설정).",
        )


def _ses_client():
    """SES 클라이언트 (lazy import — boto3 미설치 환경에서도 dev 모드는 동작).

    타임아웃: connect/read 를 settings.ses_timeout 으로 제한 + 표준 재시도
    (최대 3 회). SES 는 비멱등 엔드포인트이므로 재시도는 botocore 표준
    모드(지수 백오프)에 맡기고, 횟수는 보수적으로 제한한다.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "ses",
        region_name=settings.ses_region or None,
        config=Config(
            connect_timeout=int(settings.ses_timeout),
            read_timeout=int(settings.ses_timeout),
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def send_email(to: str, subject: str, body: str) -> bool:
    """이메일 1통 발송. **이 함수가 유일한 교체 지점(replaceable seam)**.

    반환값: SES 에 실제 전달했으면 True, 개발 모드에서 미발송이면 False.
    SES 오류는 삼키지 않고 로그 + False 로 보고한다(호출부가 500 을 선택할 수 있게).
    """
    if not delivery_configured():
        # 개발 모드: 본문을 로그로만 확인할 수 있다(메일함 없이 링크를 따라가는 용도).
        # ⚠ 본문에는 token 이 들어 있으므로 WARNING 레벨로만 남기고 운영에선
        #   SES_SOURCE 를 설정할 것.
        logger.warning(
            "[email] 발송 채널 미구성(SES_SOURCE 비어 있음) — 미발송. to=%s subject=%s\n%s",
            to,
            subject,
            body,
        )
        return False

    try:
        from botocore.exceptions import BotoCoreError, ClientError

        _ses_client().send_email(
            Source=settings.ses_source,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        # 자격증명은 절대 로그에 남기지 않는다 — region 과 예외 클래스명만.
        logger.error(
            "[email] 발송 실패 region=%s err=%s",
            settings.ses_region,
            type(exc).__name__,
        )
        return False
    logger.info("[email] 발송 완료 to=%s", to)
    return True


# ── 본문/링크 생성 (순수 함수 — 테스트가 여기서 token 을 파싱한다) ──


def verification_email_body(link: str, expire_minutes: int) -> str:
    return (
        "FLOW MUSIC 이메일 인증\n"
        "\n"
        "아래 링크를 눌러 이메일 인증을 완료하세요.\n"
        f"{link}\n"
        "\n"
        f"링크는 {expire_minutes} 분 후 만료됩니다.\n"
        "본인이 요청하지 않았다면 이 메일을 무시하세요.\n"
    )


def reset_email_body(link: str, expire_minutes: int) -> str:
    return (
        "FLOW MUSIC 비밀번호 재설정\n"
        "\n"
        "아래 링크를 눌러 새 비밀번호를 설정하세요.\n"
        f"{link}\n"
        "\n"
        f"링크는 {expire_minutes} 분 후 만료됩니다.\n"
        "본인이 요청하지 않았다면 이 메일을 무시하고 비밀번호를 변경하세요.\n"
    )


def verification_link(raw_token: str) -> str:
    return f"{settings.frontend_base_url}/verify-email?token={raw_token}"


def reset_link(raw_token: str) -> str:
    return f"{settings.frontend_base_url}/reset-password?token={raw_token}"


def send_verification_email(to: str, raw_token: str, expire_minutes: int) -> bool:
    """인증 메일. token 은 **평문 그대로 링크에만** 실린다(DB 에는 sha256 만)."""
    return send_email(
        to,
        "FLOW MUSIC — 이메일 인증",
        verification_email_body(verification_link(raw_token), expire_minutes),
    )


def send_reset_email(to: str, raw_token: str, expire_minutes: int) -> bool:
    return send_email(
        to,
        "FLOW MUSIC — 비밀번호 재설정",
        reset_email_body(reset_link(raw_token), expire_minutes),
    )
