"""관리자 감사 로그 — 모든 /api/admin 접근(변경/실패 시도 포함) 기록."""

from app.db import SessionLocal
from app.models import AuditLog


def log_admin_access(
    *,
    method: str,
    path: str,
    status_code: int,
    token_prefix: str,
    role: str,
    ip: str,
) -> None:
    """관리자 요청 1건을 감사 로그에 기록. 실패하더라도 요청 흐름을 방해하지 않는다."""
    db = None
    try:
        db = SessionLocal()
        db.add(
            AuditLog(
                method=method,
                path=path,
                status_code=status_code,
                actor_token_prefix=token_prefix or "",
                actor_role=role or "none",
                client_ip=ip or "",
            )
        )
        db.commit()
    except Exception:
        if db is not None:
            db.rollback()
    finally:
        if db is not None:
            db.close()
