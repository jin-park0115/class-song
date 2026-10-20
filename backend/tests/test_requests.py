import pytest
from sqlalchemy import select

from app.models import SystemSetting, Track
from app.services.youtube import parse_video_id

VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"


@pytest.mark.parametrize(
    "url",
    [
        URL,
        f"https://youtu.be/{VID}",
        f"youtu.be/{VID}?si=abc",
        f"https://m.youtube.com/watch?v={VID}&t=30",
        f"https://music.youtube.com/watch?v={VID}&list=RD",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/live/{VID}?feature=share",
    ],
)
def test_parse_video_id_variants(url):
    assert parse_video_id(url) == VID


@pytest.mark.parametrize(
    "url",
    ["", "not a url", "https://vimeo.com/123", f"https://evil.com/watch?v={VID}", "https://youtu.be/short",
     "ftp://youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=bad!chars!!"],
)
def test_parse_video_id_rejects(url):
    assert parse_video_id(url) is None


def test_preview(as_user):
    c = as_user("student01")
    body = c.post("/api/youtube/preview", json={"url": f"https://youtu.be/{VID}"}).json()
    assert body["video_id"] == VID and body["source_url"] == URL
    assert body["metadata_found"] and body["title"] == f"Song {VID}" and body["channel"] == "Test Channel"
    assert body["track"] is None and body["my_request"] is None
    assert c.post("/api/youtube/preview", json={"url": "https://vimeo.com/1"}).json()["error"]["code"] == "INVALID_YOUTUBE_URL"


def test_create_request_pending(as_user, db):
    """수용 테스트 7: 학생이 URL 신청 → PENDING."""
    c = as_user("student01")
    res = c.post("/api/requests", json={"url": URL, "comment": "아침에 듣고 싶어요"})
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "PENDING" and body["track"]["title"] == f"Song {VID}"
    assert body["track"]["media_status"] == "NOT_AVAILABLE"
    mine = c.get("/api/requests/me").json()
    assert [r["status"] for r in mine["items"]] == ["PENDING"] and mine["pending"] == 1


