"""DB 테이블. 플레이리스트·스케줄 등은 STEP 03에서 추가한다."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ROLES = ("STUDENT", "ADMIN")
ACCOUNT_STATUSES = ("PENDING_APPROVAL", "ACTIVE", "REJECTED", "DISABLED")
MEDIA_STATUSES = ("NOT_AVAILABLE", "PENDING_ACQUISITION", "PROCESSING", "READY", "FAILED", "BLOCKED")
RIGHTS_STATUSES = ("UNKNOWN", "CONFIRMED", "DENIED")
RIGHTS_BASES = ("OWNED_FILE", "LICENSED", "CC_LICENSE", "PUBLIC_DOMAIN", "OTHER")
REQUEST_STATUSES = ("PENDING", "APPROVED", "REJECTED", "CANCELLED")
REJECT_REASONS = ("DUPLICATE", "INAPPROPRIATE", "NO_RIGHTS", "TOO_LONG", "OTHER")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", ROLES), name="ck_users_role"),
        CheckConstraint(_in("account_status", ACCOUNT_STATUSES), name="ck_users_account_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login_id: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(50))
    signup_note: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(16), default="STUDENT")
    account_status: Mapped[str] = mapped_column(String(20), default="PENDING_APPROVAL")
    status_changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_note: Mapped[str | None] = mapped_column(Text)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_agent: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship()


class ControlAuditLog(Base):
    __tablename__ = "control_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # CLI 실행처럼 웹 사용자가 없는 동작은 NULL
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[object] = mapped_column(JSONB)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Track(Base):
    """음원 고유 엔티티. 같은 YouTube 영상에 여러 신청이 들어와도 트랙은 하나다(요구사항 6장)."""

    __tablename__ = "tracks"
    __table_args__ = (
        CheckConstraint(_in("media_status", MEDIA_STATUSES), name="ck_tracks_media_status"),
        CheckConstraint(_in("rights_status", RIGHTS_STATUSES), name="ck_tracks_rights_status"),
        CheckConstraint(
            f"rights_basis IS NULL OR {_in('rights_basis', RIGHTS_BASES)}", name="ck_tracks_rights_basis"
        ),
        # 권리가 확인되지 않은 곡은 재생 가능 상태가 될 수 없다(요구사항 4.3)
        CheckConstraint(
            "media_status <> 'READY' OR rights_status = 'CONFIRMED'", name="ck_tracks_ready_needs_rights"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    youtube_video_id: Mapped[str | None] = mapped_column(String(11), unique=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(300))
    artist_or_channel: Mapped[str | None] = mapped_column(String(200))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    media_status: Mapped[str] = mapped_column(String(24), default="NOT_AVAILABLE")
    media_path: Mapped[str | None] = mapped_column(Text)  # music_directory 기준 상대 경로
    sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    rights_status: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    rights_basis: Mapped[str | None] = mapped_column(String(20))
    rights_note: Mapped[str | None] = mapped_column(Text)
    rights_checked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rights_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SongRequest(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint(_in("status", REQUEST_STATUSES), name="ck_requests_status"),
        CheckConstraint(
            f"reject_reason IS NULL OR {_in('reject_reason', REJECT_REASONS)}", name="ck_requests_reject_reason"
        ),
        # 같은 학생이 같은 곡을 진행 중(대기/승인)으로 두 번 신청할 수 없다
        Index(
            "uq_requests_user_track_active",
            "user_id",
            "track_id",
            unique=True,
            postgresql_where="status IN ('PENDING', 'APPROVED')",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"), index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(String(16))
    reject_note: Mapped[str | None] = mapped_column(Text)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    track: Mapped[Track] = relationship()
