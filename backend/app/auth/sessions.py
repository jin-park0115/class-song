import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuthSession, User


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _ttl(user: User) -> timedelta:
    s = get_settings()
    hours = s.session_ttl_admin_hours if user.is_admin else s.session_ttl_student_hours
    return timedelta(hours=hours)


def csrf_for(token: str) -> str:
    """CSRF 토큰은 세션 토큰에서 파생한다. 탭을 여러 개 열어도 같은 값을 쓰고, 쿠키를 읽을 수 없는 다른 사이트는 계산할 수 없다."""
    return hashlib.sha256(b"csrf:" + token.encode()).hexdigest()


def create_session(db: Session, user: User, user_agent: str | None) -> tuple[str, str]:
    """세션을 만들고 (세션 토큰, CSRF 토큰) 평문을 돌려준다. DB에는 해시만 저장한다."""
    token = secrets.token_urlsafe(32)
    csrf = csrf_for(token)
    now = datetime.now(UTC)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=_hash(token),
            csrf_token_hash=_hash(csrf),
            expires_at=now + _ttl(user),
            last_seen_at=now,
            user_agent=(user_agent or "")[:500],
        )
    )
    return token, csrf


def load_session(db: Session, token: str) -> AuthSession | None:
    """유효한 세션을 찾고 만료 시각을 마지막 활동 기준으로 연장한다."""
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == _hash(token)))
    if row is None:
        return None
    now = datetime.now(UTC)
    if row.expires_at <= now:
        db.delete(row)
        db.commit()
        return None
    row.last_seen_at = now
    row.expires_at = now + _ttl(row.user)
    db.commit()
    return row


def csrf_matches(row: AuthSession, csrf: str | None) -> bool:
    return bool(csrf) and secrets.compare_digest(row.csrf_token_hash, _hash(csrf))


def revoke_user_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
