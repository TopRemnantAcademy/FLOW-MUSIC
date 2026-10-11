"""이메일 발송 채널 (GAP-002) — 표준 라이브러리 smtplib 만 사용.

설계 원칙
1. **제3자 메일 API 를 날조하지 않는다.** smtplib 는 표준 라이브러리이므로 의존성 0.
   자격증명이 준비되면 `send_email()` **함수 본문만** 교체하면 된다(호출부 무변경).
2. **조용한 "성공처럼 보임" 금지.** 발송 채널이 없는데도 200 을 돌려주면
   사용자는 메일을 영원히 기다린다. → `delivery_configured()` 로 명시 판정하고,
   운영(`enable_email_delivery=True`)인데 host 가 없으면 503 으로 **크게 실패**한다.
3. **비밀번호는 절대 로그에 남기지 않는다.** 로그는 host:port 와 수신자만.
4. 메일 본문/링크 생성(`*_email_body`)과 실제 SMTP I/O(`send_email`)를 분리 —
   테스트는 smtp 계층 없이 본문에서 token 을 추출해 "사용자가 실제로 밟는 경로"를 검증한다.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def delivery_configured() -> bool:
    """실제 발송이 가능한 구성인지 (smtp_host 가 채워졌는지)."""
    return bool(settings.smtp_host.strip())


def ensure_delivery_available() -> None:
    """발송이 **불가능한데** 요청이 성공으로 보일 상황을 막는다(fail-closed).

    - enable_email_delivery=False (기본, 개발/CI): 발송 채널이 없어도 그냥 로그만 남긴다.
    - enable_email_delivery=True (운영 의도): smtp_host 미설정이면 503 —
      "메일 보냈다"고 거짓 응답하지 않는다.
    """
    if settings.enable_email_delivery and not delivery_configured():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="이메일 발송이 구성되지 않았습니다(SMTP_HOST 미설정).",
        )


def send_email(to: str, subject: str, body: str) -> bool:
    """이메일 1통 발송. **이 함수가 유일한 교체 지점(replaceable seam)**.

    반환값: 실제로 SMTP 에 전달했으면 True, 개발 모드에서 미발송이면 False.
    SMTP 오류는 삼키지 않고 로그 + False 로 보고한다(호출부가 500 을 선택할 수 있게).
    """
    if not delivery_configured():
        # 개발 모드: 본문을 로그로만 확인할 수 있다(메일함 없이 링크를 따라가는 용도).
        # ⚠ 본문에는 token 이 들어 있으므로 WARNING 레벨로만 남기고 운영에선 host 를 설정할 것.
        logger.warning(
            "[email] 발송 채널 미구성(SMTP_HOST 비어 있음) — 미발송. to=%s subject=%s\n%s",
            to,
            subject,
            body,
        )
        return False

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
            ) as smtp:
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
            ) as smtp:
                if settings.smtp_use_starttls:
                    smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        # 비밀번호는 절대 로그에 남기지 않는다 — host:port 와 예외만.
        logger.error("[email] 발송 실패 host=%s:%s err=%s", settings.smtp_host, settings.smtp_port, exc)
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
