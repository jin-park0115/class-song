import os

# app 모듈을 가져오기 전에 테스트 DB를 지정해야 엔진이 테스트 DB로 만들어진다.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://jukebox:jukebox@localhost:5432/jukebox_test"
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401
from app.auth.passwords import hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import User  # noqa: E402
from app.services.player import PlayerController  # noqa: E402
from tests.fake_mpd import FakeMPD  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
    with engine.begin() as conn:
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def make_user(db):
    def _make(login_id: str, role: str = "STUDENT", status: str = "ACTIVE", **kw) -> User:
        user = User(
            login_id=login_id,
            name=login_id,
            password_hash=hash_password(PASSWORD),
            role=role,
            account_status=status,
            **kw,
        )
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture
def fake_mpd():
    return FakeMPD()


@pytest.fixture
def client(fake_mpd):
    player = PlayerController("fake", 0, client_factory=fake_mpd.factory)
    with TestClient(create_app(player)) as c:
        yield c


def login(client: TestClient, login_id: str, password: str = PASSWORD) -> str:
    """로그인하고 CSRF 토큰을 돌려준다."""
    res = client.post("/api/auth/login", json={"login_id": login_id, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["csrf_token"]


@pytest.fixture
def admin_client(client, make_user):
    make_user("admin01", role="ADMIN")
    csrf = login(client, "admin01")
    client.headers["X-CSRF-Token"] = csrf
    return client
