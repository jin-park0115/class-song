"""곡 신청(FR-002~004). 승인하면 YouTube에서 오디오를 받아(mp3) 재생 대기열 끝에 넣는다."""

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, FastAPI, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.player import Player
from app.api.views import request_out, track_out
from app.auth.deps import DB, ActiveUser, AdminUser
from app.config import get_settings
from app.db import SessionLocal
from app.errors import ApiError
from app.models import REJECT_REASONS, SongRequest, Track, User
from app.services import audit, media, youtube
from app.services.player import PlayerError
from app.services.settings import get_int_setting

log = logging.getLogger("jukebox.requests")
router = APIRouter(tags=["requests"])

ACTIVE = ("PENDING", "APPROVED")


class UrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)


class RequestIn(UrlIn):
    comment: str | None = Field(default=None, max_length=300)
    # 메타데이터 조회가 안 될 때(인터넷 없음 등) 학생이 직접 적는 곡명
    title: str | None = Field(default=None, max_length=300)


class ReviewIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    reject_reason: Literal[REJECT_REASONS] | None = None  # type: ignore[valid-type]
    reject_note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _reason(self) -> "ReviewIn":
        if self.decision == "REJECT":
            if not self.reject_reason:
                raise ValueError("반려할 때는 reject_reason 이 필요합니다.")
            if self.reject_reason == "OTHER" and not (self.reject_note or "").strip():
                raise ValueError("기타 사유는 reject_note 를 적어야 합니다.")
        return self


def _video_id(url: str) -> str:
    video_id = youtube.parse_video_id(url)
    if not video_id:
        raise ApiError(422, "INVALID_YOUTUBE_URL", "YouTube 영상 주소가 아닙니다. (예: https://www.youtube.com/watch?v=...)")
    return video_id


def _active_request(db: DB, user_id: int, track_id: int) -> SongRequest | None:
    return db.scalar(
        select(SongRequest).where(
            SongRequest.user_id == user_id, SongRequest.track_id == track_id, SongRequest.status.in_(ACTIVE)
        )
    )


def _warnings(db: DB, duration: int | None) -> list[str]:
    limit = get_int_setting(db, "max_track_duration_seconds", 600)
    if duration and duration > limit:
        return [f"곡 길이가 {limit // 60}분을 넘습니다. 관리자 승인 시 확인합니다."]
    return []


@router.post("/api/youtube/preview")
def preview(body: UrlIn, user: ActiveUser, request: Request, db: DB) -> dict:
    """신청 전에 메타데이터와 중복 여부를 보여준다."""
    video_id = _video_id(body.url)
    track = db.scalar(select(Track).where(Track.youtube_video_id == video_id))
    if track:
        meta = youtube.VideoMeta(track.title, track.artist_or_channel, track.thumbnail_url)
        found = True
    else:
        meta = request.app.state.metadata_fetcher(video_id)
        found = meta is not None
    mine = _active_request(db, user.id, track.id) if track else None
    return {
        "video_id": video_id,
        "source_url": youtube.canonical_url(video_id),
        "metadata_found": found,
        "title": meta.title if meta else None,
        "channel": meta.channel if meta else None,
        "thumbnail_url": (meta.thumbnail_url if meta else None) or youtube.thumbnail_for(video_id),
        "track": track_out(track) if track else None,
        "my_request": request_out(mine) if mine else None,
        "blocked": bool(track and track.media_status == "BLOCKED"),
        "warnings": _warnings(db, track.duration_seconds if track else None),
    }


def _get_or_create_track(db: DB, request: Request, video_id: str, manual_title: str | None) -> Track:
    track = db.scalar(select(Track).where(Track.youtube_video_id == video_id))
    if track:
        return track
    meta = request.app.state.metadata_fetcher(video_id)
    title = (meta.title if meta else None) or (manual_title or "").strip()
    if not title:
        raise ApiError(422, "TITLE_REQUIRED", "곡 정보를 불러오지 못했습니다. 곡명을 직접 입력하세요.")
    track = Track(
        youtube_video_id=video_id,
        source_url=youtube.canonical_url(video_id),
        title=title,
        artist_or_channel=meta.channel if meta else None,
        thumbnail_url=(meta.thumbnail_url if meta else None) or youtube.thumbnail_for(video_id),
    )
    try:
        with db.begin_nested():
            db.add(track)
    except IntegrityError:  # 다른 학생이 같은 순간에 같은 영상을 신청
        return db.scalar(select(Track).where(Track.youtube_video_id == video_id))
    return track


