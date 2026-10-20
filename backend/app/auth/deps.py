from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.sessions import csrf_matches, load_session
from app.config import get_settings
from app.db import get_db
from app.errors import ApiError
from app.models import AuthSession, User

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def current_session(request: Request, db: Annotated[Session, Depends(get_db)]) -> AuthSession:
    token = request.cookies.get(get_settings().session_cookie_name)
    row = load_session(db, token) if token else None
    if row is None:
        raise ApiError(401, "UNAUTHENTICATED", "로그인이 필요합니다.")
    if request.method not in SAFE_METHODS and not csrf_matches(
        row, request.headers.get("X-CSRF-Token")
    ):
        raise ApiError(403, "CSRF_FAILED", "CSRF 토큰이 없거나 올바르지 않습니다.")
    return row


def any_user(row: Annotated[AuthSession, Depends(current_session)]) -> User:
    """승인 대기 계정까지 허용(/api/auth/me, logout 용)."""
    return row.user


def active_user(user: Annotated[User, Depends(any_user)]) -> User:
    if user.account_status == "PENDING_APPROVAL":
        raise ApiError(403, "ACCOUNT_PENDING", "관리자 승인 대기 중입니다.")
    if user.account_status != "ACTIVE":
        raise ApiError(403, "ACCOUNT_INACTIVE", "사용할 수 없는 계정입니다.")
    if user.must_change_password:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "비밀번호를 먼저 변경해야 합니다.")
    return user


def admin_user(user: Annotated[User, Depends(active_user)]) -> User:
    if not user.is_admin:
        raise ApiError(403, "FORBIDDEN", "관리자 권한이 필요합니다.")
    return user


CurrentSession = Annotated[AuthSession, Depends(current_session)]
AnyUser = Annotated[User, Depends(any_user)]
ActiveUser = Annotated[User, Depends(active_user)]
AdminUser = Annotated[User, Depends(admin_user)]
DB = Annotated[Session, Depends(get_db)]
