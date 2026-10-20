from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import AuthSession, ControlAuditLog, User
from tests.conftest import PASSWORD, login


def test_login_sets_httponly_cookie_and_returns_csrf(client, make_user):
    make_user("admin01", role="ADMIN")
    res = client.post("/api/auth/login", json={"login_id": "admin01", "password": PASSWORD})
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "ADMIN" and body["csrf_token"]
    cookie = res.headers["set-cookie"]
    assert "jb_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_session_token_stored_only_as_hash(client, make_user, db):
    make_user("admin01", role="ADMIN")
    client.post("/api/auth/login", json={"login_id": "admin01", "password": PASSWORD})
    token = client.cookies["jb_session"]
    row = db.scalar(select(AuthSession))
    assert row.token_hash != token and len(row.token_hash) == 64


def test_wrong_password_and_unknown_user_give_same_error(client, make_user):
    make_user("admin01", role="ADMIN")
    a = client.post("/api/auth/login", json={"login_id": "admin01", "password": "wrong-password"})
    b = client.post("/api/auth/login", json={"login_id": "nobody", "password": "wrong-password"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_five_failures(client, make_user, db):
    make_user("admin01", role="ADMIN")
    for _ in range(4):
        assert client.post("/api/auth/login", json={"login_id": "admin01", "password": "x"}).status_code == 401
    res = client.post("/api/auth/login", json={"login_id": "admin01", "password": "x"})
    assert res.status_code == 423
    # 잠긴 동안에는 올바른 비밀번호도 거부
    res = client.post("/api/auth/login", json={"login_id": "admin01", "password": PASSWORD})
    assert res.status_code == 423
    assert db.scalar(select(ControlAuditLog).where(ControlAuditLog.action == "auth.login_failed"))


def test_lock_expires(client, make_user, db):
    user = make_user("admin01", role="ADMIN", locked_until=datetime.now(UTC) - timedelta(seconds=1))
    assert user.locked_until is not None
    login(client, "admin01")


def test_inactive_accounts_cannot_login(client, make_user):
    make_user("rejected1", status="REJECTED")
    make_user("disabled1", status="DISABLED")
    for login_id in ("rejected1", "disabled1"):
        res = client.post("/api/auth/login", json={"login_id": login_id, "password": PASSWORD})
        assert res.status_code == 403


def test_me_requires_login(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_stable_csrf(client, make_user):
    make_user("admin01", role="ADMIN")
    csrf = login(client, "admin01")
    assert client.get("/api/auth/me").json()["csrf_token"] == csrf
    assert client.get("/api/auth/me").json()["csrf_token"] == csrf  # 탭 여러 개여도 깨지지 않음


def test_logout_requires_csrf_and_revokes_session(client, make_user, db):
    make_user("admin01", role="ADMIN")
    csrf = login(client, "admin01")
    assert client.post("/api/auth/logout").status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert db.scalar(select(AuthSession)) is None
    assert client.get("/api/auth/me").status_code == 401


def test_expired_session_rejected(client, make_user, db):
    make_user("admin01", role="ADMIN")
    login(client, "admin01")
    row = db.scalar(select(AuthSession))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_login_id_is_case_insensitive(client, make_user):
    make_user("admin01", role="ADMIN")
    res = client.post("/api/auth/login", json={"login_id": " Admin01 ", "password": PASSWORD})
    assert res.status_code == 200


def test_successful_login_resets_failures(client, make_user, db):
    make_user("admin01", role="ADMIN")
    client.post("/api/auth/login", json={"login_id": "admin01", "password": "x"})
    login(client, "admin01")
    db.expire_all()
    assert db.scalar(select(User)).failed_login_count == 0