@router.post("/api/requests", status_code=201)
def create_request(body: RequestIn, user: ActiveUser, request: Request, db: DB) -> dict:
    video_id = _video_id(body.url)

    pending = db.scalar(
        select(func.count())
        .select_from(SongRequest)
        .where(SongRequest.user_id == user.id, SongRequest.status == "PENDING")
    )
    limit = get_int_setting(db, "max_pending_requests_per_user", 5)
    if pending >= limit:
        raise ApiError(409, "REQUEST_LIMIT", f"승인 대기 중인 신청은 최대 {limit}건입니다. 결과가 나온 뒤 다시 신청하세요.")

    track = _get_or_create_track(db, request, video_id, body.title)
    if track.media_status == "BLOCKED":
        raise ApiError(409, "TRACK_BLOCKED", "강의실에서 재생할 수 없는 곡으로 지정되어 있습니다.")
    if _active_request(db, user.id, track.id):
        raise ApiError(409, "DUPLICATE_REQUEST", "이미 신청한 곡입니다.")

    req = SongRequest(user_id=user.id, track=track, comment=(body.comment or "").strip() or None)
    db.add(req)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "DUPLICATE_REQUEST", "이미 신청한 곡입니다.") from None
    db.commit()
    return request_out(req) | {"warnings": _warnings(db, track.duration_seconds)}


@router.get("/api/requests/me")
def my_requests(user: ActiveUser, db: DB) -> dict:
    rows = db.scalars(
        select(SongRequest).where(SongRequest.user_id == user.id).order_by(SongRequest.created_at.desc())
    ).all()
    limit = get_int_setting(db, "max_pending_requests_per_user", 5)
    pending = sum(r.status == "PENDING" for r in rows)
    return {"items": [request_out(r) for r in rows], "pending": pending, "pending_limit": limit}


@router.delete("/api/requests/{request_id}")
def cancel_request(request_id: int, user: ActiveUser, db: DB) -> dict:
    req = db.get(SongRequest, request_id)
    if req is None or req.user_id != user.id:
        raise ApiError(404, "NOT_FOUND", "신청을 찾을 수 없습니다.")
    if req.status != "PENDING":
        raise ApiError(409, "NOT_CANCELLABLE", "승인 대기 중인 신청만 취소할 수 있습니다.")
    req.status = "CANCELLED"
    req.cancelled_at = datetime.now(UTC)
    db.commit()
    return request_out(req)


