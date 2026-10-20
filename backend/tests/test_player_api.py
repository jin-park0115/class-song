import pytest
from sqlalchemy import select

from app.models import ControlAuditLog, SystemSetting
from tests.conftest import login


def test_status_requires_login(client):
    assert client.get("/api/player/status").status_code == 401


def test_status_shape(admin_client):
    st = admin_client.get("/api/player/status").json()
    assert st["connected"] is True
    assert st["state"] == "stop"
    assert st["volume"] == 20
    assert st["max_volume"] == 40
    assert st["queue_length"] == 2
    assert st["outputs"] == [{"name": "Fake Output", "enabled": True}]


def test_play_pause_resume_next_stop(admin_client):
    def act(action):
        res = admin_client.post("/api/player/control", json={"action": action})
        assert res.status_code == 200, res.text
        return res.json()["status"]

    st = act("play")
    assert st["state"] == "play" and st["current"]["title"] == "Song A"
    assert st["next"]["title"] == "Song B"
    assert act("pause")["state"] == "pause"
    assert act("resume")["state"] == "play"
    assert act("next")["current"]["title"] == "Song B"
    assert act("stop")["state"] == "stop"


def test_volume_is_clamped_to_max(admin_client, fake_mpd):
    """수용 테스트 4: 80 요청 → 상한 40 적용, clamped=true."""
    res = admin_client.post("/api/player/control", json={"action": "volume", "value": 80})
    assert res.status_code == 200
    body = res.json()
    assert body["applied_volume"] == 40 and body["clamped"] is True
    assert fake_mpd.volume == 40


def test_volume_within_max_not_clamped(admin_client, fake_mpd):
    res = admin_client.post("/api/player/control", json={"action": "volume", "value": 25})
    assert res.json()["clamped"] is False and fake_mpd.volume == 25


def test_max_volume_from_system_settings(admin_client, fake_mpd, db):
    db.add(SystemSetting(key="max_volume", value_json=30))
    db.commit()
    res = admin_client.post("/api/player/control", json={"action": "volume", "value": 35})
    assert res.json()["applied_volume"] == 30
    assert res.json()["status"]["max_volume"] == 30


@pytest.mark.parametrize("value", [-1, 101, None])
def test_volume_validation(admin_client, value):
    res = admin_client.post("/api/player/control", json={"action": "volume", "value": value})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_FAILED"


def test_unknown_action_rejected(admin_client):
    assert admin_client.post("/api/player/control", json={"action": "explode"}).status_code == 422


def test_mute_and_unmute_restores_volume(admin_client, fake_mpd):
    fake_mpd.volume = 30
    st = admin_client.post("/api/player/control", json={"action": "mute"}).json()["status"]
    assert fake_mpd.volume == 0 and st["muted"] is True
    res = admin_client.post("/api/player/control", json={"action": "unmute"}).json()
    assert fake_mpd.volume == 30 and res["applied_volume"] == 30 and res["status"]["muted"] is False


def test_unmute_respects_lowered_max(admin_client, fake_mpd, db):
    fake_mpd.volume = 35
    admin_client.post("/api/player/control", json={"action": "mute"})
    db.add(SystemSetting(key="max_volume", value_json=20))
    db.commit()
    res = admin_client.post("/api/player/control", json={"action": "unmute"}).json()
    assert fake_mpd.volume == 20 and res["clamped"] is True


@pytest.mark.parametrize("action", ["play", "resume"])
def test_playback_lowers_volume_above_cap(admin_client, fake_mpd, action):
    """MPD가 100%로 시작해도 소리가 나기 전에 상한으로 내린다."""
    if action == "resume":
        fake_mpd.state = "pause"
    fake_mpd.volume = 100
    res = admin_client.post("/api/player/control", json={"action": action})
    assert res.status_code == 200 and fake_mpd.volume == 40


