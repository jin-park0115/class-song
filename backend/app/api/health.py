from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import SessionLocal

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception:  # noqa: BLE001 - 상태 점검은 실패 사유만 알린다
        checks["db"] = "unavailable"

    player_status = request.app.state.player.status()
    checks["mpd"] = "ok" if player_status["connected"] else "unavailable"

    scheduler = request.app.state.scheduler
    checks["scheduler"] = "disabled" if scheduler is None else "ok" if scheduler.running else "stopped"

    ok = all(v in ("ok", "disabled") for v in checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", "checks": checks})
