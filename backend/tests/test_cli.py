import io

import pytest
from sqlalchemy import select

from app import cli
from app.auth.passwords import verify_password
from app.models import AuthSession, ControlAuditLog, User
from tests.conftest import login

GOOD = "admin-password-123"


def run(monkeypatch, *argv, password=GOOD):
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    return cli.main(list(argv))


def test_create_admin(monkeypatch, db):
    assert run(monkeypatch, "create", "--login-id", "Admin01", "--name", "운영자", "--password-stdin") == 0
    user = db.scalar(select(User))
    assert user.login_id == "admin01" and user.role == "ADMIN" and user.account_status == "ACTIVE"
    assert verify_password(user.password_hash, GOOD)
    assert db.scalar(select(ControlAuditLog).where(ControlAuditLog.action == "cli.admin_create"))


def test_create_rejects_weak_password(monkeypatch, db, capsys):
    assert run(monkeypatch, "create", "--login-id", "admin01", "--password-stdin", password="short") == 1
    assert "최소 10자" in capsys.readouterr().err
    assert db.scalar(select(User)) is None


def test_create_rejects_password_containing_login_id(monkeypatch, db):
    assert run(monkeypatch, "create", "--login-id", "admin01", "--password-stdin", password="xxadmin01xxyy") == 1


def test_create_duplicate(monkeypatch, make_user):
    make_user("admin01", role="ADMIN")
    assert run(monkeypatch, "create", "--login-id", "admin01", "--password-stdin") == 1


@pytest.mark.parametrize("bad", ["ab", "has space", "UPPER!", "a" * 21])
def test_create_invalid_login_id(monkeypatch, bad):
    assert run(monkeypatch, "create", "--login-id", bad, "--password-stdin") == 1


def test_promote_and_revoke(monkeypatch, make_user, db, client):
    make_user("admin01", role="ADMIN")
    make_user("student01")
    login(client, "student01")
    assert run(monkeypatch, "promote", "--login-id", "student01") == 0
    db.expire_all()
    student = db.scalar(select(User).where(User.login_id == "student01"))
    assert student.role == "ADMIN"
    assert db.scalar(select(AuthSession).where(AuthSession.user_id == student.id)) is None
    assert run(monkeypatch, "revoke", "--login-id", "student01") == 0
    db.expire_all()
    assert db.get(User, student.id).role == "STUDENT"


def test_revoke_last_admin_needs_force(monkeypatch, make_user):
    make_user("admin01", role="ADMIN")
    assert run(monkeypatch, "revoke", "--login-id", "admin01") == 1
    assert run(monkeypatch, "revoke", "--login-id", "admin01", "--force") == 0


def test_reset_password_admin_only(monkeypatch, make_user, db):
    make_user("admin01", role="ADMIN", failed_login_count=3)
    make_user("student01")
    assert run(monkeypatch, "reset-password", "--login-id", "student01", "--password-stdin") == 1
    assert run(monkeypatch, "reset-password", "--login-id", "admin01", "--password-stdin", password="brand-new-pass-1") == 0
    db.expire_all()
    admin = db.scalar(select(User).where(User.login_id == "admin01"))
    assert verify_password(admin.password_hash, "brand-new-pass-1") and admin.failed_login_count == 0
