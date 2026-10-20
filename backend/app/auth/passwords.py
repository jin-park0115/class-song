import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id

LOGIN_ID_RE = re.compile(r"^[a-z0-9_]{4,20}$")
MIN_PASSWORD_LENGTH = 10


def validate_login_id(login_id: str) -> str | None:
    if not LOGIN_ID_RE.fullmatch(login_id):
        return "아이디는 4–20자의 영소문자·숫자·_ 만 사용할 수 있습니다."
    return None


def validate_password(login_id: str, password: str) -> str | None:
    """규칙 위반 시 사유 문자열, 통과 시 None (요구사항 2.1)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"비밀번호는 최소 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다."
    if login_id and login_id.lower() in password.lower():
        return "비밀번호에 아이디를 포함할 수 없습니다."
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# 존재하지 않는 아이디로 로그인할 때도 해시 검증 시간을 소모해 계정 존재 여부 노출을 줄인다.
DUMMY_HASH = _hasher.hash("dummy-password-for-timing")
