"""곡 목록·음원 라이브러리(FR-005, 요구사항 4.2·4.3)와 재생 대기열 추가."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import exists, or_, select

from app.api.player import Player
from app.api.views import track_out
from app.auth.deps import DB, ActiveUser, AdminUser
from app.config import get_settings
from app.errors import ApiError
from app.models import RIGHTS_BASES, SongRequest, Track
from app.services import audit, media
from app.services.player import PlayerError

router = APIRouter(tags=["tracks"])


def _approved():
    return exists().where(SongRequest.track_id == Track.id, SongRequest.status == "APPROVED")


def _track(db: DB, track_id: int) -> Track:
    track = db.get(Track, track_id)
    if track is None:
        raise ApiError(404, "NOT_FOUND", "곡을 찾을 수 없습니다.")
    return track


@router.get("/api/tracks")
def list_tracks(
    _: ActiveUser, db: DB, status: Literal["approved", "ready"] = "approved", q: str | None = None
) -> dict:
    """승인된 곡(또는 재생 가능한 곡) 목록. 신청자는 노출하지 않는다."""
    stmt = select(Track).where(_approved()) if status == "approved" else select(Track).where(Track.media_status == "READY")
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Track.title.ilike(like), Track.artist_or_channel.ilike(like)))
    rows = db.scalars(stmt.order_by(Track.title).limit(500)).all()
    return {"items": [track_out(t) for t in rows]}


@router.get("/api/tracks/{track_id}")
def get_track(track_id: int, _: ActiveUser, db: DB) -> dict:
    return track_out(_track(db, track_id))


@router.get("/api/admin/tracks")
def admin_library(_: AdminUser, db: DB) -> dict:
    """음원 라이브러리: 승인된 신청이 있거나 파일이 있는 곡."""
    rows = db.scalars(
        select(Track).where(or_(_approved(), Track.media_path.is_not(None))).order_by(Track.created_at.desc())
    ).all()
    return {"items": [track_out(t, admin=True) for t in rows]}


class TrackPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    artist_or_channel: str | None = Field(default=None, max_length=200)
    duration_seconds: int | None = Field(default=None, ge=1, le=24 * 3600)
    rights_status: Literal["CONFIRMED", "DENIED"] | None = None
    rights_basis: Literal[RIGHTS_BASES] | None = None  # type: ignore[valid-type]
    rights_note: str | None = Field(default=None, max_length=500)


@router.patch("/api/admin/tracks/{track_id}")
def update_track(track_id: int, body: TrackPatch, admin: AdminUser, db: DB) -> dict:
    track = _track(db, track_id)
    changes = body.model_dump(exclude_unset=True)
    for key in ("title", "artist_or_channel", "duration_seconds"):
        if key in changes:
            setattr(track, key, changes[key])

    if "rights_note" in changes:
        track.rights_note = (changes["rights_note"] or "").strip() or None
    if "rights_basis" in changes:
        track.rights_basis = changes["rights_basis"]

    if body.rights_status:
        if body.rights_status == "CONFIRMED":
            if not track.rights_basis:
                raise ApiError(422, "RIGHTS_BASIS_REQUIRED", "권리 확인 근거(rights_basis)를 선택하세요.")
            if track.rights_basis == "OTHER" and not track.rights_note:
                raise ApiError(422, "RIGHTS_NOTE_REQUIRED", "근거가 '기타'이면 메모를 적어야 합니다.")
            track.rights_status = "CONFIRMED"
            if track.media_status == "BLOCKED":  # 차단 해제
                track.media_status = "READY" if track.media_path else "PENDING_ACQUISITION"
        else:
            track.rights_status = "DENIED"
            track.media_status = "BLOCKED"  # 편성·재생 대상에서 제외(요구사항 4.3)
        track.rights_checked_by = admin.id
        track.rights_checked_at = datetime.now(UTC)

    audit.record(db, admin.id, "tracks.update", {"track_id": track.id, **{k: v for k, v in changes.items()}})
    db.commit()
    return track_out(track, admin=True)


@router.post("/api/admin/tracks/{track_id}/media")
def upload_media(track_id: int, file: UploadFile, admin: AdminUser, db: DB) -> dict:
    """이용 권리가 확인된 곡에 음원 파일을 등록한다 → READY."""
    settings = get_settings()
    track = _track(db, track_id)
    if track.rights_status != "CONFIRMED":
        raise ApiError(409, "RIGHTS_NOT_CONFIRMED", "이용 권리를 먼저 확인(CONFIRMED)해야 음원을 등록할 수 있습니다.")

    ext = media.extension_of(file.filename)
    media_dir = Path(settings.media_dir)
    tmp, digest = media.receive(file.file, media_dir, settings.max_upload_mb * 1024 * 1024)
    try:
        duration = media.probe(tmp, ext)
        other = db.scalar(select(Track).where(Track.sha256 == digest, Track.id != track.id))
        if other:
            raise ApiError(409, "DUPLICATE_FILE", f"이미 다른 곡('{other.title}')에 등록된 파일입니다.")
        relpath = media.finalize(tmp, media_dir, track.id, ext, track.media_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    track.media_path = relpath
    track.sha256 = digest
    track.duration_seconds = track.duration_seconds or duration
    track.media_status = "READY"
    audit.record(db, admin.id, "tracks.media_upload", {"track_id": track.id, "path": relpath, "sha256": digest})
    db.commit()
    return track_out(track, admin=True)


class QueueIn(BaseModel):
    track_id: int


@router.post("/api/admin/queue")
def add_to_queue(body: QueueIn, admin: AdminUser, player: Player, db: DB) -> dict:
    """재생 가능한(READY) 곡을 MPD 대기열 끝에 추가한다. 플레이리스트 편성은 STEP 03에서."""
    track = _track(db, body.track_id)
    if track.media_status != "READY" or not track.media_path:
        raise ApiError(409, "TRACK_NOT_READY", "재생 가능한(READY) 곡만 대기열에 넣을 수 있습니다.")
    try:
        player.add_to_queue(track.media_path)
    except PlayerError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    audit.record(db, admin.id, "queue.add", {"track_id": track.id})
    db.commit()
    return {"ok": True}


@router.delete("/api/admin/queue/{position}")
def remove_from_queue(position: int, admin: AdminUser, player: Player, db: DB) -> dict:
    try:
        player.remove_at(position)
    except PlayerError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    audit.record(db, admin.id, "queue.remove", {"position": position})
    db.commit()
    return {"ok": True}
