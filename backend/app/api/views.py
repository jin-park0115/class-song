"""API 응답 형태. 여러 라우터가 같은 모양을 쓰도록 한곳에 둔다."""

from app.models import SongRequest, Track, User


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def track_out(t: Track, admin: bool = False) -> dict:
    out = {
        "id": t.id,
        "youtube_video_id": t.youtube_video_id,
        "source_url": t.source_url,
        "title": t.title,
        "artist_or_channel": t.artist_or_channel,
        "duration_seconds": t.duration_seconds,
        "thumbnail_url": t.thumbnail_url,
        "media_status": t.media_status,
        "rights_status": t.rights_status,
    }
    if admin:
        out |= {
            "rights_basis": t.rights_basis,
            "rights_note": t.rights_note,
            "rights_checked_at": _iso(t.rights_checked_at),
            "has_media": bool(t.media_path),
        }
    return out


def request_out(r: SongRequest, requester: User | None = None) -> dict:
    out = {
        "id": r.id,
        "status": r.status,
        "comment": r.comment,
        "reject_reason": r.reject_reason,
        "reject_note": r.reject_note,
        "created_at": _iso(r.created_at),
        "reviewed_at": _iso(r.reviewed_at),
        "track": track_out(r.track),
    }
    if requester is not None:
        out["requester"] = {"id": requester.id, "login_id": requester.login_id, "name": requester.name}
    return out
