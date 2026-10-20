import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import admin_users, auth, health, pages, player, playlists, requests, schedules, tracks
from app.config import get_settings
from app.db import SessionLocal
from app.errors import install_error_handlers
from app.services import youtube
from app.services.player import PlayerController
from app.services.ratelimit import RateLimiter
from app.services.scheduler import ScheduleRunner, utcnow

log = logging.getLogger("jukebox.app")
TICK_SECONDS = 2  # 스케줄 점검 간격. 정시 시작이 이만큼 늦을 수 있다


def create_app(
    player_controller: PlayerController | None = None,
    metadata_fetcher: youtube.MetadataFetcher | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.metadata_fetcher = metadata_fetcher or (
            youtube.fetch_oembed if settings.youtube_metadata_enabled else youtube.disabled_fetcher
        )
        # IP당 로그인 30회/5분, 회원가입 10회/시간
        app.state.login_limiter = RateLimiter(30, 300)
        app.state.signup_limiter = RateLimiter(10, 3600)
        app.state.player = player_controller or PlayerController(
            settings.mpd_host,
            settings.mpd_port,
            settings.mpd_password,
            settings.mpd_timeout_seconds,
        )
        app.state.runner = ScheduleRunner(app.state.player, SessionLocal, clock)
        # 스케줄러는 앱 프로세스 안에 하나만 둔다(uvicorn 워커 1개 전제). 테스트는 끄고 tick 을 직접 부른다.
        app.state.scheduler = None
        if settings.scheduler_enabled:
            scheduler = BackgroundScheduler(timezone="UTC")
            scheduler.add_job(
                app.state.runner.safe_tick, "interval", seconds=TICK_SECONDS, id="schedule-tick",
                max_instances=1, coalesce=True, next_run_time=utcnow(),  # 켜자마자 첫 tick(재합류 판단)
            )
            scheduler.start()
            app.state.scheduler = scheduler
            log.info("scheduler started tick=%ss", TICK_SECONDS)
        yield
        if app.state.scheduler:
            app.state.scheduler.shutdown(wait=False)
        app.state.player.close()

    app = FastAPI(title="AX2 Classroom Jukebox", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(auth.router)
    app.include_router(player.router)
    app.include_router(admin_users.router)
    app.include_router(requests.router)
    app.include_router(tracks.router)
    app.include_router(playlists.router)
    app.include_router(schedules.router)
    app.include_router(health.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# 2초마다 남는 실행 로그와, 페이드(수 초) 동안 tick 을 건너뛰었다는 정상 경고는 끈다. 작업 오류는 ERROR 로 남는다.
logging.getLogger("apscheduler").setLevel(logging.ERROR)
app = create_app()
