from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth.deps import DB, AnyUser, CurrentSession
from app.auth.passwords import (
    DUMMY_HASH,
    hash_password,
    validate_login_id,
    validate_password,
    verify_password,
)
from app.auth.sessions import create_session, csrf_for, revoke_user_sessions
from app.config import get_settings
from app.errors import ApiError
from app.models import User
from app.services import audit
from app.services.ratelimit import client_ip
from app.services.settings import get_bool_setting, get_int_setting

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    login_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SignupIn(BaseModel):
    login_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=256)
    signup_note: str | None = Field(default=None, max_length=200)


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def _start_session(db, user: User, request: Request, response: Response) -> str:
    settings = get_settings()
    token, csrf = create_session(db, user, request.headers.get("user-agent"))
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
    return csrf


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
    request.app.state.login_limiter.check(client_ip(request))
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
    csrf = _start_session(db, user, request, response)
    if user.is_admin:
        audit.record(db, user.id, "auth.admin_login", {})
    db.commit()
    return _me(user, csrf)


@router.post("/signup", status_code=201)
def signup(body: SignupIn, request: Request, response: Response, db: DB) -> dict:
    """회원가입 → 승인 대기(PENDING_APPROVAL). 가입 후 바로 로그인되어 승인 대기 화면을 본다."""
    request.app.state.signup_limiter.check(client_ip(request))
    if not get_bool_setting(db, "signup_enabled", True):
        raise ApiError(403, "SIGNUP_CLOSED", "지금은 회원가입을 받지 않습니다.")

    login_id = body.login_id.strip().lower()
    if problem := validate_login_id(login_id):
        raise ApiError(422, "INVALID_LOGIN_ID", problem)
    if problem := validate_password(login_id, body.password):
        raise ApiError(422, "WEAK_PASSWORD", problem)
    name = body.name.strip()
    if not name:
        raise ApiError(422, "INVALID_NAME", "이름을 입력하세요.")
    if db.scalar(select(User.id).where(User.login_id == login_id)):
        raise ApiError(409, "LOGIN_ID_TAKEN", "이미 사용 중인 아이디입니다.")

    pending = db.scalar(
        select(func.count()).select_from(User).where(User.account_status == "PENDING_APPROVAL")
    )
    if pending >= get_int_setting(db, "max_pending_signups", 100):
        raise ApiError(503, "SIGNUP_QUEUE_FULL", "승인 대기 중인 가입이 많아 잠시 가입을 받을 수 없습니다.")

    user = User(
        login_id=login_id,
        name=name,
        signup_note=(body.signup_note or "").strip() or None,
        password_hash=hash_password(body.password),
        role="STUDENT",
        account_status="PENDING_APPROVAL",
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # 동시에 같은 아이디로 가입한 경우
        db.rollback()
        raise ApiError(409, "LOGIN_ID_TAKEN", "이미 사용 중인 아이디입니다.") from None
    audit.record(db, user.id, "auth.signup", {"login_id": login_id})
    csrf = _start_session(db, user, request, response)
    db.commit()
    return _me(user, csrf)


@router.post("/password")
def change_password(body: PasswordIn, user: AnyUser, request: Request, response: Response, db: DB) -> dict:
    """비밀번호 변경. 임시 비밀번호로 로그인한 경우에도 여기서 바꾼다."""
    if not verify_password(user.password_hash, body.current_password):
        raise ApiError(403, "INVALID_CREDENTIALS", "현재 비밀번호가 올바르지 않습니다.")
    if problem := validate_password(user.login_id, body.new_password):
        raise ApiError(422, "WEAK_PASSWORD", problem)
    if verify_password(user.password_hash, body.new_password):
        raise ApiError(422, "SAME_PASSWORD", "현재와 다른 비밀번호를 입력하세요.")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # 다른 기기의 세션은 모두 끊고, 지금 기기에는 새 세션을 준다
    revoke_user_sessions(db, user.id)
    csrf = _start_session(db, user, request, response)
    audit.record(db, user.id, "auth.password_changed", {})
    db.commit()
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
