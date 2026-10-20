from sqlalchemy import select

from app.models import AuthSession, SystemSetting, User
from tests.conftest import PASSWORD, login

GOOD = {"login_id": "newbie01", "name": "새학생", "password": "strong-pass-2026", "signup_note": "AX2 3번"}


def signup(client, **over):
    return client.post("/api/auth/signup", json=GOOD | over)


def test_signup_creates_pending_and_logs_in(client, db):
    res = signup(client)
    assert res.status_code == 201
    body = res.json()
    assert body["account_status"] == "PENDING_APPROVAL" and body["role"] == "STUDENT" and body["csrf_token"]
    assert client.get("/api/auth/me").json()["account_status"] == "PENDING_APPROVAL"
    st = client.get("/api/player/status")
    assert st.status_code == 403 and st.json()["error"]["code"] == "ACCOUNT_PENDING"
    user = db.scalar(select(User))
    assert user.signup_note == "AX2 3번" and user.password_hash != GOOD["password"]


def test_signup_validation(client, make_user):
    make_user("taken01")
    assert signup(client, login_id="taken01").json()["error"]["code"] == "LOGIN_ID_TAKEN"
    assert signup(client, login_id="Bad Id!").json()["error"]["code"] == "INVALID_LOGIN_ID"
    assert signup(client, password="short").json()["error"]["code"] == "WEAK_PASSWORD"
    assert signup(client, password="xxnewbie01xx").json()["error"]["code"] == "WEAK_PASSWORD"
    assert signup(client, name="   ").json()["error"]["code"] == "INVALID_NAME"


def test_signup_login_id_normalized(client, db):
    assert signup(client, login_id=" NewBie01 ").status_code == 201
    assert db.scalar(select(User.login_id)) == "newbie01"


def test_signup_closed(client, db):
    db.add(SystemSetting(key="signup_enabled", value_json=False))
    db.commit()
    res = signup(client)
    assert res.status_code == 403 and res.json()["error"]["code"] == "SIGNUP_CLOSED"


def test_signup_queue_full(client, db):
    db.add(SystemSetting(key="max_pending_signups", value_json=0))
    db.commit()
    assert signup(client).json()["error"]["code"] == "SIGNUP_QUEUE_FULL"


def test_signup_rate_limited(client):
    codes = [signup(client, login_id=f"user{i:04d}").status_code for i in range(11)]
    assert codes[:10] == [201] * 10 and codes[10] == 429


def _pending_id(db, login_id="newbie01"):
    db.expire_all()
    return db.scalar(select(User.id).where(User.login_id == login_id))


def test_admin_approves_signup(client, as_user, db):
    signup(client)
    uid = _pending_id(db)
    admin = as_user("admin01", role="ADMIN")
    pending = admin.get("/api/admin/users", params={"status": "PENDING_APPROVAL"}).json()["items"]
    assert [u["login_id"] for u in pending] == ["newbie01"] and pending[0]["signup_note"] == "AX2 3번"
    assert admin.post(f"/api/admin/users/{uid}/approve").json()["account_status"] == "ACTIVE"
    client.cookies.clear()
    login(client, "newbie01", GOOD["password"])
    assert client.get("/api/player/status").status_code == 200


def test_approval_takes_effect_on_existing_session(client, as_user, db):
    """승인 대기 화면을 열어 둔 채 기다리다가 승인되면 다시 로그인하지 않아도 된다."""
    signup(client)
    uid = _pending_id(db)
    pending_cookies = dict(client.cookies)
    as_user("admin01", role="ADMIN").post(f"/api/admin/users/{uid}/approve")
    client.cookies.clear()
    client.cookies.update(pending_cookies)
    assert client.get("/api/player/status").status_code == 200


def test_reject_blocks_login_and_can_be_approved_later(client, as_user, db):
    signup(client)
    uid = _pending_id(db)
    admin = as_user("admin01", role="ADMIN")
    res = admin.post(f"/api/admin/users/{uid}/reject", json={"note": "AX2 학생 아님"})
    assert res.json()["account_status"] == "REJECTED"
    assert db.scalar(select(AuthSession).where(AuthSession.user_id == uid)) is None
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"login_id": "newbie01", "password": GOOD["password"]}).status_code == 403
    admin = as_user("admin01")
    assert admin.post(f"/api/admin/users/{uid}/approve").json()["account_status"] == "ACTIVE"


def test_invalid_transitions(client, as_user, make_user, db):
    make_user("active01")
    uid = _pending_id(db, "active01")
    admin = as_user("admin01", role="ADMIN")
    assert admin.post(f"/api/admin/users/{uid}/reject", json={}).status_code == 409
    assert admin.post(f"/api/admin/users/{uid}/enable").status_code == 409
    admin_id = _pending_id(db, "admin01")
    res = admin.post(f"/api/admin/users/{admin_id}/disable")
    assert res.status_code == 409 and res.json()["error"]["code"] == "ADMIN_MANAGED_BY_CLI"
    assert admin.post("/api/admin/users/99999/approve").status_code == 404


def test_disable_revokes_sessions(client, as_user, db):
    as_user("student01")
    uid = _pending_id(db, "student01")
    as_user("admin01", role="ADMIN").post(f"/api/admin/users/{uid}/disable")
    assert db.scalar(select(AuthSession).where(AuthSession.user_id == uid)) is None


def test_reset_password_flow(client, as_user, db):
    as_user("student01")
    uid = _pending_id(db, "student01")
    temp = as_user("admin01", role="ADMIN").post(f"/api/admin/users/{uid}/reset-password").json()["temporary_password"]
    assert len(temp) == 12

    client.cookies.clear()
    assert client.post("/api/auth/login", json={"login_id": "student01", "password": PASSWORD}).status_code == 401
    csrf = login(client, "student01", temp)
    client.headers["X-CSRF-Token"] = csrf
    assert client.get("/api/auth/me").json()["must_change_password"] is True
    st = client.get("/api/player/status")
    assert st.status_code == 403 and st.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"

    bad = client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "fresh-pass-2026"})
    assert bad.status_code == 403
    same = client.post("/api/auth/password", json={"current_password": temp, "new_password": temp})
    assert same.status_code == 422
    res = client.post("/api/auth/password", json={"current_password": temp, "new_password": "fresh-pass-2026"})
    assert res.status_code == 200 and res.json()["must_change_password"] is False
    client.headers["X-CSRF-Token"] = res.json()["csrf_token"]
    assert client.get("/api/player/status").status_code == 200


def test_students_cannot_manage_users(as_user):
    student = as_user("student01")
    assert student.get("/api/admin/users").status_code == 403
    assert student.post("/api/admin/users/1/approve").status_code == 403