def test_duplicate_request_blocked_but_track_shared(as_user, db, fake_youtube):
    c = as_user("student01")
    c.post("/api/requests", json={"url": URL})
    res = c.post("/api/requests", json={"url": f"https://youtu.be/{VID}"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "DUPLICATE_REQUEST"
    preview = c.post("/api/youtube/preview", json={"url": URL}).json()
    assert preview["my_request"]["status"] == "PENDING"

    other = as_user("student02")
    assert other.post("/api/requests", json={"url": URL}).status_code == 201
    assert len(db.scalars(select(Track)).all()) == 1  # 요구사항 10장: video ID당 트랙 하나
    assert fake_youtube.calls.count(VID) == 1  # 처음 한 번만 조회하고 이후에는 저장된 트랙을 재사용


def test_metadata_failure_requires_manual_title(as_user, fake_youtube):
    fake_youtube.available = False
    c = as_user("student01")
    preview = c.post("/api/youtube/preview", json={"url": URL}).json()
    assert preview["metadata_found"] is False and preview["thumbnail_url"].endswith("/hqdefault.jpg")
    res = c.post("/api/requests", json={"url": URL})
    assert res.status_code == 422 and res.json()["error"]["code"] == "TITLE_REQUIRED"
    res = c.post("/api/requests", json={"url": URL, "title": "직접 쓴 곡명"})
    assert res.status_code == 201 and res.json()["track"]["title"] == "직접 쓴 곡명"


def test_pending_limit(as_user, db):
    db.add(SystemSetting(key="max_pending_requests_per_user", value_json=2))
    db.commit()
    c = as_user("student01")
    for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb"):
        assert c.post("/api/requests", json={"url": f"https://youtu.be/{vid}"}).status_code == 201
    res = c.post("/api/requests", json={"url": "https://youtu.be/ccccccccccc"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "REQUEST_LIMIT"


def test_cancel_rules(as_user):
    """수용 테스트 8: PENDING 취소 → CANCELLED, APPROVED는 취소 불가(409)."""
    c = as_user("student01")
    a = c.post("/api/requests", json={"url": "https://youtu.be/aaaaaaaaaaa"}).json()["id"]
    b = c.post("/api/requests", json={"url": "https://youtu.be/bbbbbbbbbbb"}).json()["id"]
    assert c.delete(f"/api/requests/{a}").json()["status"] == "CANCELLED"

    as_user("admin01", role="ADMIN").post(f"/api/admin/requests/{b}/review", json={"decision": "APPROVE"})
    c = as_user("student01")
    res = c.delete(f"/api/requests/{b}")
    assert res.status_code == 409 and res.json()["error"]["code"] == "NOT_CANCELLABLE"
    # 취소한 곡은 다시 신청 가능
    assert c.post("/api/requests", json={"url": "https://youtu.be/aaaaaaaaaaa"}).status_code == 201
    # 남의 신청은 보이지도 않는다
    assert as_user("student02").delete(f"/api/requests/{b}").status_code == 404


def test_admin_review_approve(as_user):
    """수용 테스트 9: 승인 → APPROVED 후 음원을 받기 시작한다. 받지 못하면 FAILED(READY 아님)."""
    student = as_user("student01")
    rid = student.post("/api/requests", json={"url": URL, "comment": "신나요"}).json()["id"]
    as_user("student02").post("/api/requests", json={"url": URL})

    admin = as_user("admin01", role="ADMIN")
    pending = admin.get("/api/admin/requests").json()["items"]
    assert len(pending) == 2
    first = pending[0]
    assert first["requester"]["login_id"] == "student01" and first["comment"] == "신나요"
    assert first["same_track_requests"] == 2

    res = admin.post(f"/api/admin/requests/{rid}/review", json={"decision": "APPROVE"})
    assert res.json()["status"] == "APPROVED"
    assert res.json()["track"]["media_status"] == "PROCESSING"
    assert admin.post(f"/api/admin/requests/{rid}/review", json={"decision": "APPROVE"}).status_code == 409

    tracks = as_user("student01").get("/api/tracks").json()["items"]
    assert len(tracks) == 1 and tracks[0]["media_status"] == "FAILED"  # 테스트 다운로더는 오프라인
    assert as_user("student01").get("/api/tracks", params={"status": "ready"}).json()["items"] == []


def test_admin_review_reject_requires_reason(as_user):
    rid = as_user("student01").post("/api/requests", json={"url": URL}).json()["id"]
    admin = as_user("admin01", role="ADMIN")
    assert admin.post(f"/api/admin/requests/{rid}/review", json={"decision": "REJECT"}).status_code == 422
    other = {"decision": "REJECT", "reject_reason": "OTHER"}
    assert admin.post(f"/api/admin/requests/{rid}/review", json=other).status_code == 422
    res = admin.post(
        f"/api/admin/requests/{rid}/review",
        json={"decision": "REJECT", "reject_reason": "INAPPROPRIATE", "reject_note": "가사"},
    )
    assert res.json()["status"] == "REJECTED" and res.json()["reject_reason"] == "INAPPROPRIATE"
    mine = as_user("student01").get("/api/requests/me").json()["items"][0]
    assert mine["reject_reason"] == "INAPPROPRIATE" and mine["reject_note"] == "가사"
    # 반려된 곡은 다시 신청할 수 있다
    assert as_user("student01").post("/api/requests", json={"url": URL}).status_code == 201


def test_blocked_track_cannot_be_requested(as_user, db):
    as_user("student01").post("/api/requests", json={"url": URL})
    track = db.scalar(select(Track))
    track.rights_status, track.media_status = "DENIED", "BLOCKED"
    db.commit()
    c = as_user("student02")
    assert c.post("/api/youtube/preview", json={"url": URL}).json()["blocked"] is True
    res = c.post("/api/requests", json={"url": URL})
    assert res.status_code == 409 and res.json()["error"]["code"] == "TRACK_BLOCKED"


def test_students_cannot_review(as_user):
    rid = as_user("student01").post("/api/requests", json={"url": URL}).json()["id"]
    c = as_user("student02")
    assert c.get("/api/admin/requests").status_code == 403
    assert c.post(f"/api/admin/requests/{rid}/review", json={"decision": "APPROVE"}).status_code == 403


def test_pending_account_cannot_request(as_user):
    c = as_user("newbie01", status="PENDING_APPROVAL")
    assert c.post("/api/requests", json={"url": URL}).status_code == 403
