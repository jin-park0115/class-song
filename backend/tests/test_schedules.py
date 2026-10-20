"""STEP 03: 플레이리스트·스케줄·자동 재생(FR-006·007·011·019, 요구사항 4.1·10장).

시각은 FixedClock 으로 고정하고 runner.tick(시각)을 직접 불러 검증한다(서울 시각 기준, 2026-10-19 = 월요일).
"""

import pytest
from sqlalchemy import select

from app.models import PlaybackEvent, Track
from app.services.player import PlayerController

MON = "2026-10-19"
TUE = "2026-10-20"


@pytest.fixture
def tracks(db):
    """재생 가능한(READY) 곡 3개."""
    out = []
    for n in range(1, 4):
        t = Track(title=f"Song {n}", duration_seconds=120, media_status="READY", media_path=f"{n}.mp3",
                  rights_status="CONFIRMED", rights_basis="OWNED_FILE")
        db.add(t)
        db.commit()
        out.append(t.id)
    return out


@pytest.fixture
def playlist(admin_client, tracks):
    pid = admin_client.post("/api/playlists", json={"name": "아침 음악"}).json()["id"]
    assert admin_client.put(f"/api/playlists/{pid}/items", json={"track_ids": tracks}).status_code == 200
    return pid


def schedule(client, pid, **kw):
    body = {"name": "1교시 전", "weekdays": [1, 2, 3, 4, 5], "start_time": "08:00", "end_time": "09:00",
            "playlist_id": pid, "volume": 25} | kw
    return client.post("/api/schedules", json=body)


def at(client, clock, local: str):
    """시계를 서울 시각 local 로 옮기고 스케줄러 tick 한 번."""
    client.app.state.runner.tick(clock.set(local))


def events(db):
    db.expire_all()
    return [(e.trigger, e.result) for e in db.scalars(select(PlaybackEvent).order_by(PlaybackEvent.id))]


# ---- 플레이리스트 ----------------------------------------------------------


def test_playlist_items_ready_only_and_total(admin_client, tracks, db):
    pid = admin_client.post("/api/playlists", json={"name": "점심"}).json()["id"]
    pending = Track(title="미준비 곡", media_status="PENDING_ACQUISITION")
    db.add(pending)
    db.commit()
    res = admin_client.put(f"/api/playlists/{pid}/items", json={"track_ids": [tracks[0], pending.id]})
    assert res.status_code == 422 and res.json()["error"]["code"] == "TRACK_NOT_READY"
    assert res.json()["error"]["track_ids"] == [pending.id]
    assert admin_client.put(f"/api/playlists/{pid}/items", json={"track_ids": [999999]}).status_code == 422

    body = admin_client.put(f"/api/playlists/{pid}/items", json={"track_ids": tracks}).json()
    assert body["item_count"] == 3 and body["total_seconds"] == 360 and body["unplayable_count"] == 0
    # 순서 뒤집기: 저장 중 위치가 잠깐 겹쳐도 커밋 때 검사(DEFERRABLE)
    body = admin_client.put(f"/api/playlists/{pid}/items", json={"track_ids": tracks[::-1]}).json()
    assert [i["track"]["id"] for i in body["items"]] == tracks[::-1]
    assert [i["position"] for i in body["items"]] == [0, 1, 2]


def test_playlist_warns_when_track_becomes_unplayable(admin_client, playlist, tracks):
    admin_client.patch(f"/api/admin/tracks/{tracks[0]}", json={"rights_status": "DENIED"})
    body = admin_client.get(f"/api/playlists/{playlist}").json()
    assert body["unplayable_count"] == 1 and body["items"][0]["track"]["media_status"] == "BLOCKED"


def test_delete_playlist_used_by_schedule(admin_client, playlist):
    sid = schedule(admin_client, playlist).json()["id"]
    res = admin_client.delete(f"/api/playlists/{playlist}")
    assert res.status_code == 409 and res.json()["error"]["code"] == "PLAYLIST_IN_USE"
    assert res.json()["error"]["schedules"][0]["id"] == sid
    admin_client.delete(f"/api/schedules/{sid}")
    assert admin_client.delete(f"/api/playlists/{playlist}").status_code == 200


