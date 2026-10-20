"""MPD에 명령을 보내는 유일한 창구(요구사항 5장).

API와 이후 단계의 스케줄러는 이 컨트롤러를 통해서만 재생을 제어한다.
MPD 연결은 하나만 유지하고, 모든 명령은 락으로 직렬화한다.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mpd import CommandError, MPDClient
from mpd import ConnectionError as MPDConnectionError

log = logging.getLogger("jukebox.player")

_CONNECTION_ERRORS = (MPDConnectionError, OSError, EOFError)


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
            "outputs": raw["outputs"],
            "error": error,
        }

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

    def pause(self) -> None:
        self._call(lambda c: c.pause(1))

    def resume(self, max_volume: int) -> None:
        def _resume(c: MPDClient) -> None:
            self._enforce_cap(c, c.status(), max_volume)
            c.pause(0)

        self._call(_resume)

    def stop(self) -> None:
        self._call(lambda c: c.stop())

    def next(self) -> None:
        self._call(lambda c: c.next())

    def set_volume(self, requested: int, max_volume: int) -> VolumeResult:
        """요청값을 상한으로 자른 뒤 적용한다(NFR-006). 음소거 중이면 음소거를 해제한다."""
        applied = max(0, min(requested, max_volume))
        self._call(lambda c: c.setvol(applied))
        self._muted_volume = None
        return VolumeResult(requested=requested, applied=applied, clamped=applied != requested)

    def mute(self) -> None:
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
        """페이드 없이 즉시 정지한다."""
        self._call(lambda c: c.stop())

    def close(self) -> None:
        with self._lock:
            self._drop()
