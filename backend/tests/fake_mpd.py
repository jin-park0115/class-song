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
        self.version = 1
        self.updates: list = []
        self.missing: set[str] = set()  # add 시 "파일 없음"을 낼 경로
        self.volume_log: list[int] = []  # setvol 이력(페이드 단계 검사)
        self.repeat = 0
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
        st = {
            "volume": str(f.volume),
            "state": f.state,
            "playlistlength": str(len(f.songs)),
            "playlist": str(f.version),
        }
        if f.songs:
            st["song"] = str(f.pos)  # 실제 MPD도 멈춘 뒤에 마지막 곡 위치를 알려준다
        if f.state != "stop" and f.songs:
            st |= {"elapsed": "5.0", "duration": f.songs[f.pos]["duration"]}
            if f.pos + 1 < len(f.songs):
                st["nextsong"] = str(f.pos + 1)
        return st

    def currentsong(self):
        self._check()
        f = self._f
        if f.state == "stop" or not f.songs:
            return {}
        return {**f.songs[f.pos], "pos": str(f.pos)}

    def playlistinfo(self, pos=None):
        self._check()
        if pos is None:
            return [{**song, "pos": str(i)} for i, song in enumerate(self._f.songs)]
        return [{**self._f.songs[pos], "pos": str(pos)}]

    def outputs(self):
        self._check()
        return [{"outputid": "0", "outputname": "Fake Output", "outputenabled": "1"}]

    def play(self, pos=None):
        self._check()
        if pos is not None:
            self._f.pos = pos
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
            raise CommandError("[55@0] {next} Not playing")  # 실제 MPD와 같다
        if f.pos + 1 < len(f.songs):
            f.pos += 1
        else:
            f.state = "stop"
            f.pos = 0

    def previous(self):
        self._check()
        f = self._f
        if f.state == "stop":
            raise CommandError("[55@0] {previous} Not playing")
        f.pos = max(0, f.pos - 1)

    def update(self, uri=None):
        self._check()
        self._f.updates.append(uri)
        return "1"

    def add(self, uri):
        self._check()
        if uri in self._f.missing:
            raise CommandError("[50@0] {add} No such directory")
        self._f.songs.append({"file": uri, "duration": "30.0"})
        self._f.version += 1

    def delete(self, pos):
        self._check()
        self._f.songs.pop(pos)
        self._f.version += 1

    def setvol(self, v):
        self._check()
        if not 0 <= v <= 100:
            raise CommandError("[2@0] {setvol} Invalid volume value")
        self._f.volume = v
        self._f.volume_log.append(v)

    def clear(self):
        self._check()
        f = self._f
        f.songs = []
        f.state = "stop"
        f.pos = 0
        f.version += 1

    def repeat(self, flag):
        self._check()
        self._f.repeat = int(flag)

    def random(self, flag):
        self._check()