# ---- 스케줄 저장 규칙 --------------------------------------------------------


def test_overlap_blocked(admin_client, playlist):
    """수용 테스트 6: 겹치는 활성 스케줄은 저장되지 않고 충돌 스케줄을 알려 준다."""
    a = schedule(admin_client, playlist).json()
    res = schedule(admin_client, playlist, name="겹침", weekdays=[1], start_time="08:30", end_time="09:30")
    assert res.status_code == 409 and res.json()["error"]["code"] == "SCHEDULE_CONFLICT"
    assert [c["id"] for c in res.json()["error"]["conflicts"]] == [a["id"]]
    assert "1교시 전" in res.json()["error"]["message"]

    # 끝과 시작이 맞닿는 구간·다른 요일·비활성 스케줄은 겹침이 아니다
    assert schedule(admin_client, playlist, name="이어서", start_time="09:00", end_time="10:00").status_code == 201
    assert schedule(admin_client, playlist, name="토요일", weekdays=[6], start_time="08:30", end_time="09:30").status_code == 201
    off = schedule(admin_client, playlist, name="꺼둠", weekdays=[1], start_time="08:30", end_time="09:30", is_enabled=False)
    assert off.status_code == 201
    # 꺼 둔 스케줄을 켜거나 옮기면 다시 검사한다
    res = admin_client.patch(f"/api/schedules/{off.json()['id']}", json={"is_enabled": True})
    assert res.status_code == 409 and res.json()["error"]["code"] == "SCHEDULE_CONFLICT"
    res = admin_client.patch(f"/api/schedules/{a['id']}", json={"end_time": "09:30"})
    assert res.status_code == 409


def test_schedule_validation(admin_client, playlist):
    assert schedule(admin_client, playlist, start_time="09:00", end_time="08:00").status_code == 422  # 자정 넘김·역순
    assert schedule(admin_client, playlist, start_time="09:00", end_time="09:00").status_code == 422
    assert schedule(admin_client, playlist, volume=101).status_code == 422
    assert schedule(admin_client, playlist, weekdays=[0]).status_code == 422
    assert schedule(admin_client, playlist, weekdays=[]).status_code == 422
    assert schedule(admin_client, playlist, fade_in_seconds=31).status_code == 422
    assert schedule(admin_client, playlist, playlist_id=999999).status_code == 422
    sid = schedule(admin_client, playlist).json()["id"]
    res = admin_client.patch(f"/api/schedules/{sid}", json={"start_time": "10:00"})
    assert res.status_code == 422 and "자정" in res.json()["error"]["message"]


def test_volume_over_cap_saved_but_limited(admin_client, playlist, client, clock, fake_mpd):
    """상한(40)을 넘는 볼륨은 저장은 되지만 표시·재생 모두 상한으로 제한된다."""
    body = schedule(admin_client, playlist, volume=80).json()
    assert body["volume"] == 80 and body["effective_volume"] == 40 and body["volume_clamped"] is True
    at(client, clock, f"{MON} 08:00:00")
    assert fake_mpd.volume == 40 and max(fake_mpd.volume_log) == 40


# ---- 자동 재생: 경계 시각·페이드 ------------------------------------------------


