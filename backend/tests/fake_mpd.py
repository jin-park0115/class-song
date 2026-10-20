"""python-mpd2 MPDClient 중 PlayerController가 쓰는 부분만 흉내 낸 테스트 더블."""

from mpd import CommandError
from mpd import ConnectionError as MPDConnectionError


class FakeMPD:
    def __init__(self, songs: list[dict] | None = None, volume: int = 20):
        self.songs = songs if songs is not None else [
            {"file": "a.mp3", "title": "Song A", "artist": "Artist A", "duration": "30.0"},
            {"file": "b.mp3", "title": "Song B", "artist": "Artist B", "duration": "40.0"},
        ]
        self.state = "stop"
        self.pos = 0
        self.volume = volume
        self.down = False  # True면 연결/명령이 모두 실패한다
        self.drop_next_call = False  # True면 다음 명령 한 번만 끊김 → 재연결 테스트
        self.connects = 0
        self.timeout = None

    def factory(self):
        return _Client(self)


class _Client:
    def __init__(self, fake: FakeMPD):
        self._f = fake
        self.timeout = None

    def _check(self):
        if self._f.down:
            raise MPDConnectionError("Connection lost")
        if self._f.drop_next_call:
            self._f.drop_next_call = False
            raise MPDConnectionError("Connection lost")

    def connect(self, host, port):
        if self._f.down:
            raise ConnectionRefusedError("refused")
        self._f.connects += 1

    def password(self, _):
        pass

    def disconnect(self):
        pass

    def status(self):
        self._check()
        f = self._f
        st = {"volume": str(f.volume), "state": f.state, "playlistlength": str(len(f.songs))}
        if f.state != "stop" and f.songs:
            st |= {"song": str(f.pos), "elapsed": "5.0", "duration": f.songs[f.pos]["duration"]}
            if f.pos + 1 < len(f.songs):
                st["nextsong"] = str(f.pos + 1)
        return st

    def currentsong(self):
        self._check()
        f = self._f
        if f.state == "stop" or not f.songs:
            return {}
        return {**f.songs[f.pos], "pos": str(f.pos)}

    def playlistinfo(self, pos):
        self._check()
        return [{**self._f.songs[pos], "pos": str(pos)}]

    def outputs(self):
        self._check()
        return [{"outputid": "0", "outputname": "Fake Output", "outputenabled": "1"}]

    def play(self):
        self._check()
        self._f.state = "play"

    def pause(self, flag):
        self._check()
        if self._f.state != "stop":
            self._f.state = "pause" if flag else "play"

    def stop(self):
        self._check()
        self._f.state = "stop"

    def next(self):
        self._check()
        f = self._f
        if f.state == "stop":
            return
        if f.pos + 1 < len(f.songs):
            f.pos += 1
        else:
            f.state = "stop"
            f.pos = 0

    def setvol(self, v):
        self._check()
        if not 0 <= v <= 100:
            raise CommandError("[2@0] {setvol} Invalid volume value")
        self._f.volume = v
