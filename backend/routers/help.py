from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.database import get_db
from backend.routers.auth import require_auth

router = APIRouter(prefix="/help")
templates = Jinja2Templates(directory="backend/templates")


@router.get("", response_class=HTMLResponse)
async def view_help(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "help.html", {"user": user})