def test_starts_and_stops_on_the_minute(admin_client, playlist, client, clock, fake_mpd, db):
    """수용 테스트 5: 시작 정각에 페이드 인 재생, 종료 정각에 페이드 아웃 후 정지."""
    clock.set(f"{MON} 07:58:00")
    schedule(admin_client, playlist, fade_in_seconds=3, fade_out_seconds=3)
    fake_mpd.volume = 30

    at(client, clock, f"{MON} 07:59:59")
    assert fake_mpd.state == "stop" and fake_mpd.volume_log == []

    at(client, clock, f"{MON} 08:00:00")
    assert fake_mpd.state == "play" and fake_mpd.pos == 0
    assert [s["file"] for s in fake_mpd.songs] == ["1.mp3", "2.mp3", "3.mp3"] and fake_mpd.repeat == 1
    # 0 에서 시작해 0.2초 간격 15단계로 25까지 올린다
    assert fake_mpd.volume_log[0] == 0 and fake_mpd.volume_log[-1] == 25 and len(fake_mpd.volume_log) == 16
    assert fake_mpd.volume_log == sorted(fake_mpd.volume_log)
    st = admin_client.get("/api/player/status").json()["schedule"]
    assert st["cause"] == "SCHEDULE" and st["current"]["name"] == "1교시 전" and st["can_resume"] is False

    fake_mpd.volume_log.clear()
    at(client, clock, f"{MON} 08:59:59")
    assert fake_mpd.state == "play" and fake_mpd.volume_log == []

    at(client, clock, f"{MON} 09:00:00")
    assert fake_mpd.state == "stop" and fake_mpd.repeat == 0
    fade_out = fake_mpd.volume_log[:-1]
    assert fade_out[-1] == 0 and fade_out == sorted(fade_out, reverse=True) and len(fade_out) == 15
    assert fake_mpd.volume == 25  # 다음 수동 재생이 무음이 되지 않게 볼륨은 되돌려 둔다
    assert events(db) == [("SCHEDULE", "COMPLETED")]  # 시작 때 PLAYING 으로 남긴 기록을 종료 때 닫는다
    assert db.scalar(select(PlaybackEvent)).ended_at is not None
    nxt = admin_client.get("/api/player/status").json()["schedule"]["next"]
    assert nxt["start"].startswith(f"{TUE}T08:00")


def test_stop_at_end_even_after_manual_control(admin_client, playlist, client, clock, fake_mpd):
    """구간 중 수동으로 다른 곡을 틀어도 종료 시각에는 항상 정지한다(10장)."""
    schedule(admin_client, playlist)
    at(client, clock, f"{MON} 08:00:00")
    admin_client.post("/api/player/control", json={"action": "play_at", "position": 2})
    at(client, clock, f"{MON} 09:00:00")
    assert fake_mpd.state == "stop"


def test_playlist_end_stop_policy(admin_client, playlist, client, clock, fake_mpd):
    schedule(admin_client, playlist, on_playlist_end="STOP")
    at(client, clock, f"{MON} 08:00:00")
    assert fake_mpd.repeat == 0


def test_no_ready_tracks_stays_stopped(admin_client, playlist, tracks, client, clock, fake_mpd, db):
    schedule(admin_client, playlist)
    for tid in tracks:
        admin_client.patch(f"/api/admin/tracks/{tid}", json={"rights_status": "DENIED"})
    at(client, clock, f"{MON} 08:00:00")
    assert fake_mpd.state == "stop"
    assert events(db) == [("SCHEDULE", "NO_READY_TRACKS")]
    assert "재생 가능한 곡이 없어" in admin_client.get("/api/player/status").json()["schedule"]["warning"]


def test_deleting_schedule_mid_window_stops(admin_client, playlist, client, clock, fake_mpd):
    sid = schedule(admin_client, playlist).json()["id"]
    at(client, clock, f"{MON} 08:00:00")
    admin_client.delete(f"/api/schedules/{sid}")
    at(client, clock, f"{MON} 08:00:02")
    assert fake_mpd.state == "stop"


