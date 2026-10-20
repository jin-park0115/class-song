"""자동 스케줄 실행(FR-007·011·019, 요구사항 4.1·10장).

ScheduleRunner.tick(now) 가 "지금 어느 예약 구간인가"를 보고 구간이 바뀔 때만 행동한다.
  - 구간 시작: 플레이리스트를 대기열에 올리고 볼륨 0 → 스케줄 볼륨(상한으로 제한)까지 페이드 인
  - 구간 종료: 수동 조작 여부와 관계없이 항상 페이드 아웃 후 정지
  - 앱을 켠 직후 첫 tick 이 구간 한가운데면 rejoin_policy(재합류) 적용
  - 구간 중에는 아무것도 다시 시작하지 않는다 → 수동 정지하면 그 구간은 정지 유지(resume_schedule 로 재개)
  - 구간 밖 수동 재생은 manual_play_auto_stop_minutes 뒤 자동 정지
시각은 밖에서 넣어 준다(tick(now), clock). 테스트는 고정 시각으로 검증한다.
MPD 명령은 PlayerController 를 통해서만 보낸다.
"""

import logging
import random
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import PlaybackEvent, Playlist, PlaylistItem, Schedule
from app.services.player import PlayerController, PlayerError
from app.services.settings import get_int_setting, get_max_volume

log = logging.getLogger("jukebox.scheduler")

KST = ZoneInfo("Asia/Seoul")
# 앱을 켠 첫 tick 이 구간 시작 뒤 이 시간 안이면 "정시 시작"으로 본다(그보다 늦으면 재합류 규칙).
START_GRACE = timedelta(seconds=30)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Window:
    """한 스케줄이 특정 날짜에 실제로 차지하는 구간 [start, end)."""

    schedule_id: int
    name: str
    start: datetime
    end: datetime

    @property
    def key(self) -> tuple[int, datetime]:
        return (self.schedule_id, self.start)


def windows_between(schedules: Iterable[Schedule], start: datetime, end: datetime) -> list[Window]:
    """[start, end) 와 겹치는 모든 구간을 시작 시각 순으로 돌려준다."""
    out = []
    for s in schedules:
        tz = ZoneInfo(s.timezone)
        day = start.astimezone(tz).date()
        last = end.astimezone(tz).date()
        while day <= last:
            if day.isoweekday() in s.weekdays:
                ws = datetime.combine(day, s.start_time, tz)
                we = datetime.combine(day, s.end_time, tz)
                if ws < end and we > start:
                    out.append(Window(s.id, s.name, ws, we))
            day += timedelta(days=1)
    return sorted(out, key=lambda w: w.start)


def active_window(schedules: Iterable[Schedule], now: datetime) -> Window | None:
    """now 를 포함하는 구간. 시작 정각은 구간 안, 종료 정각은 구간 밖이다."""
    found = windows_between(schedules, now, now + timedelta(microseconds=1))
    return found[0] if found else None


def next_window(schedules: Iterable[Schedule], now: datetime) -> Window | None:
    """now 이후에 시작하는 가장 가까운 구간(일주일 안)."""
    for w in windows_between(schedules, now, now + timedelta(days=8)):
        if w.start > now:
            return w
    return None


def overlaps(a: Schedule, b: Schedule) -> bool:
    """같은 요일에 시간이 겹치는가. 끝 정각과 시작 정각이 맞닿는 것은 겹침이 아니다.

    ponytail: 두 스케줄의 타임존이 같다고 본다(MVP는 Asia/Seoul 고정).
    """
    return bool(set(a.weekdays) & set(b.weekdays)) and a.start_time < b.end_time and b.start_time < a.end_time


def fmt_hm(t: dtime) -> str:
    return t.strftime("%H:%M")


