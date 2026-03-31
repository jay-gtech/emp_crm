"""
Audit logging service.
log_action() is the single entry point — it never raises.
"""
from __future__ import annotations

from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog, AuditAction

try:
    from app.services.notification_service import create_notification_from_audit as _create_notif
    _NOTIF_OK = True
except ImportError:
    _NOTIF_OK = False


def log_action(
    db: Session,
    actor_id: int,
    action: str | AuditAction,
    target_type: str,
    target_id: int,
    detail: str | None = None,
) -> None:
    """
    Persist one audit log entry.  Safe to call from any route handler —
    failures are silently swallowed so auditing never blocks business logic.
    """
    try:
        if not isinstance(action, AuditAction):
            action = AuditAction(action)
        entry = AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        
        if _NOTIF_OK:
            try:
                _create_notif(db, entry)
            except Exception:
                pass
    except Exception:
        db.rollback()


def list_audit_logs(
    db: Session,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    """Returns audit log entries as dicts, newest first. Returns [] on error."""
    try:
        q = db.query(AuditLog)
        if actor_id is not None:
            q = q.filter(AuditLog.actor_id == actor_id)
        if target_type is not None:
            q = q.filter(AuditLog.target_type == target_type)
        if target_id is not None:
            q = q.filter(AuditLog.target_id == target_id)
        rows = q.order_by(AuditLog.created_at.desc()).limit(limit).all()
        return [
            {
                "id": r.id,
                "actor_id": r.actor_id,
                "action": r.action.value,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "detail": r.detail,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    except Exception:
        return []