def test_fade_steps_cap_and_cancel(fake_mpd):
    player = PlayerController("fake", 0, client_factory=fake_mpd.factory, sleep=lambda _: None)
    fake_mpd.volume = 10
    assert player.fade_to(30, 1.0, max_volume=40) is True
    assert fake_mpd.volume_log == [14, 18, 22, 26, 30]  # 0.2초 × 5단계

    fake_mpd.volume_log.clear()
    player.fade_to(90, 0.4, max_volume=40)  # 상한 초과 목표는 상한까지만
    assert fake_mpd.volume_log == [35, 40]

    # 페이드 중 수동 정지하면 그 자리에서 멈춘다
    calls = []

    def sleep(_):
        calls.append(1)
        if len(calls) == 2:
            player.stop()

    player = PlayerController("fake", 0, client_factory=fake_mpd.factory, sleep=sleep)
    fake_mpd.volume, fake_mpd.state = 0, "play"
    fake_mpd.volume_log.clear()
    assert player.fade_to(40, 1.0, max_volume=40) is False
    assert fake_mpd.volume_log == [8, 16] and fake_mpd.state == "stop"


# ---- 수동 우선권·재개 --------------------------------------------------------


def test_manual_stop_keeps_stopped_until_resume(admin_client, playlist, client, clock, fake_mpd, db):
    schedule(admin_client, playlist)
    at(client, clock, f"{MON} 08:00:00")
    admin_client.post("/api/player/control", json={"action": "stop"})
    at(client, clock, f"{MON} 08:10:00")
    assert fake_mpd.state == "stop"
    st = admin_client.get("/api/player/status").json()["schedule"]
    assert st["cause"] is None and st["can_resume"] is True

    res = admin_client.post("/api/player/control", json={"action": "resume_schedule"})
    assert res.status_code == 200 and res.json()["status"]["schedule"]["cause"] == "SCHEDULE"
    assert fake_mpd.state == "play" and fake_mpd.pos == 0
    res = admin_client.post("/api/player/control", json={"action": "resume_schedule"})
    assert res.json()["error"]["code"] == "SCHEDULE_ALREADY_PLAYING"

    # 다시 수동 정지한 채 구간이 끝나도, 다음 구간에서는 자동으로 재생한다
    admin_client.post("/api/player/control", json={"action": "stop"})
    at(client, clock, f"{MON} 09:00:00")
    at(client, clock, f"{TUE} 08:00:00")
    assert fake_mpd.state == "play"
    assert events(db) == [
        ("SCHEDULE", "MANUAL_STOP"), ("SCHEDULE", "MANUAL_STOP"), ("SCHEDULE", "PLAYING"),
    ]


def test_manual_play_inside_window_shows_manual(admin_client, playlist, client, clock, fake_mpd):
    schedule(admin_client, playlist)
    at(client, clock, f"{MON} 08:00:00")
    admin_client.post("/api/player/control", json={"action": "stop"})
    st = admin_client.post("/api/player/control", json={"action": "play"}).json()["status"]["schedule"]
    assert st["cause"] == "MANUAL" and st["can_resume"] is True


def test_emergency_stop_in_window(admin_client, playlist, client, clock, fake_mpd, db):
    schedule(admin_client, playlist)
    at(client, clock, f"{MON} 08:00:00")
    assert admin_client.post("/api/player/emergency-stop").status_code == 200
    at(client, clock, f"{MON} 08:30:00")
    assert fake_mpd.state == "stop"
    assert events(db) == [("SCHEDULE", "EMERGENCY_STOP")]