@router.get("/api/admin/requests")
def admin_requests(
    _: AdminUser,
    db: DB,
    status: Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"] | None = "PENDING",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    q = select(SongRequest, User).join(User, SongRequest.user_id == User.id)
    if status:
        q = q.where(SongRequest.status == status)
    q = q.order_by(SongRequest.created_at.asc() if status == "PENDING" else SongRequest.created_at.desc())
    rows = db.execute(q.limit(min(limit, 500)).offset(offset)).all()
    # 같은 곡을 신청한 사람 수(중복 신청 통합 판단용)
    counts = dict(
        db.execute(
            select(SongRequest.track_id, func.count())
            .where(SongRequest.status.in_(ACTIVE))
            .group_by(SongRequest.track_id)
        ).all()
    )
    return {
        "items": [
            request_out(r, requester=u) | {"same_track_requests": counts.get(r.track_id, 0)} for r, u in rows
        ]
    }


def _queue(app: FastAPI, track: Track) -> None:
    """대기열 끝에 넣는다. MPD가 꺼져 있어도 승인·다운로드는 유지하고 라이브러리에서 다시 넣으면 된다."""
    try:
        app.state.player.add_to_queue(track.media_path)
    except PlayerError as exc:
        log.warning("queue add failed track_id=%s code=%s", track.id, exc.code)


def _acquire(app: FastAPI, track_id: int, admin_id: int) -> None:
    """(백그라운드) YouTube에서 오디오를 받아 mp3로 저장 → READY → 대기열 끝에 추가. 실패하면 FAILED."""
    media_dir = Path(get_settings().media_dir)
    with SessionLocal() as db:
        track = db.get(Track, track_id)
        try:
            tmp = app.state.downloader(track.youtube_video_id, media_dir)
            try:
                duration = media.probe(tmp, "mp3")
                with tmp.open("rb") as f:
                    digest = hashlib.file_digest(f, "sha256").hexdigest()
                relpath = media.finalize(tmp, media_dir, track.id, "mp3", track.media_path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            track.media_path = relpath
            track.sha256 = digest
            track.duration_seconds = track.duration_seconds or duration
            # READY 는 권리 확인이 필요하므로(DB 제약), 승인한 관리자를 확인자로 남긴다
            track.rights_status = "CONFIRMED"
            track.rights_basis = "OTHER"
            track.rights_note = "신청 승인 시 YouTube에서 자동으로 받음"
            track.rights_checked_by = admin_id
            track.rights_checked_at = datetime.now(UTC)
            track.media_status = "READY"
            audit.record(db, admin_id, "tracks.download", {"track_id": track.id, "path": relpath, "sha256": digest})
            db.commit()
        except Exception as exc:
            log.warning("download failed track_id=%s err=%s", track_id, exc)
            db.rollback()
            track = db.get(Track, track_id)
            track.media_status = "FAILED"  # 라이브러리에서 파일을 직접 올리면 READY 가 된다
            audit.record(db, admin_id, "tracks.download", {"track_id": track_id, "error": str(exc)[:300]})
            db.commit()
            return
        _queue(app, track)


@router.post("/api/admin/requests/{request_id}/review")
def review(
    request_id: int, body: ReviewIn, admin: AdminUser, db: DB, request: Request, tasks: BackgroundTasks
) -> dict:
    req = db.get(SongRequest, request_id)
    if req is None:
        raise ApiError(404, "NOT_FOUND", "신청을 찾을 수 없습니다.")
    if req.status != "PENDING":
        raise ApiError(409, "INVALID_TRANSITION", "승인 대기 중인 신청만 검토할 수 있습니다.")
    req.reviewed_by = admin.id
    req.reviewed_at = datetime.now(UTC)
    queue_now = False
    if body.decision == "APPROVE":
        req.status = "APPROVED"
        track = req.track
        if track.media_status == "READY":  # 이미 받아 둔 곡은 바로 대기열로
            queue_now = True
        elif track.media_status in ("NOT_AVAILABLE", "PENDING_ACQUISITION", "FAILED"):
            track.media_status = "PROCESSING"
            tasks.add_task(_acquire, request.app, track.id, admin.id)
    else:
        req.status = "REJECTED"
        req.reject_reason = body.reject_reason
        req.reject_note = (body.reject_note or "").strip() or None
    audit.record(
        db,
        admin.id,
        "requests.review",
        {"request_id": req.id, "decision": body.decision, "reason": body.reject_reason},
    )
    db.commit()
    if queue_now:
        _queue(request.app, req.track)
    return request_out(req, requester=req.user)


@router.post("/api/admin/tracks/{track_id}/download")
def redownload(track_id: int, admin: AdminUser, db: DB, request: Request, tasks: BackgroundTasks) -> dict:
    """받기에 실패했거나 멈춘 곡(서버 재시작 등)을 다시 받는다. 받으면 대기열 끝에 들어간다."""
    track = db.get(Track, track_id)
    if track is None:
        raise ApiError(404, "NOT_FOUND", "곡을 찾을 수 없습니다.")
    if track.media_status in ("READY", "BLOCKED") or not track.youtube_video_id:
        raise ApiError(409, "INVALID_TRANSITION", "다시 받을 수 없는 곡입니다.")
    # ponytail: 실제로 받는 중에 또 누르면 두 번 받는다(같은 파일로 덮어써서 결과는 같다)
    track.media_status = "PROCESSING"
    db.commit()
    tasks.add_task(_acquire, request.app, track.id, admin.id)
    return track_out(track, admin=True)
