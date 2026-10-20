"""가입 승인·사용자 관리(요구사항 7.1). ADMIN 권한 부여·회수 API는 두지 않는다(CLI 전용)."""

import secrets
import string
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.deps import DB, AdminUser
from app.auth.passwords import hash_password
from app.auth.sessions import revoke_user_sessions
from app.errors import ApiError
from app.models import User
from app.services import audit

router = APIRouter(prefix="/api/admin/users", tags=["admin-users"])

Status = Literal["PENDING_APPROVAL", "ACTIVE", "REJECTED", "DISABLED"]


class RejectIn(BaseModel):
    note: str | None = Field(default=None, max_length=200)


def _out(u: User) -> dict:
    return {
        "id": u.id,
        "login_id": u.login_id,
        "name": u.name,
        "signup_note": u.signup_note,
        "role": u.role,
        "account_status": u.account_status,
        "reject_note": u.reject_note,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


def _student(db: DB, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "NOT_FOUND", "사용자를 찾을 수 없습니다.")
    if user.is_admin:
        raise ApiError(409, "ADMIN_MANAGED_BY_CLI", "관리자 계정은 CLI로만 관리합니다.")
    return user


def _set_status(db: DB, admin: User, user: User, status: str, action: str, payload: dict | None = None) -> dict:
    user.account_status = status
    user.status_changed_by = admin.id
    user.status_changed_at = datetime.now(UTC)
    if status != "ACTIVE":
        revoke_user_sessions(db, user.id)
    audit.record(db, admin.id, action, {"user_id": user.id, "login_id": user.login_id, **(payload or {})})
    db.commit()
    return _out(user)


@router.get("")
def list_users(_: AdminUser, db: DB, status: Status | None = None, limit: int = 100, offset: int = 0) -> dict:
    q = select(User).order_by(User.created_at.desc())
    if status:
        q = q.where(User.account_status == status)
    rows = db.scalars(q.limit(min(limit, 500)).offset(offset)).all()
    return {"items": [_out(u) for u in rows]}


@router.post("/{user_id}/approve")
def approve(user_id: int, admin: AdminUser, db: DB) -> dict:
    user = _student(db, user_id)
    if user.account_status not in ("PENDING_APPROVAL", "REJECTED"):
        raise ApiError(409, "INVALID_TRANSITION", "승인 대기 또는 거절 상태의 계정만 승인할 수 있습니다.")
    user.reject_note = None
    return _set_status(db, admin, user, "ACTIVE", "users.approve")


@router.post("/{user_id}/reject")
def reject(user_id: int, body: RejectIn, admin: AdminUser, db: DB) -> dict:
    user = _student(db, user_id)
    if user.account_status != "PENDING_APPROVAL":
        raise ApiError(409, "INVALID_TRANSITION", "승인 대기 중인 계정만 거절할 수 있습니다.")
    user.reject_note = (body.note or "").strip() or None
    return _set_status(db, admin, user, "REJECTED", "users.reject", {"note": user.reject_note})


@router.post("/{user_id}/disable")
def disable(user_id: int, admin: AdminUser, db: DB) -> dict:
    user = _student(db, user_id)
    if user.account_status != "ACTIVE":
        raise ApiError(409, "INVALID_TRANSITION", "사용 중인 계정만 비활성화할 수 있습니다.")
    return _set_status(db, admin, user, "DISABLED", "users.disable")


@router.post("/{user_id}/enable")
def enable(user_id: int, admin: AdminUser, db: DB) -> dict:
    user = _student(db, user_id)
    if user.account_status != "DISABLED":
        raise ApiError(409, "INVALID_TRANSITION", "비활성화된 계정만 다시 활성화할 수 있습니다.")
    return _set_status(db, admin, user, "ACTIVE", "users.enable")


def _temp_password() -> str:
    # 헷갈리는 글자(0/O, 1/l/I)를 뺀 12자
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    return "".join(secrets.choice(alphabet) for _ in range(12))


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, admin: AdminUser, db: DB) -> dict:
    """학생 임시 비밀번호 발급. 응답에 한 번만 보여주고 저장하지 않는다."""
    user = _student(db, user_id)
    temp = _temp_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    revoke_user_sessions(db, user.id)
    audit.record(db, admin.id, "users.reset_password", {"user_id": user.id, "login_id": user.login_id})
    db.commit()
    return {"temporary_password": temp, "user": _out(user)}