def test_resume_schedule_outside_window(admin_client, playlist, clock):
    schedule(admin_client, playlist)
    clock.set(f"{MON} 10:00:00")
    res = admin_client.post("/api/player/control", json={"action": "resume_schedule"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_ACTIVE_SCHEDULE"


def test_manual_play_outside_window_auto_stops(admin_client, client, clock, fake_mpd, db):
    clock.set(f"{MON} 10:00:00")
    admin_client.post("/api/player/control", json={"action": "play"})
    at(client, clock, f"{MON} 10:00:00")
    at(client, clock, f"{MON} 10:29:59")
    assert fake_mpd.state == "play"
    at(client, clock, f"{MON} 10:30:00")
    assert fake_mpd.state == "stop"
    assert events(db) == [("MANUAL", "AUTO_STOPPED")]


def test_manual_play_switches_to_schedule_at_start(admin_client, playlist, client, clock, fake_mpd):
    """수동 재생 중 예약 구간이 시작되면 예약 재생으로 바뀐다."""
    schedule(admin_client, playlist)
    clock.set(f"{MON} 07:50:00")
    admin_client.post("/api/player/control", json={"action": "play"})
    at(client, clock, f"{MON} 07:50:00")
    at(client, clock, f"{MON} 08:00:00")
    assert [s["file"] for s in fake_mpd.songs] == ["1.mp3", "2.mp3", "3.mp3"]
    assert admin_client.get("/api/player/status").json()["schedule"]["cause"] == "SCHEDULE"


# ---- 재시작 재합류 ----------------------------------------------------------


def test_rejoin_restart_mid_window(admin_client, playlist, client, clock, fake_mpd, db):
    """수용 테스트 11: 구간 중 앱 재시작 → RESTART 는 처음부터 페이드 인 재생, 기록이 남는다."""
    sid = schedule(admin_client, playlist).json()["id"]
    # 꺼지기 전 앱이 남긴, 닫히지 못한 기록
    db.add(PlaybackEvent(trigger="SCHEDULE", schedule_id=sid, started_at=clock.set(f"{MON} 08:00:00"), result="PLAYING"))
    db.commit()
    at(client, clock, f"{MON} 08:30:00")  # 이 앱의 첫 tick = 재시작 직후
    assert fake_mpd.state == "play" and fake_mpd.pos == 0 and fake_mpd.volume_log[0] == 0
    assert events(db) == [("SCHEDULE", "INTERRUPTED"), ("REJOIN", "PLAYING")]


def test_rejoin_skipped_when_too_late(admin_client, playlist, client, clock, fake_mpd, db):
    schedule(admin_client, playlist)
    at(client, clock, f"{MON} 08:58:01")  # 남은 시간 1분 59초 < 2분
    assert fake_mpd.state == "stop"
    assert events(db) == [("REJOIN", "TOO_LATE")]
    at(client, clock, f"{MON} 08:59:00")
    assert fake_mpd.state == "stop"


def test_rejoin_skip_policy(admin_client, playlist, client, clock, fake_mpd, db):
    schedule(admin_client, playlist, rejoin_policy="SKIP")
    at(client, clock, f"{MON} 08:30:00")
    assert fake_mpd.state == "stop" and events(db) == [("REJOIN", "SKIPPED")]


def test_restart_right_at_start_is_normal_start(admin_client, playlist, client, clock, db):
    schedule(admin_client, playlist)
    at(client, clock, f"{MON} 08:00:20")  # 시작 직후 30초 안
    assert events(db) == [("SCHEDULE", "PLAYING")]


# ---- 조회·권한 ------------------------------------------------------------


def test_upcoming_today_and_week(admin_client, playlist, as_user, clock):
    schedule(admin_client, playlist)
    schedule(admin_client, playlist, name="점심", weekdays=[1, 3], start_time="13:00", end_time="14:00", volume=35)
    clock.set(f"{MON} 08:30:00")
    body = as_user("student01").get("/api/schedules/upcoming").json()
    assert [(w["name"], w["status"]) for w in body["today"]] == [("1교시 전", "now"), ("점심", "upcoming")]
    assert len(body["week"]) == 7 and body["week"][0]["playlist_name"] == "아침 음악"
    assert body["next"]["name"] == "점심" and body["next"]["start"].startswith(f"{MON}T13:00")


def test_students_cannot_edit_or_control(admin_client, playlist, as_user):
    """수용 테스트 10: 학생은 재생 제어·편성 변경 403(조회는 가능)."""
    c = as_user("student01")
    assert c.post("/api/player/control", json={"action": "resume_schedule"}).status_code == 403
    assert c.post("/api/playlists", json={"name": "x"}).status_code == 403
    assert c.put(f"/api/playlists/{playlist}/items", json={"track_ids": []}).status_code == 403
    assert schedule(c, playlist).status_code == 403
    assert c.get("/api/schedules").status_code == 200
    assert c.get(f"/api/playlists/{playlist}").status_code == 200
