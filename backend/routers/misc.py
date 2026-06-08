from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.database import get_db
from backend.models.models import PlannedExpense, CompanySettings, User
from backend.routers.auth import require_auth
from backend.services.ocr import process_receipt
from backend.services.files import save_receipt, UPLOAD_ROOT
from backend.services.auth import hash_password
import shutil
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

router = APIRouter()
templates = Jinja2Templates(directory="backend/templates")

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/app/backups"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))


# ── OCR ────────────────────────────────────────────────
@router.post("/ocr/upload")
async def ocr_upload(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    # Save to temp
    tmp_path = UPLOAD_ROOT / "tmp" / file.filename
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)

    result = await process_receipt(str(tmp_path))
    tmp_path.unlink(missing_ok=True)
    return JSONResponse(result)


# ── Planned Expenses ───────────────────────────────────
@router.get("/gepland", response_class=HTMLResponse)
async def planned_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(PlannedExpense).order_by(PlannedExpense.planned_date))
    planned = result.scalars().all()
    return templates.TemplateResponse("planned.html", {
        "request": request, "planned": planned, "user": user
    })


@router.post("/gepland/nieuw")
async def create_planned(
    request: Request,
    title: str = Form(...),
    amount: Optional[float] = Form(None),
    planned_date: Optional[str] = Form(None),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    d = None
    if planned_date:
        try:
            d = datetime.strptime(planned_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    item = PlannedExpense(title=title, amount=amount, planned_date=d, description=description)
    db.add(item)
    await db.commit()
    return RedirectResponse("/gepland", status_code=302)


@router.post("/gepland/{id}/verwijderen")
async def delete_planned(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(PlannedExpense).where(PlannedExpense.id == id))
    item = result.scalar_one_or_none()
    if item:
        await db.delete(item)
        await db.commit()
    return RedirectResponse("/gepland", status_code=302)


@router.post("/gepland/{id}/status")
async def update_planned_status(
    id: int,
    request: Request,
    status: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(PlannedExpense).where(PlannedExpense.id == id))
    item = result.scalar_one_or_none()
    if item:
        item.status = status
        await db.commit()
    return RedirectResponse("/gepland", status_code=302)


# ── Settings ───────────────────────────────────────────
@router.get("/instellingen", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(CompanySettings))
    settings = result.scalar_one_or_none()
    return templates.TemplateResponse("settings.html", {
        "request": request, "user": user, "settings": settings,
        "success": request.query_params.get("success")
    })


@router.post("/instellingen")
async def update_settings(
    request: Request,
    company_name: str = Form(...),
    logo: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(CompanySettings))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = CompanySettings()
        db.add(settings)

    settings.company_name = company_name

    if logo and logo.filename:
        logo_dir = UPLOAD_ROOT / "logo"
        logo_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(logo.filename).suffix
        logo_path = logo_dir / f"logo{ext}"
        content = await logo.read()
        with open(logo_path, "wb") as f:
            f.write(content)
        settings.logo_path = str(logo_path)

    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


@router.post("/instellingen/wachtwoord")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    from backend.services.auth import verify_password
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if not verify_password(current_password, user.password_hash):
        result = await db.execute(select(CompanySettings))
        settings = result.scalar_one_or_none()
        return templates.TemplateResponse("settings.html", {
            "request": request, "user": user, "settings": settings,
            "pw_error": "Huidig wachtwoord is onjuist."
        })

    user.password_hash = hash_password(new_password)
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


# ── Backup ─────────────────────────────────────────────
@router.post("/backup/maak")
async def create_backup(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    src = DATA_DIR / "boekhoud.db"
    if src.exists():
        dst = BACKUP_DIR / f"boekhoud_{ts}.db"
        shutil.copy2(src, dst)
    return RedirectResponse("/instellingen?success=backup", status_code=302)
