"""MPD에 명령을 보내는 유일한 창구(요구사항 5장).

API와 이후 단계의 스케줄러는 이 컨트롤러를 통해서만 재생을 제어한다.
MPD 연결은 하나만 유지하고, 모든 명령은 락으로 직렬화한다.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mpd import CommandError, MPDClient
from mpd import ConnectionError as MPDConnectionError

log = logging.getLogger("jukebox.player")

_CONNECTION_ERRORS = (MPDConnectionError, OSError, EOFError)
FADE_STEP_SECONDS = 0.2  # 페이드 한 단계 간격(3초 페이드 = 15단계)


class PlayerError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class VolumeResult:
    requested: int
    applied: int
    clamped: bool


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _song(info: dict | None) -> dict | None:
    if not info:
        return None
    file = info.get("file", "")
    return {
        "title": info.get("title") or file.rsplit("/", 1)[-1],
        "artist": info.get("artist"),
        "file": file,
        "duration": _float(info.get("duration") or info.get("time")),
        "position": _int(info.get("pos")),
    }


class PlayerController:
    def __init__(
        self,
        host: str,
        port: int,
        password: str | None = None,
        timeout: float = 5.0,
        client_factory: Callable[[], MPDClient] = MPDClient,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._host = host
        self._port = port
        self._password = password
        self._timeout = timeout
        self._client_factory = client_factory
        self._client: MPDClient | None = None
        self._lock = threading.RLock()
        # MPD에는 음소거 명령이 없으므로 음소거 직전 볼륨을 기억했다가 복원한다.
        self._muted_volume: int | None = None
        self._sleep = sleep  # 테스트에서 페이드를 기다리지 않도록 바꿔 끼운다
        # 수동 조작(정지·볼륨·일시정지 등)이 들어오면 번호가 바뀌고, 진행 중인 페이드는 그 자리에서 멈춘다
        self._fade_gen = 0

    # ---- 연결 -------------------------------------------------------------

    def _connect(self) -> MPDClient:
        client = self._client_factory()
        client.timeout = self._timeout
        client.connect(self._host, self._port)
        if self._password:
            client.password(self._password)
        log.info("mpd connected host=%s port=%s", self._host, self._port)
        return client

    def _drop(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:  # noqa: BLE001 - 이미 끊긴 연결 정리
                pass
        self._client = None

    def _call(self, fn: Callable[[MPDClient], Any]) -> Any:
        """명령을 실행한다. 연결이 끊겼으면 한 번 재연결 후 재시도한다."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._client is None:
                        self._client = self._connect()
                    return fn(self._client)
                except CommandError as exc:
                    log.warning("mpd command failed: %s", exc)
                    raise PlayerError(502, "MPD_COMMAND_FAILED", f"MPD 명령 실패: {exc}") from exc
                except _CONNECTION_ERRORS as exc:
                    self._drop()
                    if attempt == 2:
                        log.error("mpd unavailable: %s", exc)
                        raise PlayerError(
                            503, "MPD_UNAVAILABLE", "MPD 서버에 연결할 수 없습니다."
                        ) from exc
                    log.warning("mpd connection lost, reconnecting: %s", exc)

    # ---- 조회 -------------------------------------------------------------

    def status(self) -> dict:
        """재생 현황(FR-010). MPD에 연결할 수 없어도 예외 대신 connected=False 로 돌려준다."""

        def _read(c: MPDClient) -> dict:
            st = c.status()
            current = _song(c.currentsong())
            next_pos = _int(st.get("nextsong"))
            nxt = _song(c.playlistinfo(next_pos)[0]) if next_pos is not None else None
            outputs = [
                {"name": o.get("outputname"), "enabled": o.get("outputenabled") == "1"}
                for o in c.outputs()
            ]
            return {"status": st, "current": current, "next": nxt, "outputs": outputs}

        try:
            raw = self._call(_read)
        except PlayerError as exc:
            return {
                "connected": False,
                "state": "unknown",
                "volume": None,
                "muted": self._muted_volume is not None,
                "current": None,
                "next": None,
                "elapsed": None,
                "duration": None,
                "queue_length": None,
                "queue_version": None,
                "outputs": [],
                "error": {"code": exc.code, "message": exc.message},
            }

        st = raw["status"]
        volume = _int(st.get("volume"))
        current = raw["current"]
        mpd_error = st.get("error")
        no_output = bool(raw["outputs"]) and not any(o["enabled"] for o in raw["outputs"])
        error = None
        if mpd_error:
            error = {"code": "MPD_PLAYBACK_ERROR", "message": mpd_error}
        elif not raw["outputs"] or no_output:
            error = {"code": "NO_AUDIO_OUTPUT", "message": "활성화된 오디오 출력 장치가 없습니다."}
        return {
            "connected": True,
            "state": st.get("state", "unknown"),  # play | pause | stop
            # 믹서가 없는 출력에서는 MPD가 volume=-1 을 돌려준다.
            "volume": volume if volume is not None and volume >= 0 else None,
            "muted": self._muted_volume is not None,
            "current": current,
            "next": raw["next"],
            "elapsed": _float(st.get("elapsed")),
            "duration": _float(st.get("duration")) or (current or {}).get("duration"),
            "queue_length": _int(st.get("playlistlength")),
            # 대기열이 바뀔 때마다 MPD가 올리는 번호. 화면은 이 값이 바뀔 때만 대기열을 다시 읽는다.
            "queue_version": _int(st.get("playlist")),
            "outputs": raw["outputs"],
            "error": error,
        }

    def queue(self, limit: int = 200) -> list[dict]:
        def _read(c: MPDClient) -> list[dict]:
            return [_song(i) for i in c.playlistinfo()[:limit]]

        return self._call(_read)

    def add_to_queue(self, uri: str, scan_timeout: float = 10.0) -> None:
        """새로 저장한 파일을 MPD 라이브러리에 반영(update)한 뒤 대기열 끝에 추가한다.

        update 는 비동기라서 끝날 때까지 기다린다. 기다리는 동안 락을 잡지 않아 상태 조회는 계속된다.
        """
        self._call(lambda c: c.update(uri))
        self._wait_scan(scan_timeout)
        self._call(lambda c: c.add(uri))

    def _wait_scan(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while "updating_db" in self._call(lambda c: c.status()):
            if time.monotonic() > deadline:
                raise PlayerError(504, "MPD_SCAN_TIMEOUT", "MPD가 새 파일을 아직 인식하지 못했습니다. 잠시 후 다시 시도하세요.")
            time.sleep(0.1)

    def load_queue(self, uris: list[str], repeat: bool, scan_timeout: float = 30.0) -> int:
        """대기열을 비우고 이 곡들로 채운다(예약 재생 시작). MPD가 못 찾는 파일은 건너뛴다. 넣은 곡 수를 돌려준다."""
        self._call(lambda c: c.update())  # 업로드 뒤 아직 MPD 라이브러리에 없는 파일 반영
        self._wait_scan(scan_timeout)

        def _load(c: MPDClient) -> int:
            c.clear()
            added = 0
            for uri in uris:
                try:
                    c.add(uri)
                    added += 1
                except CommandError as exc:
                    log.warning("schedule track missing in mpd uri=%s err=%s", uri, exc)
            c.repeat(1 if repeat else 0)  # on_playlist_end=REPEAT 면 끝나면 처음부터
            c.random(0)  # 섞기는 넣을 때 순서를 섞어서 한다
            return added

        added = self._call(_load)
        if not added:
            raise PlayerError(409, "NO_READY_TRACKS", "재생할 수 있는 곡이 없습니다.")
        return added

    def remove_at(self, position: int) -> None:
        def _remove(c: MPDClient) -> None:
            length = _int(c.status().get("playlistlength")) or 0
            if not 0 <= position < length:
                raise PlayerError(422, "INVALID_POSITION", "대기열에 없는 곡 위치입니다.")
            c.delete(position)

        self._call(_remove)

    def remove_file(self, uri: str) -> None:
        """대기열에서 이 파일을 모두 뺀다(뒤에서부터 지워야 위치가 안 밀린다)."""

        def _remove(c: MPDClient) -> None:
            for song in reversed(c.playlistinfo()):
                if song.get("file") == uri:
                    c.delete(int(song["pos"]))

        self._call(_remove)

    # ---- 제어 -------------------------------------------------------------

    @staticmethod
    def _enforce_cap(c: MPDClient, st: dict, max_volume: int) -> None:
        """소리가 나기 직전에 MPD 볼륨이 상한을 넘으면 내린다(NFR-005 비정상 볼륨 방지).

        MPD는 기본 볼륨 100%로 시작하거나 외부(mpc 등)에서 바뀔 수 있다.
        """
        current = _int(st.get("volume"))
        if current is not None and current > max_volume:
            log.warning("volume %s exceeds cap %s, lowering before playback", current, max_volume)
            c.setvol(max_volume)

    def play(self, max_volume: int) -> None:
        def _play(c: MPDClient) -> None:
            st = c.status()
            if _int(st.get("playlistlength")) in (None, 0):
                raise PlayerError(409, "QUEUE_EMPTY", "재생할 곡이 대기열에 없습니다.")
            self._enforce_cap(c, st, max_volume)
            c.play()

        self._call(_play)

    def play_at(self, position: int, max_volume: int) -> None:
        def _play_at(c: MPDClient) -> None:
            st = c.status()
            length = _int(st.get("playlistlength")) or 0
            if not 0 <= position < length:
                raise PlayerError(422, "INVALID_POSITION", "대기열에 없는 곡 위치입니다.")
            self._enforce_cap(c, st, max_volume)
            c.play(position)

        self._call(_play_at)

    def pause(self) -> None:
        self._cancel_fade()
        self._call(lambda c: c.pause(1))

    def resume(self, max_volume: int) -> None:
        def _resume(c: MPDClient) -> None:
            self._enforce_cap(c, c.status(), max_volume)
            c.pause(0)

        self._call(_resume)

    def stop(self) -> None:
        self._cancel_fade()
        self._call(lambda c: c.stop())

    def next(self, max_volume: int) -> None:
        self._skip(1, max_volume)

    def previous(self, max_volume: int) -> None:
        self._skip(-1, max_volume)

    def _skip(self, step: int, max_volume: int) -> None:
        def _do(c: MPDClient) -> None:
            st = c.status()
            if st.get("state") != "stop":
                # 마지막 곡에서 next 하면 MPD는 재생을 멈춘다. 턴테이블을 잘못 돌려 음악이 끊기지 않게 막는다
                if step > 0 and st.get("nextsong") is None:
                    raise PlayerError(409, "NO_NEXT_SONG", "대기열의 마지막 곡이에요.")
                c.next() if step > 0 else c.previous()
                return
            # 멈춘 상태에서는 MPD가 next/previous 를 거부한다("Not playing"). 멈춘 곡 기준 앞/뒤 곡을 재생한다
            length = _int(st.get("playlistlength")) or 0
            cur = _int(st.get("song"))
            target = 0 if cur is None else cur + step
            if not 0 <= target < length:
                raise PlayerError(409, "NO_NEXT_SONG" if step > 0 else "NO_PREVIOUS_SONG",
                                  "대기열의 마지막 곡이에요." if step > 0 else "대기열의 첫 곡이에요.")
            self._enforce_cap(c, st, max_volume)
            c.play(target)

        self._call(_do)

    def set_volume(self, requested: int, max_volume: int) -> VolumeResult:
        """요청값을 상한으로 자른 뒤 적용한다(NFR-006). 음소거 중이면 음소거를 해제한다."""
        applied = max(0, min(requested, max_volume))
        self._cancel_fade()
        self._call(lambda c: c.setvol(applied))
        self._muted_volume = None
        return VolumeResult(requested=requested, applied=applied, clamped=applied != requested)

    def mute(self) -> None:
        self._cancel_fade()

        def _mute(c: MPDClient) -> None:
            if self._muted_volume is not None:
                return
            current = _int(c.status().get("volume"))
            if current is None or current < 0:
                raise PlayerError(409, "NO_MIXER", "볼륨을 제어할 수 없는 출력 장치입니다.")
            c.setvol(0)
            self._muted_volume = current

        self._call(_mute)

    def unmute(self, max_volume: int) -> VolumeResult | None:
        def _unmute(c: MPDClient) -> VolumeResult | None:
            if self._muted_volume is None:
                return None
            restore = self._muted_volume
            applied = min(restore, max_volume)
            c.setvol(applied)
            self._muted_volume = None
            return VolumeResult(requested=restore, applied=applied, clamped=applied != restore)

        return self._call(_unmute)

    def emergency_stop(self) -> None:
        """페이드 없이 즉시 정지한다. 진행 중인 페이드(예약 시작 등)도 멈춘다."""
        self._cancel_fade()
        self._call(lambda c: c.stop())

    # ---- 페이드(요구사항 4.1: MPD 볼륨을 단계적으로 바꾼다) ----------------

    def _cancel_fade(self) -> None:
        self._fade_gen += 1

    def fade_to(self, target: int, seconds: float, max_volume: int) -> bool:
        """지금 볼륨에서 target(상한으로 제한)까지 seconds 동안 FADE_STEP_SECONDS 간격으로 setvol 한다.

        끝까지 갔으면 True, 중간에 수동 조작으로 취소됐으면 False. 단계 사이에는 락을 잡지 않아 다른 명령이 끼어들 수 있다.
        """
        target = max(0, min(target, max_volume))
        gen = self._fade_gen
        start = _int(self._call(lambda c: c.status()).get("volume"))
        if start is None or start < 0:
            return True  # 믹서가 없는 출력은 볼륨을 바꿀 수 없다
        start = min(start, max_volume)
        steps = max(1, round(seconds / FADE_STEP_SECONDS)) if seconds > 0 else 1
        for i in range(1, steps + 1):
            if self._fade_gen != gen:
                log.info("fade cancelled at step %s/%s", i, steps)
                return False
            vol = round(start + (target - start) * i / steps)
            self._call(lambda c, v=vol: c.setvol(v))
            if i < steps:
                self._sleep(seconds / steps)
        self._muted_volume = None
        return True

    def fade_in_play(self, target: int, seconds: float, max_volume: int) -> bool:
        """볼륨 0에서 대기열 처음부터 재생을 시작하고 target 까지 페이드 인한다."""
        self._cancel_fade()

        def _start(c: MPDClient) -> None:
            if _int(c.status().get("volume")) is not None:
                c.setvol(0)
            c.play(0)

        self._call(_start)
        return self.fade_to(target, seconds, max_volume)

    def fade_out_stop(self, seconds: float, max_volume: int) -> None:
        """재생 중이면 0까지 페이드 아웃하고 정지한다. 수동 재생이 바로 들리도록 볼륨은 원래대로 되돌려 둔다."""
        st = self._call(lambda c: c.status())
        before = _int(st.get("volume"))
        if st.get("state") == "play" and seconds > 0:
            self.fade_to(0, seconds, max_volume)

        def _stop(c: MPDClient) -> None:
            c.stop()
            c.repeat(0)
            if before is not None and before >= 0:
                c.setvol(min(before, max_volume))

        self._cancel_fade()
        self._call(_stop)

    def close(self) -> None:
        with self._lock:
            self._drop()
