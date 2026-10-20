import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import admin_users, auth, health, pages, player, requests, tracks
from app.config import get_settings
from app.errors import install_error_handlers
from app.services import youtube
from app.services.player import PlayerController
from app.services.ratelimit import RateLimiter


def create_app(
    player_controller: PlayerController | None = None,
    metadata_fetcher: youtube.MetadataFetcher | None = None,
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
        yield
        app.state.player.close()

    app = FastAPI(title="AX2 Classroom Jukebox", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(auth.router)
    app.include_router(player.router)
    app.include_router(admin_users.router)
    app.include_router(requests.router)
    app.include_router(tracks.router)
    app.include_router(health.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
