from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.deps import DB, AnyUser, CurrentSession
from app.auth.passwords import DUMMY_HASH, verify_password
from app.auth.sessions import create_session, csrf_for
from app.config import get_settings
from app.errors import ApiError
from app.models import User
from app.services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    login_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _me(user: User, csrf_token: str) -> dict:
    return {
        "id": user.id,
        "login_id": user.login_id,
        "name": user.name,
        "role": user.role,
        "account_status": user.account_status,
        "must_change_password": user.must_change_password,
        "csrf_token": csrf_token,
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: DB) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    user = db.scalar(select(User).where(User.login_id == body.login_id.strip().lower()))

    if user is None:
        verify_password(DUMMY_HASH, body.password)
        raise ApiError(401, "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.")

    if user.locked_until and user.locked_until > now:
        raise ApiError(423, "ACCOUNT_LOCKED", "로그인 실패가 반복되어 잠시 잠겼습니다. 잠시 후 다시 시도하세요.")

    if not verify_password(user.password_hash, body.password):
        user.failed_login_count += 1
        locked = user.failed_login_count >= settings.login_max_failures
        if locked:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.failed_login_count = 0
        audit.record(db, user.id, "auth.login_failed", {"locked": locked})
        db.commit()
        if locked:
            raise ApiError(423, "ACCOUNT_LOCKED", "로그인 실패가 반복되어 잠시 잠겼습니다. 잠시 후 다시 시도하세요.")
        raise ApiError(401, "INVALID_CREDENTIALS", "아이디 또는 비밀번호가 올바르지 않습니다.")

    if user.account_status in ("REJECTED", "DISABLED"):
        raise ApiError(403, "ACCOUNT_INACTIVE", "사용할 수 없는 계정입니다.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    token, csrf = create_session(db, user, request.headers.get("user-agent"))
    if user.is_admin:
        audit.record(db, user.id, "auth.admin_login", {})
    db.commit()

    max_age = (
        settings.session_ttl_admin_hours if user.is_admin else settings.session_ttl_student_hours
    ) * 3600
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return _me(user, csrf)


@router.post("/logout")
def logout(row: CurrentSession, response: Response, db: DB) -> dict:
    db.delete(row)
    db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: AnyUser, request: Request) -> dict:
    return _me(user, csrf_for(request.cookies[get_settings().session_cookie_name]))
