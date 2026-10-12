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


def log_moderation_action(
    *,
    action: str,
    target_type: str,
    target_id: int,
    actor_role: str,
    actor_token_prefix: str,
    detail: str = "",
    path: str = "",
    client_ip: str = "",
) -> None:
    """记录一次审核动作（批准/驳回/下架/举报处置）到 audit_logs。

    GAP-016：审核动作必须可独立查询与追责，而不是只靠 admin 访问日志。
    与 log_admin_access 一样：**失败不得中断业务流程**（审计是旁路，不是关键路径），
    但也不能静默——记一条服务端日志，便于发现「审计写不进去」这种系统性故障。
    """
    import logging

    db = None
    try:
        db = SessionLocal()
        db.add(
            AuditLog(
                method="POST",
                path=path or f"/api/admin/{target_type}/{target_id}/{action}",
                status_code=200,
                actor_token_prefix=actor_token_prefix or "",
                actor_role=actor_role or "none",
                client_ip=client_ip or "",
                action=action,
                target_type=target_type,
                target_id=int(target_id),
                detail=(detail or "")[:2000],
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 — 审计旁路，绝不阻断主流程
        logging.getLogger(__name__).exception("审核动作审计写入失败（action=%s id=%s）", action, target_id)
        if db is not None:
            db.rollback()
    finally:
        if db is not None:
            db.close()
