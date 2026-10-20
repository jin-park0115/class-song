import os
import tempfile

# app 모듈을 가져오기 전에 테스트 DB·음원 경로를 지정해야 엔진·설정이 테스트용으로 만들어진다.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://jukebox:jukebox@localhost:5432/jukebox_test"
)
os.environ["MEDIA_DIR"] = tempfile.mkdtemp(prefix="jukebox-media-")
os.environ["SCHEDULER_ENABLED"] = "false"  # 테스트는 시각을 고정하고 runner.tick 을 직접 부른다

from datetime import UTC, datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401
from app.auth.passwords import hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import User  # noqa: E402
from app.services.player import PlayerController  # noqa: E402
from app.services.youtube import VideoMeta  # noqa: E402
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


class FixedClock:
    """스케줄 시각 고정용 시계. clock.set("2026-10-19 08:00")(서울 시각)로 옮긴다."""

    def __init__(self):
        self.now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)  # 월요일 09:00 KST

    def set(self, local: str) -> datetime:
        self.now = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo("Asia/Seoul")).astimezone(UTC)
        return self.now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock():
    return FixedClock()


class FakeYouTube:
    """oEmbed 대신 쓰는 메타데이터 조회. available=False 면 조회 실패를 흉내 낸다."""

    def __init__(self):
        self.available = True
        self.calls: list[str] = []

    def __call__(self, video_id: str) -> VideoMeta | None:
        self.calls.append(video_id)
        if not self.available:
            return None
        return VideoMeta(f"Song {video_id}", "Test Channel", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")


@pytest.fixture
def fake_youtube():
    return FakeYouTube()


@pytest.fixture
def client(fake_mpd, fake_youtube, clock):
    player = PlayerController("fake", 0, client_factory=fake_mpd.factory, sleep=lambda _: None)
    with TestClient(create_app(player, metadata_fetcher=fake_youtube, clock=clock)) as c:
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


@pytest.fixture
def as_user(client, make_user):
    """다른 계정으로 바꿔 로그인한다: as_user("student01") → CSRF 헤더까지 설정."""

    def _as(login_id: str, role: str = "STUDENT", status: str = "ACTIVE"):
        from sqlalchemy import select

        with SessionLocal() as s:
            exists = s.scalar(select(User.id).where(User.login_id == login_id))
        if not exists:
            make_user(login_id, role=role, status=status)
        client.cookies.clear()
        client.headers["X-CSRF-Token"] = login(client, login_id)
        return client

    return _as
