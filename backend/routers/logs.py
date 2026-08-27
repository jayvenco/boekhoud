from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
import os
import pathlib

from backend.models.database import get_db
from backend.routers.auth import require_auth

router = APIRouter(prefix="/logs")
templates = Jinja2Templates(directory="backend/templates")

LOG_DIR = pathlib.Path(os.getenv("LOG_DIR", "/app/logs"))
MAX_TAIL_BYTES = 300_000
MAX_LINES = 1000


def _list_log_files():
    if not LOG_DIR.exists():
        return []
    files = [f for f in LOG_DIR.iterdir() if f.is_file() and f.name.startswith("boekhoud.log")]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def _resolve_log_file(name: str) -> pathlib.Path:
    files = _list_log_files()
    if not files:
        return None
    if name:
        for f in files:
            if f.name == name:
                return f
        raise HTTPException(404)
    return files[0]


def _tail(path: pathlib.Path, max_lines: int = MAX_LINES) -> str:
    if not path or not path.exists():
        return ""
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > MAX_TAIL_BYTES:
            f.seek(size - MAX_TAIL_BYTES)
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


@router.get("", response_class=HTMLResponse)
async def view_logs(request: Request, db: AsyncSession = Depends(get_db), bestand: str = ""):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    files = _list_log_files()
    selected = _resolve_log_file(bestand)
    content = _tail(selected) if selected else ""

    return templates.TemplateResponse(request, "logs.html", {
        "user": user,
        "files": [f.name for f in files],
        "selected": selected.name if selected else "",
        "content": content,
    })


@router.get("/data", response_class=PlainTextResponse)
async def logs_data(request: Request, db: AsyncSession = Depends(get_db), bestand: str = ""):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        raise HTTPException(401)
    selected = _resolve_log_file(bestand)
    return _tail(selected) if selected else ""


@router.get("/export")
async def export_log(request: Request, db: AsyncSession = Depends(get_db), bestand: str = ""):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    selected = _resolve_log_file(bestand)
    if not selected:
        raise HTTPException(404)
    return FileResponse(selected, media_type="text/plain", filename=selected.name)
