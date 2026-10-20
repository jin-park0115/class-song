from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

router = APIRouter(include_in_schema=False)

# 인증·권한은 페이지가 호출하는 API에서 검증한다. 페이지 스크립트는 /api/auth/me 결과에 따라
# 알맞은 화면(/login, /pending, /password, /me, /admin)으로 이동한다.


def _page(name: str, **ctx):
    def handler(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    return handler


@router.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/login")


router.add_api_route("/login", _page("login.html"), response_class=HTMLResponse)
router.add_api_route("/signup", _page("signup.html"), response_class=HTMLResponse)
router.add_api_route("/pending", _page("pending.html"), response_class=HTMLResponse)
router.add_api_route("/password", _page("password.html"), response_class=HTMLResponse)
router.add_api_route("/me", _page("me.html"), response_class=HTMLResponse)
router.add_api_route("/admin/manage", _page("manage.html"), response_class=HTMLResponse)


@router.get("/admin", response_class=HTMLResponse)
def admin_console(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"monitor_stream_url": "/api/player/monitor" if get_settings().mpd_monitor_url else None},
    )