class ScheduleRunner:
    def __init__(
        self,
        player: PlayerController,
        session_factory: Callable[[], Session],
        clock: Callable[[], datetime] = utcnow,
    ):
        self._player = player
        self._sessions = session_factory
        self.clock = clock
        self._lock = threading.RLock()
        self._started = False  # 첫 tick 은 앱 재시작 직후 → 재합류 판단
        self._window: Window | None = None  # 지금 맡고 있는 구간(재생했거나, 건너뛰기로 한 구간)
        self._playing = False  # 그 구간을 예약으로 재생 중인가(수동 정지·비상 정지하면 False)
        self._event_id: int | None = None
        self._manual_since: datetime | None = None  # 구간 밖 수동 재생을 처음 본 시각
        self.warning: str | None = None  # 콘솔에 띄울 경고(곡 없음 등)

    # ---- 매 tick -------------------------------------------------------

    def tick(self, now: datetime | None = None) -> None:
        now = now or self.clock()
        with self._lock, self._sessions() as db:
            schedules = _enabled(db)
            window = active_window(schedules, now)
            first = not self._started
            self._started = True
            if first:
                self._close_interrupted(db, now)

            if self._window and (window is None or window.key != self._window.key):
                self._end(db, now)  # 구간이 끝났거나 바뀜(스케줄 수정·삭제 포함)
            if window and self._window is None:
                schedule = next(s for s in schedules if s.id == window.schedule_id)
                if first and now - window.start > START_GRACE:
                    self._rejoin(db, window, schedule, now)
                else:
                    self._start(db, window, schedule, now, "SCHEDULE")

            if window is None:
                self._auto_stop_manual(db, now)
            else:
                self._manual_since = None

    def safe_tick(self) -> None:
        """APScheduler 가 부르는 입구. 한 번 실패해도 다음 tick 은 계속 돈다."""
        try:
            self.tick()
        except Exception:  # noqa: BLE001
            log.exception("schedule tick failed")

    # ---- 구간 시작·종료·재합류 ----------------------------------------------

    def _start(self, db: Session, window: Window, schedule: Schedule, now: datetime, trigger: str) -> None:
        self._window = window
        self._playing = False
        self._event_id = None
        max_volume = get_max_volume(db)
        playlist = schedule.playlist
        tracks = [i.track for i in playlist.items if i.track.media_status == "READY" and i.track.media_path]
        if not playlist.is_active or not tracks:
            # 재생 가능한 곡이 없으면 재생하지 않고 정지 상태를 유지한다(10장 라이브러리 부족)
            result = "PLAYLIST_INACTIVE" if not playlist.is_active else "NO_READY_TRACKS"
            self.warning = (
                f"'{schedule.name}': 플레이리스트 '{playlist.name}'이(가) 비활성이라 재생하지 않았어요."
                if not playlist.is_active
                else f"'{schedule.name}': 플레이리스트 '{playlist.name}'에 재생 가능한 곡이 없어 재생하지 않았어요."
            )
            self._record(db, trigger, schedule.id, now, result, ended_at=now)
            log.warning("schedule start skipped schedule_id=%s result=%s", schedule.id, result)
            try:
                self._player.stop()
            except PlayerError:
                pass
            return
        if schedule.playback_mode == "SHUFFLE":
            random.shuffle(tracks)
        try:
            self._player.load_queue([t.media_path for t in tracks], repeat=schedule.on_playlist_end == "REPEAT")
            self._playing = True
            self.warning = None
            self._event_id = self._record(db, trigger, schedule.id, now, "PLAYING")
            log.info(
                "schedule start schedule_id=%s trigger=%s tracks=%s volume=%s cap=%s",
                schedule.id, trigger, len(tracks), schedule.volume, max_volume,
            )
            self._player.fade_in_play(schedule.volume, schedule.fade_in_seconds, max_volume)
        except PlayerError as exc:
            self._playing = False
            self.warning = f"'{schedule.name}' 재생 시작 실패: {exc.message}"
            if self._event_id:
                self._close(db, now, "FAILED", exc.code)
            else:
                self._record(db, trigger, schedule.id, now, "FAILED", ended_at=now, error_code=exc.code)
            log.error("schedule start failed schedule_id=%s code=%s", schedule.id, exc.code)

    def _end(self, db: Session, now: datetime) -> None:
        """종료 정지는 수동 조작 여부와 관계없이 항상 실행한다(10장 강의 중 방해 방지)."""
        schedule = db.get(Schedule, self._window.schedule_id)
        fade = schedule.fade_out_seconds if schedule else 3
        try:
            self._player.fade_out_stop(fade, get_max_volume(db))
        except PlayerError as exc:
            log.error("schedule stop failed schedule_id=%s code=%s", self._window.schedule_id, exc.code)
        if self._playing:
            self._close(db, now, "COMPLETED")
        log.info("schedule end schedule_id=%s", self._window.schedule_id)
        self._window = None
        self._playing = False
        self._event_id = None

    def _rejoin(self, db: Session, window: Window, schedule: Schedule, now: datetime) -> None:
        """재시작 재합류(10장): RESTART 는 처음부터, 남은 시간이 짧거나 SKIP 이면 이 구간은 건너뛴다."""
        remaining = (window.end - now).total_seconds()
        minimum = get_int_setting(db, "rejoin_min_remaining_seconds", 120)
        if schedule.rejoin_policy == "SKIP" or remaining < minimum:
            result = "SKIPPED" if schedule.rejoin_policy == "SKIP" else "TOO_LATE"
            self._window = window
            self._playing = False
            self._record(db, "REJOIN", schedule.id, now, result, ended_at=now)
            log.info("schedule rejoin schedule_id=%s result=%s remaining=%ss", schedule.id, result, int(remaining))
            return
        log.info("schedule rejoin schedule_id=%s result=RESTART remaining=%ss", schedule.id, int(remaining))
        self._start(db, window, schedule, now, "REJOIN")

    def _auto_stop_manual(self, db: Session, now: datetime) -> None:
        """구간 밖 수동 재생은 정해진 시간 뒤 자동 정지(10장)."""
        if self._player.status().get("state") != "play":
            self._manual_since = None
            return
        if self._manual_since is None:
            self._manual_since = now
            return
        limit = timedelta(minutes=get_int_setting(db, "manual_play_auto_stop_minutes", 30))
        if now - self._manual_since < limit:
            return
        try:
            self._player.fade_out_stop(3, get_max_volume(db))
        except PlayerError as exc:
            log.error("manual auto stop failed code=%s", exc.code)
            return
        self._record(db, "MANUAL", None, self._manual_since, "AUTO_STOPPED", ended_at=now)
        log.info("manual playback auto-stopped after %s", limit)
        self._manual_since = None

    # ---- API 에서 부르는 조작 ----------------------------------------------

    def manual_stop(self, emergency: bool = False) -> None:
        """구간 중 수동 정지·비상 정지: 그 구간은 정지 유지. 플레이어는 호출한 쪽이 먼저 멈춘다."""
        with self._lock, self._sessions() as db:
            if self._window and self._playing:
                self._playing = False
                self._close(db, self.clock(), "EMERGENCY_STOP" if emergency else "MANUAL_STOP")

    def resume_schedule(self) -> None:
        """수동 정지한 예약 구간을 다시 시작한다(플레이리스트 처음부터 페이드 인)."""
        now = self.clock()
        with self._lock, self._sessions() as db:
            schedules = _enabled(db)
            window = active_window(schedules, now)
            if window is None:
                raise PlayerError(409, "NO_ACTIVE_SCHEDULE", "지금은 예약 재생 시간이 아니에요.")
            if self._playing and self._window and self._window.key == window.key:
                raise PlayerError(409, "SCHEDULE_ALREADY_PLAYING", "이미 예약 재생 중이에요.")
            schedule = next(s for s in schedules if s.id == window.schedule_id)
            self._start(db, window, schedule, now, "SCHEDULE")
            if not self._playing:
                raise PlayerError(409, "SCHEDULE_NOT_STARTED", self.warning or "예약 재생을 시작하지 못했어요.")

    # ---- 화면용 정보 -------------------------------------------------------

    def info(self, db: Session, state: str) -> dict:
        """재생 원인(예약/수동)·현재 구간·다음 예약. 락을 잡지 않는다(tick 이 페이드 중이어도 바로 응답)."""
        now = self.clock()
        schedules = _enabled(db)
        window = active_window(schedules, now)
        nxt = next_window(schedules, now)
        playing_schedule = self._playing and window is not None and self._window is not None and self._window.key == window.key
        cause = None
        if state in ("play", "pause"):
            cause = "SCHEDULE" if playing_schedule else "MANUAL"
        return {
            "cause": cause,
            "current": _window_out(window, schedules) if window else None,
            "can_resume": window is not None and not playing_schedule,
            "next": _window_out(nxt, schedules) if nxt else None,
            "warning": self.warning,
        }

    # ---- 기록 ---------------------------------------------------------------

    def _close_interrupted(self, db: Session, now: datetime) -> None:
        """앱이 구간 도중 꺼져서 닫히지 못한 기록을 INTERRUPTED 로 닫는다(재합류 기록은 새로 남는다)."""
        open_events = db.scalars(select(PlaybackEvent).where(PlaybackEvent.ended_at.is_(None))).all()
        for e in open_events:
            e.ended_at = now
            e.result = "INTERRUPTED"
        if open_events:
            db.commit()
            log.warning("closed %s playback event(s) interrupted by restart", len(open_events))

    def _record(
        self, db: Session, trigger: str, schedule_id: int | None, started_at: datetime, result: str,
        ended_at: datetime | None = None, error_code: str | None = None,
    ) -> int:
        event = PlaybackEvent(
            trigger=trigger, schedule_id=schedule_id, started_at=started_at, ended_at=ended_at,
            result=result, error_code=error_code,
        )
        db.add(event)
        db.commit()
        return event.id

    def _close(self, db: Session, now: datetime, result: str, error_code: str | None = None) -> None:
        event = db.get(PlaybackEvent, self._event_id) if self._event_id else None
        if event is None:
            return
        event.ended_at = now
        event.result = result
        event.error_code = error_code
        db.commit()


def _enabled(db: Session) -> list[Schedule]:
    return list(
        db.scalars(
            select(Schedule)
            .where(Schedule.is_enabled)
            .options(
                selectinload(Schedule.playlist).selectinload(Playlist.items).selectinload(PlaylistItem.track)
            )
        )
    )


def _window_out(w: Window, schedules: list[Schedule]) -> dict:
    s = next((s for s in schedules if s.id == w.schedule_id), None)
    return {
        "schedule_id": w.schedule_id,
        "name": w.name,
        "playlist_name": s.playlist.name if s else None,
        "start": w.start.isoformat(),
        "end": w.end.isoformat(),
    }