def test_play_with_empty_queue(admin_client, fake_mpd):
    fake_mpd.songs = []
    res = admin_client.post("/api/player/control", json={"action": "play"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "QUEUE_EMPTY"


def test_student_cannot_control(client, make_user):
    """수용 테스트 10: 학생이 /api/player/control 호출 → 403."""
    make_user("student01")
    csrf = login(client, "student01")
    res = client.post("/api/player/control", json={"action": "play"}, headers={"X-CSRF-Token": csrf})
    assert res.status_code == 403 and res.json()["error"]["code"] == "FORBIDDEN"
    res = client.post("/api/player/emergency-stop", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 403


def test_student_can_view_status(client, make_user):
    make_user("student01")
    login(client, "student01")
    assert client.get("/api/player/status").status_code == 200


def test_pending_account_cannot_view_status(client, make_user):
    make_user("newbie01", status="PENDING_APPROVAL")
    login(client, "newbie01")
    res = client.get("/api/player/status")
    assert res.status_code == 403 and res.json()["error"]["code"] == "ACCOUNT_PENDING"
    assert client.get("/api/auth/me").status_code == 200


def test_control_without_csrf_rejected(admin_client):
    res = admin_client.post("/api/player/control", json={"action": "play"}, headers={"X-CSRF-Token": ""})
    assert res.status_code == 403 and res.json()["error"]["code"] == "CSRF_FAILED"


def test_unauthenticated_control_401(client):
    assert client.post("/api/player/control", json={"action": "play"}).status_code == 401


def test_emergency_stop(admin_client, fake_mpd, db):
    admin_client.post("/api/player/control", json={"action": "play"})
    res = admin_client.post("/api/player/emergency-stop")
    assert res.status_code == 200 and fake_mpd.state == "stop"
    assert db.scalar(select(ControlAuditLog).where(ControlAuditLog.action == "player.emergency_stop"))


def test_control_is_audited(admin_client, db):
    admin_client.post("/api/player/control", json={"action": "volume", "value": 90})
    row = db.scalar(select(ControlAuditLog).where(ControlAuditLog.action == "player.volume"))
    assert row.payload == {"action": "volume", "requested": 90, "applied": 40}
    assert row.actor_id is not None


def test_mpd_down_status_and_control(admin_client, fake_mpd, db):
    """수용 테스트 11 일부: MPD 중단 시 상태로 식별 가능, 제어는 503."""
    fake_mpd.down = True
    st = admin_client.get("/api/player/status").json()
    assert st["connected"] is False and st["error"]["code"] == "MPD_UNAVAILABLE"
    res = admin_client.post("/api/player/control", json={"action": "play"})
    assert res.status_code == 503 and res.json()["error"]["code"] == "MPD_UNAVAILABLE"
    res = admin_client.post("/api/player/emergency-stop")
    assert res.status_code == 503
    failed = db.scalar(select(ControlAuditLog).where(ControlAuditLog.action == "player.emergency_stop"))
    assert failed.payload["error"] == "MPD_UNAVAILABLE"


def test_reconnects_after_mpd_restart(admin_client, fake_mpd):
    assert admin_client.get("/api/player/status").json()["connected"]
    fake_mpd.down = True
    assert not admin_client.get("/api/player/status").json()["connected"]
    fake_mpd.down = False
    assert admin_client.get("/api/player/status").json()["connected"]


def test_queue_lists_items_in_order(admin_client):
    items = admin_client.get("/api/player/queue").json()["items"]
    assert [i["title"] for i in items] == ["Song A", "Song B"]
    assert items[1] == {"title": "Song B", "artist": "Artist B", "file": "b.mp3", "duration": 40.0, "position": 1}


def test_queue_requires_active_user(client):
    assert client.get("/api/player/queue").status_code == 401


def test_status_has_queue_version(admin_client):
    assert admin_client.get("/api/player/status").json()["queue_version"] == 1


def test_play_at_and_previous(admin_client, fake_mpd):
    res = admin_client.post("/api/player/control", json={"action": "play_at", "position": 1})
    assert res.status_code == 200 and res.json()["status"]["current"]["title"] == "Song B"
    res = admin_client.post("/api/player/control", json={"action": "previous"})
    assert res.json()["status"]["current"]["title"] == "Song A"


def test_play_at_enforces_volume_cap(admin_client, fake_mpd):
    fake_mpd.volume = 90
    admin_client.post("/api/player/control", json={"action": "play_at", "position": 0})
    assert fake_mpd.volume == 40


@pytest.mark.parametrize("body", [{"action": "play_at"}, {"action": "play_at", "position": -1}])
def test_play_at_validation(admin_client, body):
    assert admin_client.post("/api/player/control", json=body).status_code == 422


def test_play_at_out_of_range(admin_client):
    res = admin_client.post("/api/player/control", json={"action": "play_at", "position": 9})
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_POSITION"
