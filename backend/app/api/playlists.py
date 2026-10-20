"""플레이리스트(FR-006, 요구사항 7.5)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.views import track_out
from app.auth.deps import DB, ActiveUser, AdminUser
from app.errors import ApiError
from app.models import Playlist, PlaylistItem, Schedule, Track
from app.services import audit

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


class PlaylistIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class ItemsIn(BaseModel):
    track_ids: list[int] = Field(max_length=500)


def playlist_out(p: Playlist, admin: bool = False, items: bool = True) -> dict:
    tracks = [i.track for i in p.items]
    # 편성 뒤에 곡이 사용 불가(BLOCKED) 등으로 바뀌면 재생 때 건너뛴다 → 경고로 알린다(4.3)
    unplayable = [t for t in tracks if t.media_status != "READY"]
    out = {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "is_active": p.is_active,
        "item_count": len(tracks),
        "total_seconds": sum(t.duration_seconds or 0 for t in tracks),
        "unknown_duration_count": sum(t.duration_seconds is None for t in tracks),
        "unplayable_count": len(unplayable),
    }
    if items:
        out["items"] = [{"position": i.position, "track": track_out(i.track, admin=admin)} for i in p.items]
    return out


def _playlist(db: DB, playlist_id: int) -> Playlist:
    p = db.get(Playlist, playlist_id)
    if p is None:
        raise ApiError(404, "NOT_FOUND", "플레이리스트를 찾을 수 없습니다.")
    return p


@router.get("")
def list_playlists(user: ActiveUser, db: DB, limit: int = 100, offset: int = 0) -> dict:
    total = db.scalar(select(func.count()).select_from(Playlist))
    rows = db.scalars(select(Playlist).order_by(Playlist.name, Playlist.id).limit(min(limit, 500)).offset(offset))
    return {"items": [playlist_out(p, admin=user.is_admin, items=False) for p in rows], "total": total}


@router.get("/{playlist_id}")
def get_playlist(playlist_id: int, user: ActiveUser, db: DB) -> dict:
    return playlist_out(_playlist(db, playlist_id), admin=user.is_admin)


@router.post("", status_code=201)
def create_playlist(body: PlaylistIn, admin: AdminUser, db: DB) -> dict:
    p = Playlist(name=body.name.strip(), description=(body.description or "").strip() or None,
                 is_active=body.is_active, created_by=admin.id)
    db.add(p)
    db.flush()
    audit.record(db, admin.id, "playlists.create", {"playlist_id": p.id, "name": p.name})
    db.commit()
    return playlist_out(p, admin=True)


@router.patch("/{playlist_id}")
def update_playlist(playlist_id: int, body: PlaylistPatch, admin: AdminUser, db: DB) -> dict:
    p = _playlist(db, playlist_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is not None:
        p.name = changes["name"].strip()
    if "description" in changes:
        p.description = (changes["description"] or "").strip() or None
    if changes.get("is_active") is not None:
        p.is_active = changes["is_active"]
    audit.record(db, admin.id, "playlists.update", {"playlist_id": p.id, **changes})
    db.commit()
    return playlist_out(p, admin=True)


@router.delete("/{playlist_id}")
def delete_playlist(playlist_id: int, admin: AdminUser, db: DB) -> dict:
    p = _playlist(db, playlist_id)
    using = db.scalars(select(Schedule).where(Schedule.playlist_id == p.id)).all()
    if using:
        names = ", ".join(s.name for s in using)
        raise ApiError(
            409, "PLAYLIST_IN_USE", f"스케줄({names})이 쓰고 있어 지울 수 없어요. 스케줄에서 먼저 바꾸세요.",
            schedules=[{"id": s.id, "name": s.name} for s in using],
        )
    db.delete(p)
    audit.record(db, admin.id, "playlists.delete", {"playlist_id": playlist_id, "name": p.name})
    db.commit()
    return {"ok": True}


@router.put("/{playlist_id}/items")
def replace_items(playlist_id: int, body: ItemsIn, admin: AdminUser, db: DB) -> dict:
    """전체 순서를 한 트랜잭션으로 저장한다. READY 가 아닌 곡이 하나라도 있으면 422(FR-006)."""
    p = _playlist(db, playlist_id)
    tracks = {t.id: t for t in db.scalars(select(Track).where(Track.id.in_(body.track_ids)))}
    missing = [tid for tid in body.track_ids if tid not in tracks]
    if missing:
        raise ApiError(422, "TRACK_NOT_FOUND", "없는 곡이 들어 있어요.", track_ids=missing)
    not_ready = [tracks[tid] for tid in dict.fromkeys(body.track_ids) if tracks[tid].media_status != "READY"]
    if not_ready:
        titles = ", ".join(f"'{t.title}'" for t in not_ready[:3])
        raise ApiError(
            422, "TRACK_NOT_READY", f"재생 가능(READY)한 곡만 넣을 수 있어요: {titles}",
            track_ids=[t.id for t in not_ready],
        )
    # 옛 항목 삭제와 새 항목 삽입 사이에 위치가 겹쳐도 UNIQUE 는 커밋 때 검사한다(DEFERRABLE)
    p.items = [PlaylistItem(track_id=tid, position=i) for i, tid in enumerate(body.track_ids)]
    audit.record(db, admin.id, "playlists.items", {"playlist_id": p.id, "track_ids": body.track_ids})
    db.commit()
    db.refresh(p)
    return playlist_out(p, admin=True)
