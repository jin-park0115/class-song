import logging

from sqlalchemy.orm import Session

from app.models import ControlAuditLog

log = logging.getLogger("jukebox.audit")


def record(db: Session, actor_id: int | None, action: str, payload: dict | None = None) -> None:
    """감사 로그를 남긴다. 커밋은 호출한 쪽에서 한다."""
    payload = payload or {}
    db.add(ControlAuditLog(actor_id=actor_id, action=action, payload=payload))
    log.info("audit action=%s actor_id=%s payload=%s", action, actor_id, payload)
