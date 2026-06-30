from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.database import get_db
from backend.models.backup_database import get_backup_db
from backend.models.models import PlannedExpense, CompanySettings, BackupSettings, AISettings, User, Income, Expense
from backend.routers.auth import require_auth
from backend.services.ocr import process_receipt
from backend.services.files import save_receipts, UPLOAD_ROOT
from backend.services.auth import hash_password
from backend.services.ai_providers import PROVIDERS
from backend.services.crypto import decrypt, mask
from backend.services.invoice_numbering import get_next_invoice_number, get_numbering_settings
from backend.models.models import InvoiceNumberingSettings
from backend.services.i18n import t
from datetime import datetime
from pathlib import Path
from typing import Optional

router = APIRouter()
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t


# ── Factuurnummer ─────────────────────────────────────────
@router.get("/api/volgend-factuurnummer")
async def next_invoice_number(
    request: Request,
    jaar: int = 0,
    type: str = "inkomsten",
    db: AsyncSession = Depends(get_db)
):
    """Geeft het volgende vrije factuurnummer voor het opgegeven boekjaar terug."""
    if not jaar:
        jaar = datetime.now().year

    settings = await get_numbering_settings(db)
    if not settings.auto_enabled:
        return {"invoice_number": None, "auto_enabled": False}

    invoice = await get_next_invoice_number(db, jaar, type, settings)
    return {"invoice_number": invoice, "auto_enabled": True}


def _validate_invoice_template(template: str) -> Optional[str]:
    """Alleen de plaatshouders {year} en {number} zijn toegestaan (geen vrije format-strings).
    Geeft het opgeschoonde template terug, of None als het ongeldig is."""
    import re
    template = template.strip() or "{year}-{number}"
    placeholders = re.findall(r"\{[^{}]*\}", template)
    if "{year}" not in template or any(p not in ("{year}", "{number}") for p in placeholders):
        return None
    return template


@router.post("/instellingen/factuurnummering")
async def update_invoice_numbering_settings(
    request: Request,
    auto_enabled: Optional[str] = Form(None),
    padding: int = Form(3),
    format_template_income: str = Form("I-{year}-{number}"),
    format_template_expense: str = Form("U-{year}-{number}"),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    template_income = _validate_invoice_template(format_template_income)
    template_expense = _validate_invoice_template(format_template_expense)
    if template_income is None or template_expense is None:
        return RedirectResponse("/instellingen?error=factuurformaat", status_code=302)

    settings = await get_numbering_settings(db)
    settings.auto_enabled = auto_enabled == "on"
    settings.padding = max(1, min(10, padding or 3))
    settings.format_template_income = template_income
    settings.format_template_expense = template_expense
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


# ── OCR ────────────────────────────────────────────────
@router.post("/ocr/upload")
async def ocr_upload(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return JSONResponse({"error": "Niet ingelogd"}, status_code=401)

    try:
        filename = file.filename or "upload"
        safe_name = "".join(c for c in filename if c.isalnum() or c in ".-_")
        if not safe_name:
            safe_name = "upload.pdf"

        tmp_dir = UPLOAD_ROOT / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Use unique temp name to avoid collisions
        import uuid
        tmp_id = str(uuid.uuid4())[:8]
        ext = Path(safe_name).suffix or ".pdf"
        tmp_filename = f"ocr_{tmp_id}{ext}"
        tmp_path = tmp_dir / tmp_filename

        file_content = await file.read()
        if not file_content:
            return JSONResponse({"error": "Leeg bestand ontvangen."})

        with open(tmp_path, "wb") as f:
            f.write(file_content)

        result = await process_receipt(str(tmp_path), db)
        # Keep the temp file and return its ID so the form can reference it
        result["_tmp_file"] = tmp_filename
        result["_original_filename"] = safe_name
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": f"Verwerkingsfout: {str(e)}"})


# ── Planned Expenses ───────────────────────────────────
@router.get("/gepland", response_class=HTMLResponse)
async def planned_list(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(PlannedExpense).order_by(PlannedExpense.planned_date))
    planned = result.scalars().all()
    return templates.TemplateResponse(request, "planned.html", {"planned": planned, "user": user})


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
async def settings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    backup_db: AsyncSession = Depends(get_backup_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(CompanySettings))
    settings = result.scalar_one_or_none()
    result = await backup_db.execute(select(BackupSettings))
    backup_settings = result.scalar_one_or_none()

    result = await db.execute(select(AISettings))
    ai_settings = result.scalar_one_or_none()
    ai_provider_models = {pid: [{"id": m.id, "label": m.label} for m in p.models] for pid, p in PROVIDERS.items()}
    ai_key_masks = {}
    if ai_settings:
        if ai_settings.openai_api_key_encrypted:
            ai_key_masks["openai"] = mask(decrypt(ai_settings.openai_api_key_encrypted))
        if ai_settings.anthropic_api_key_encrypted:
            ai_key_masks["anthropic"] = mask(decrypt(ai_settings.anthropic_api_key_encrypted))

    invoice_numbering_settings = await get_numbering_settings(db)

    return templates.TemplateResponse(request, "settings.html", {"user": user, "settings": settings,
        "backup_settings": backup_settings,
        "ai_settings": ai_settings,
        "ai_providers": PROVIDERS,
        "ai_provider_models": ai_provider_models,
        "ai_key_masks": ai_key_masks,
        "invoice_numbering_settings": invoice_numbering_settings,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error")})


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
        return templates.TemplateResponse(request, "settings.html", {"user": user, "settings": settings,
            "pw_error": "Huidig wachtwoord is onjuist."})

    user.password_hash = hash_password(new_password)
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)
