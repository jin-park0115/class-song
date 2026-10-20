"""재생 스케줄(FR-007·015, 요구사항 4.1·7.5)."""

from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.auth.deps import DB, ActiveUser, AdminUser
from app.errors import ApiError
from app.models import Playlist, Schedule
from app.services import audit
from app.services.scheduler import KST, fmt_hm, next_window, overlaps, windows_between
from app.services.settings import get_max_volume

router = APIRouter(prefix="/api/schedules", tags=["schedules"])

WEEKDAY_KO = "월화수목금토일"


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: dtime
    end_time: dtime
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"  # MVP 고정(요구사항 4.1)
    playlist_id: int
    volume: int = Field(ge=0, le=100)  # 상한(max_volume)을 넘으면 저장은 하되 재생 때 상한으로 제한
    fade_in_seconds: int = Field(default=3, ge=0, le=30)
    fade_out_seconds: int = Field(default=3, ge=0, le=30)
    rejoin_policy: Literal["RESTART", "SKIP"] = "RESTART"
    playback_mode: Literal["SEQUENTIAL", "SHUFFLE"] = "SEQUENTIAL"
    on_playlist_end: Literal["REPEAT", "STOP"] = "REPEAT"
    is_enabled: bool = True

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 7 for d in v):
            raise ValueError("요일은 1(월)~7(일)이어야 합니다.")
        return sorted(set(v))

    @field_validator("start_time", "end_time")
    @classmethod
    def _minute(cls, v: dtime) -> dtime:
        return v.replace(second=0, microsecond=0, tzinfo=None)

    @model_validator(mode="after")
    def _order(self) -> "ScheduleIn":
        if self.start_time >= self.end_time:
            raise ValueError("시작 시각이 종료 시각보다 빨라야 해요. 자정을 넘는 구간은 만들 수 없어요.")
        return self


class SchedulePatch(BaseModel):
    """바꿀 항목만 보낸다. 합친 결과를 ScheduleIn 규칙으로 다시 검사한다."""

    name: str | None = None
    weekdays: list[int] | None = None
    start_time: dtime | None = None
    end_time: dtime | None = None
    playlist_id: int | None = None
    volume: int | None = None
    fade_in_seconds: int | None = None
    fade_out_seconds: int | None = None
    rejoin_policy: str | None = None
    playback_mode: str | None = None
    on_playlist_end: str | None = None
    is_enabled: bool | None = None


FIELDS = list(ScheduleIn.model_fields)


def _when(s: Schedule) -> str:
    days = "".join(WEEKDAY_KO[d - 1] for d in s.weekdays)
    return f"{days} {fmt_hm(s.start_time)}–{fmt_hm(s.end_time)}"


def schedule_out(s: Schedule, max_volume: int) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "weekdays": list(s.weekdays),
        "start_time": fmt_hm(s.start_time),
        "end_time": fmt_hm(s.end_time),
        "timezone": s.timezone,
        "when": _when(s),
        "playlist_id": s.playlist_id,
        "playlist_name": s.playlist.name if s.playlist else None,
        "volume": s.volume,
        "effective_volume": min(s.volume, max_volume),
        "volume_clamped": s.volume > max_volume,
        "fade_in_seconds": s.fade_in_seconds,
        "fade_out_seconds": s.fade_out_seconds,
        "rejoin_policy": s.rejoin_policy,
        "playback_mode": s.playback_mode,
        "on_playlist_end": s.on_playlist_end,
        "is_enabled": s.is_enabled,
    }


def _check(db: DB, data: ScheduleIn, exclude_id: int | None) -> None:
    if db.get(Playlist, data.playlist_id) is None:
        raise ApiError(422, "PLAYLIST_NOT_FOUND", "플레이리스트를 찾을 수 없습니다.")
    if not data.is_enabled:
        return
    # 출력 장치가 하나라 활성 스케줄끼리는 시간이 겹치면 안 된다(4.1)
    candidate = Schedule(**data.model_dump())
    others = db.scalars(select(Schedule).where(Schedule.is_enabled, Schedule.id != (exclude_id or 0)))
    conflicts = [s for s in others if overlaps(candidate, s)]
    if conflicts:
        max_volume = get_max_volume(db)
        names = ", ".join(f"'{s.name}'({_when(s)})" for s in conflicts)
        raise ApiError(
            409, "SCHEDULE_CONFLICT", f"다른 스케줄과 시간이 겹쳐요: {names}",
            conflicts=[schedule_out(s, max_volume) for s in conflicts],
        )


