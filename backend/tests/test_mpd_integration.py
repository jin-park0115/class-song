"""실제 mpd 데몬(null 출력)으로 PlayerController를 검증한다. mpd·ffmpeg가 없으면 건너뛴다.

강의실 서버에서는 ALSA 출력으로 같은 시나리오를 scripts/mpd-smoke-test.sh 로 확인한다.
"""

import shutil
import socket
import subprocess
import time

import pytest

from app.services.player import PlayerController

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (shutil.which("mpd") and shutil.which("ffmpeg")), reason="mpd/ffmpeg 미설치"
    ),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mpd_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("mpd")
    music = root / "music"
    music.mkdir()
    for freq in (440, 660):
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=20",
             "-metadata", f"title=Tone {freq}", "-ac", "2", str(music / f"tone-{freq}.mp3")],
            check=True,
        )
    port = _free_port()
    conf = root / "mpd.conf"
    conf.write_text(
        f'music_directory "{music}"\n'
        f'db_file "{root / "database"}"\n'
        f'state_file "{root / "state"}"\n'
        f'pid_file "{root / "pid"}"\n'
        f'log_file "{root / "log"}"\n'
        'bind_to_address "127.0.0.1"\n'
        f'port "{port}"\n'
        'audio_output {\n type "null"\n name "Null Output"\n mixer_type "software"\n}\n'
    )
    proc = subprocess.Popen(["mpd", "--no-daemon", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("mpd가 시작되지 않았습니다")

    from mpd import MPDClient

    c = MPDClient()
    c.connect("127.0.0.1", port)
    c.update()
    for _ in range(50):
        if "updating_db" not in c.status():
            break
        time.sleep(0.1)
    c.clear()
    for f in ("tone-440.mp3", "tone-660.mp3"):
        c.add(f)
    c.disconnect()

    yield port
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def player(mpd_server):
    p = PlayerController("127.0.0.1", mpd_server)
    yield p
    p.stop()
    p.close()


def test_real_mpd_full_cycle(player):
    st = player.status()
    assert st["connected"] and st["queue_length"] == 2
    assert st["outputs"] == [{"name": "Null Output", "enabled": True}]

    player.play(max_volume=40)
    assert player.status()["volume"] <= 40  # MPD 기본 100% 에서 시작해도 상한으로 내려간다
    time.sleep(1.2)
    st = player.status()
    assert st["state"] == "play"
    assert st["current"]["title"] == "Tone 440"
    assert st["next"]["title"] == "Tone 660"
    assert st["elapsed"] > 0 and st["duration"] == pytest.approx(20, abs=0.5)

    player.pause()
    assert player.status()["state"] == "pause"
    player.resume(max_volume=40)
    assert player.status()["state"] == "play"

    vol = player.set_volume(80, max_volume=40)
    assert vol.applied == 40 and vol.clamped
    assert player.status()["volume"] == 40

    player.mute()
    assert player.status()["volume"] == 0
    player.unmute(max_volume=40)
    assert player.status()["volume"] == 40

    player.next()
    assert player.status()["current"]["title"] == "Tone 660"

    player.emergency_stop()
    assert player.status()["state"] == "stop"
