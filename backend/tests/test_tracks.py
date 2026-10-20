import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import Track

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
MEDIA = Path(os.environ["MEDIA_DIR"])

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 미설치")


@pytest.fixture(scope="session")
def tone_files(tmp_path_factory):
    """권리 문제가 없는 사인파 MP3 두 개(내용이 달라 해시가 다르다)."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 미설치")
    d = tmp_path_factory.mktemp("tones")
    out = []
    for f in (440, 660):
        p = d / f"tone{f}.mp3"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={f}:duration=3",
             "-b:a", "64k", str(p)],
            check=True,
        )
        out.append(p)
    return out


@pytest.fixture(autouse=True)
def _clean_media():
    yield
    for p in MEDIA.iterdir():
        p.unlink()


@pytest.fixture
def approved_track(as_user, db):
    rid = as_user("student01").post("/api/requests", json={"url": URL}).json()["id"]
    as_user("admin01", role="ADMIN").post(f"/api/admin/requests/{rid}/review", json={"decision": "APPROVE"})
    return db.scalar(select(Track)).id


def confirm(admin, track_id, **extra):
    return admin.patch(
        f"/api/admin/tracks/{track_id}", json={"rights_status": "CONFIRMED", "rights_basis": "OWNED_FILE"} | extra
    )


def upload(admin, track_id, path: Path, name: str | None = None):
    with path.open("rb") as f:
        return admin.post(f"/api/admin/tracks/{track_id}/media", files={"file": (name or path.name, f, "audio/mpeg")})


def test_library_lists_approved_tracks(as_user, approved_track):
    items = as_user("admin01").get("/api/admin/tracks").json()["items"]
    assert [t["id"] for t in items] == [approved_track]
    assert items[0]["rights_status"] == "UNKNOWN" and items[0]["has_media"] is False


def test_upload_requires_confirmed_rights(as_user, approved_track, tone_files):
    admin = as_user("admin01")
    res = upload(admin, approved_track, tone_files[0])
    assert res.status_code == 409 and res.json()["error"]["code"] == "RIGHTS_NOT_CONFIRMED"
    assert list(MEDIA.iterdir()) == []


def test_confirm_rights_needs_basis(as_user, approved_track):
    admin = as_user("admin01")
    res = admin.patch(f"/api/admin/tracks/{approved_track}", json={"rights_status": "CONFIRMED"})
    assert res.json()["error"]["code"] == "RIGHTS_BASIS_REQUIRED"
    res = confirm(admin, approved_track, rights_basis="OTHER")
    assert res.json()["error"]["code"] == "RIGHTS_NOTE_REQUIRED"
    res = confirm(admin, approved_track, rights_basis="OTHER", rights_note="학교 라이선스")
    assert res.json()["rights_status"] == "CONFIRMED" and res.json()["rights_checked_at"]


@needs_ffmpeg
def test_upload_makes_track_ready(as_user, approved_track, tone_files, db):
    """수용 테스트 1: 권리 확인한 MP3 등록 → READY."""
    admin = as_user("admin01")
    confirm(admin, approved_track)
    res = upload(admin, approved_track, tone_files[0], name="../../etc/passwd.mp3")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["media_status"] == "READY" and body["has_media"] and body["duration_seconds"] == 3
    # 파일명은 사용자 입력이 아니라 트랙 ID
    assert sorted(p.name for p in MEDIA.iterdir()) == [f"{approved_track}.mp3"]
    ready = as_user("student01").get("/api/tracks", params={"status": "ready"}).json()["items"]
    assert [t["id"] for t in ready] == [approved_track]


def test_upload_rejects_non_audio(as_user, approved_track, tmp_path):
    admin = as_user("admin01")
    confirm(admin, approved_track)
    fake = tmp_path / "fake.mp3"
    fake.write_bytes(b"hello, not audio" * 100)
    res = upload(admin, approved_track, fake)
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_AUDIO"
    assert list(MEDIA.iterdir()) == []  # 부분 파일 남지 않음


def test_upload_rejects_extension(as_user, approved_track, tmp_path):
    admin = as_user("admin01")
    confirm(admin, approved_track)
    f = tmp_path / "x.wav"
    f.write_bytes(b"RIFF")
    assert upload(admin, approved_track, f).json()["error"]["code"] == "UNSUPPORTED_FORMAT"


@needs_ffmpeg
def test_upload_extension_must_match_format(as_user, approved_track, tone_files, tmp_path):
    admin = as_user("admin01")
    confirm(admin, approved_track)
    res = upload(admin, approved_track, tone_files[0], name="song.flac")
    assert res.json()["error"]["code"] == "INVALID_AUDIO"


@needs_ffmpeg
def test_upload_too_large(as_user, approved_track, tone_files, monkeypatch):
    admin = as_user("admin01")
    confirm(admin, approved_track)
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    res = upload(admin, approved_track, tone_files[0])
    assert res.status_code == 413
    assert list(MEDIA.iterdir()) == []


@needs_ffmpeg
def test_duplicate_file_detected(as_user, approved_track, tone_files, db):
    admin = as_user("admin01")
    confirm(admin, approved_track)
    upload(admin, approved_track, tone_files[0])
    rid = as_user("student01").post("/api/requests", json={"url": "https://youtu.be/bbbbbbbbbbb"}).json()["id"]
    admin = as_user("admin01")
    admin.post(f"/api/admin/requests/{rid}/review", json={"decision": "APPROVE"})
    other = db.scalar(select(Track.id).where(Track.id != approved_track))
    confirm(admin, other)
    res = upload(admin, other, tone_files[0])
    assert res.status_code == 409 and res.json()["error"]["code"] == "DUPLICATE_FILE"
    assert upload(admin, other, tone_files[1]).status_code == 200


@needs_ffmpeg
def test_deny_blocks_and_reconfirm_restores(as_user, approved_track, tone_files):
    admin = as_user("admin01")
    confirm(admin, approved_track)
    upload(admin, approved_track, tone_files[0])
    res = admin.patch(f"/api/admin/tracks/{approved_track}", json={"rights_status": "DENIED"})
    assert res.json()["media_status"] == "BLOCKED"
    res = admin.post("/api/admin/queue", json={"track_id": approved_track})
    assert res.status_code == 409 and res.json()["error"]["code"] == "TRACK_NOT_READY"
    assert confirm(admin, approved_track).json()["media_status"] == "READY"


@needs_ffmpeg
def test_add_ready_track_to_queue_shows_db_title(as_user, approved_track, tone_files, fake_mpd):
    admin = as_user("admin01")
    confirm(admin, approved_track)
    upload(admin, approved_track, tone_files[0])
    fake_mpd.songs = []
    res = admin.post("/api/admin/queue", json={"track_id": approved_track})
    assert res.status_code == 200
    assert fake_mpd.updates == [f"{approved_track}.mp3"]
    items = admin.get("/api/player/queue").json()["items"]
    assert items[0]["title"] == "Song dQw4w9WgXcQ" and items[0]["artist"] == "Test Channel"
    assert items[0]["track_id"] == approved_track and items[0]["thumbnail_url"]
    st = admin.post("/api/player/control", json={"action": "play"}).json()["status"]
    assert st["current"]["title"] == "Song dQw4w9WgXcQ"

    assert admin.delete("/api/admin/queue/0").status_code == 200
    assert fake_mpd.songs == []
    assert admin.delete("/api/admin/queue/0").status_code == 422


def test_queue_not_ready(as_user, approved_track):
    res = as_user("admin01").post("/api/admin/queue", json={"track_id": approved_track})
    assert res.status_code == 409


def test_students_cannot_manage_library(as_user, approved_track, tmp_path):
    c = as_user("student01")
    assert c.get("/api/admin/tracks").status_code == 403
    assert c.patch(f"/api/admin/tracks/{approved_track}", json={"title": "x"}).status_code == 403
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    assert upload(c, approved_track, f).status_code == 403
    assert c.post("/api/admin/queue", json={"track_id": approved_track}).status_code == 403


def test_get_track(as_user, approved_track):
    c = as_user("student01")
    assert c.get(f"/api/tracks/{approved_track}").json()["id"] == approved_track
    assert c.get("/api/tracks/99999").status_code == 404
