import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import auth, health, pages, player
from app.config import get_settings
from app.errors import install_error_handlers
from app.services.player import PlayerController


def create_app(player_controller: PlayerController | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
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
    app.include_router(health.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
