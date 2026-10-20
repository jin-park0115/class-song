from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.auth.deps import DB, ActiveUser, AdminUser
from app.config import get_settings
from app.errors import ApiError
from app.models import Track
from app.services import audit
from app.services.player import PlayerController, PlayerError
from app.services.scheduler import ScheduleRunner
from app.services.settings import get_max_volume

router = APIRouter(prefix="/api/player", tags=["player"])


def get_player(request: Request) -> PlayerController:
    return request.app.state.player


Player = Annotated[PlayerController, Depends(get_player)]


def get_runner(request: Request) -> ScheduleRunner:
    return request.app.state.runner


Runner = Annotated[ScheduleRunner, Depends(get_runner)]

Action = Literal[
    "play", "play_at", "pause", "resume", "stop", "next", "previous", "volume", "mute", "unmute",
    "resume_schedule",
]


class ControlIn(BaseModel):
    action: Action
    value: int | None = Field(default=None, ge=0, le=100)
    position: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _required_fields(self) -> "ControlIn":
        if self.action == "volume" and self.value is None:
            raise ValueError("volume 동작에는 0–100 사이의 value 가 필요합니다.")
        if self.action == "play_at" and self.position is None:
            raise ValueError("play_at 동작에는 대기열 위치 position 이 필요합니다.")
        return self


def _enrich(db: DB, songs: list[dict | None]) -> None:
    """MPD 파일 경로(<track-id>.<ext>)로 DB 곡 정보를 찾아 제목·아티스트·썸네일을 채운다."""
    files = {s["file"] for s in songs if s and s.get("file")}
    if not files:
        return
    tracks = {t.media_path: t for t in db.scalars(select(Track).where(Track.media_path.in_(files)))}
    for song in songs:
        t = tracks.get(song["file"]) if song else None
        if t:
            song |= {
                "title": t.title,
                "artist": t.artist_or_channel or song.get("artist"),
                "thumbnail_url": t.thumbnail_url,
                "track_id": t.id,
            }


def _status(player: PlayerController, runner: ScheduleRunner, db: DB) -> dict:
    st = player.status()
    _enrich(db, [st["current"], st["next"]])
    # schedule.cause: 재생 원인(SCHEDULE 예약 / MANUAL 수동), current·next: 지금·다음 예약 구간
    return {**st, "max_volume": get_max_volume(db), "schedule": runner.info(db, st["state"])}


@router.get("/status")
def status(_: ActiveUser, player: Player, runner: Runner, db: DB) -> dict:
    return _status(player, runner, db)


@router.get("/queue")
def queue(_: ActiveUser, player: Player, db: DB) -> dict:
    try:
        items = player.queue()
    except PlayerError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    _enrich(db, items)
    return {"items": items}


@router.post("/control")
def control(body: ControlIn, admin: AdminUser, player: Player, runner: Runner, db: DB) -> dict:
    max_volume = get_max_volume(db)
    result: dict = {"ok": True}
    payload: dict = {"action": body.action}
    try:
        match body.action:
            case "play":
                player.play(max_volume)
            case "play_at":
                player.play_at(body.position, max_volume)
                payload |= {"position": body.position}
            case "pause":
                player.pause()
            case "resume":
                player.resume(max_volume)
            case "stop":
                player.stop()
                runner.manual_stop()  # 예약 구간 중이면 그 구간은 정지 유지(10장)
            case "resume_schedule":
                runner.resume_schedule()
            case "next":
                player.next(max_volume)
            case "previous":
                player.previous(max_volume)
            case "mute":
                player.mute()
            case "volume":
                vol = player.set_volume(body.value, max_volume)
                result |= {"applied_volume": vol.applied, "clamped": vol.clamped}
                payload |= {"requested": vol.requested, "applied": vol.applied}
            case "unmute":
                vol = player.unmute(max_volume)
                if vol is not None:
                    result |= {"applied_volume": vol.applied, "clamped": vol.clamped}
                    payload |= {"applied": vol.applied}
    except PlayerError as exc:
        audit.record(db, admin.id, f"player.{body.action}", payload | {"error": exc.code})
        db.commit()
        raise ApiError(exc.status, exc.code, exc.message) from exc

    audit.record(db, admin.id, f"player.{body.action}", payload)
    db.commit()
    return result | {"status": _status(player, runner, db)}


@router.post("/emergency-stop")
def emergency_stop(admin: AdminUser, player: Player, runner: Runner, db: DB) -> dict:
    """페이드 없이 즉시 정지 + 현재 예약 구간은 자동으로 다시 틀지 않는다."""
    try:
        player.emergency_stop()
        runner.manual_stop(emergency=True)
    except PlayerError as exc:
        audit.record(db, admin.id, "player.emergency_stop", {"error": exc.code})
        db.commit()
        raise ApiError(exc.status, exc.code, exc.message) from exc
    audit.record(db, admin.id, "player.emergency_stop", {})
    db.commit()
    return {"ok": True, "status": _status(player, runner, db)}


@router.get("/monitor")
async def monitor(_: AdminUser) -> StreamingResponse:
    """MPD httpd 출력을 관리자 브라우저로 전달한다(노트북·원격 확인용 모니터 스피커)."""
    upstream = get_settings().mpd_monitor_url
    if not upstream:
        raise ApiError(404, "MONITOR_DISABLED", "모니터 스트림이 설정되지 않았습니다.")
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    try:
        res = await client.send(client.build_request("GET", upstream), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise ApiError(503, "MONITOR_UNAVAILABLE", "모니터 스트림에 연결할 수 없습니다. (재생 중일 때만 열립니다)") from exc

    async def close() -> None:
        await res.aclose()
        await client.aclose()

    return StreamingResponse(
        res.aiter_raw(),
        media_type=res.headers.get("content-type", "audio/mpeg"),
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(close),
    )
