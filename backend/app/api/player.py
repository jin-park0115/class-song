from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator

from app.auth.deps import DB, ActiveUser, AdminUser
from app.errors import ApiError
from app.services import audit
from app.services.player import PlayerController, PlayerError
from app.services.settings import get_max_volume

router = APIRouter(prefix="/api/player", tags=["player"])


def get_player(request: Request) -> PlayerController:
    return request.app.state.player


Player = Annotated[PlayerController, Depends(get_player)]

# resume_schedule 은 스케줄러가 생기는 STEP 03에서 추가한다.
Action = Literal["play", "pause", "resume", "stop", "next", "volume", "mute", "unmute"]


class ControlIn(BaseModel):
    action: Action
    value: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _volume_needs_value(self) -> "ControlIn":
        if self.action == "volume" and self.value is None:
            raise ValueError("volume 동작에는 0–100 사이의 value 가 필요합니다.")
        return self


def _status(player: PlayerController, db: DB) -> dict:
    return {**player.status(), "max_volume": get_max_volume(db)}


@router.get("/status")
def status(_: ActiveUser, player: Player, db: DB) -> dict:
    return _status(player, db)


@router.post("/control")
def control(body: ControlIn, admin: AdminUser, player: Player, db: DB) -> dict:
    max_volume = get_max_volume(db)
    result: dict = {"ok": True}
    payload: dict = {"action": body.action}
    try:
        match body.action:
            case "play":
                player.play(max_volume)
            case "pause":
                player.pause()
            case "resume":
                player.resume(max_volume)
            case "stop":
                player.stop()
            case "next":
                player.next()
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
    return result | {"status": _status(player, db)}


@router.post("/emergency-stop")
def emergency_stop(admin: AdminUser, player: Player, db: DB) -> dict:
    try:
        player.emergency_stop()
    except PlayerError as exc:
        audit.record(db, admin.id, "player.emergency_stop", {"error": exc.code})
        db.commit()
        raise ApiError(exc.status, exc.code, exc.message) from exc
    audit.record(db, admin.id, "player.emergency_stop", {})
    db.commit()
    return {"ok": True, "status": _status(player, db)}
