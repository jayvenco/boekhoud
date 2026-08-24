from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from backend.models.database import get_db
from backend.models.backup_database import get_backup_db
from backend.models.models import BackupSettings
from backend.routers.auth import require_auth
from backend.services import backup as backup_service
from backend.services.i18n import t

router = APIRouter()
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t


@router.get("/backups", response_class=HTMLResponse)
async def backups_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    backup_db: AsyncSession = Depends(get_backup_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    backups = await backup_service.list_backups(backup_db)
    return templates.TemplateResponse(request, "backups.html", {
        "backups": backups,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@router.post("/backups/nu")
async def create_manual_backup(
    request: Request,
    db: AsyncSession = Depends(get_db),
    backup_db: AsyncSession = Depends(get_backup_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    record = await backup_service.create_backup(backup_db, "handmatig")
    if record.status == "voltooid":
        return RedirectResponse("/backups?success=1", status_code=302)
    return RedirectResponse("/backups?error=" + str(record.note or "Back-up mislukt"), status_code=302)


@router.post("/backups/{id}/herstellen")
async def restore_backup_route(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    backup_db: AsyncSession = Depends(get_backup_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    try:
        await backup_service.restore_backup(backup_db, id)
        return RedirectResponse("/backups?success=restore", status_code=302)
    except backup_service.BackupError as e:
        return RedirectResponse(f"/backups?error={e}", status_code=302)


@router.post("/instellingen/backups")
async def update_backup_settings(
    request: Request,
    frequency: str = Form("wekelijks"),
    retention_type: str = Form("10"),
    retention_custom_count: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
    backup_db: AsyncSession = Depends(get_backup_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    valid_frequencies = {"handmatig", "elk_uur", "dagelijks", "wekelijks", "maandelijks"}
    valid_retentions = {"5", "10", "25", "50", "onbeperkt", "aangepast"}
    if frequency not in valid_frequencies:
        frequency = "wekelijks"
    if retention_type not in valid_retentions:
        retention_type = "10"

    result = await backup_db.execute(select(BackupSettings))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = BackupSettings()
        backup_db.add(settings)

    settings.frequency = frequency
    settings.retention_type = retention_type
    settings.retention_custom_count = retention_custom_count if retention_type == "aangepast" else None
    await backup_db.commit()

    backup_service.configure_backup_job(request.app.state.scheduler, frequency)

    return RedirectResponse("/instellingen?success=1", status_code=302)