def _schedule(db: DB, schedule_id: int) -> Schedule:
    s = db.get(Schedule, schedule_id)
    if s is None:
        raise ApiError(404, "NOT_FOUND", "스케줄을 찾을 수 없습니다.")
    return s


@router.get("")
def list_schedules(_: ActiveUser, db: DB, limit: int = 100, offset: int = 0) -> dict:
    total = db.scalar(select(func.count()).select_from(Schedule))
    rows = db.scalars(select(Schedule).order_by(Schedule.start_time, Schedule.id).limit(min(limit, 500)).offset(offset))
    max_volume = get_max_volume(db)
    return {"items": [schedule_out(s, max_volume) for s in rows], "total": total}


@router.get("/upcoming")
def upcoming(_: ActiveUser, db: DB, request: Request) -> dict:
    """오늘·이번 주(월~일, Asia/Seoul) 재생 구간과 다음 재생 시각(FR-015)."""
    now: datetime = request.app.state.runner.clock()
    today = now.astimezone(KST).date()
    monday = today - timedelta(days=today.isoweekday() - 1)
    week_start = datetime.combine(monday, dtime(0), KST)
    schedules = list(db.scalars(select(Schedule).where(Schedule.is_enabled)))
    by_id = {s.id: s for s in schedules}

    def out(w) -> dict:
        s = by_id[w.schedule_id]
        return {
            "schedule_id": w.schedule_id,
            "name": w.name,
            "playlist_name": s.playlist.name,
            "date": w.start.date().isoformat(),
            "weekday": w.start.isoweekday(),
            "start": w.start.isoformat(),
            "end": w.end.isoformat(),
            "start_time": fmt_hm(s.start_time),
            "end_time": fmt_hm(s.end_time),
            "status": "done" if w.end <= now else "now" if w.start <= now else "upcoming",
        }

    week = [out(w) for w in windows_between(schedules, week_start, week_start + timedelta(days=7))]
    nxt = next_window(schedules, now)
    return {
        "now": now.isoformat(),
        "today": [w for w in week if w["date"] == today.isoformat()],
        "week": week,
        "next": out(nxt) if nxt else None,
    }


@router.post("", status_code=201)
def create_schedule(body: ScheduleIn, admin: AdminUser, db: DB) -> dict:
    _check(db, body, None)
    s = Schedule(**body.model_dump())
    db.add(s)
    db.flush()
    audit.record(db, admin.id, "schedules.create", {"schedule_id": s.id, **body.model_dump(mode="json")})
    db.commit()
    db.refresh(s)
    return schedule_out(s, get_max_volume(db))


@router.patch("/{schedule_id}")
def update_schedule(schedule_id: int, body: SchedulePatch, admin: AdminUser, db: DB) -> dict:
    s = _schedule(db, schedule_id)
    current = {f: getattr(s, f) for f in FIELDS}
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    try:
        merged = ScheduleIn(**(current | changes))
    except ValueError as exc:  # pydantic ValidationError
        raise ApiError(422, "VALIDATION_FAILED", _first_error(exc)) from None
    _check(db, merged, s.id)
    for f, v in merged.model_dump().items():
        setattr(s, f, v)
    audit.record(db, admin.id, "schedules.update", {"schedule_id": s.id, **body.model_dump(mode="json", exclude_unset=True)})
    db.commit()
    db.refresh(s)
    return schedule_out(s, get_max_volume(db))


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, admin: AdminUser, db: DB) -> dict:
    s = _schedule(db, schedule_id)
    db.delete(s)
    audit.record(db, admin.id, "schedules.delete", {"schedule_id": schedule_id, "name": s.name})
    db.commit()
    return {"ok": True}


def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        first = errors()[0]
        return str(first.get("msg", "입력값이 올바르지 않습니다.")).removeprefix("Value error, ")
    return str(exc)
