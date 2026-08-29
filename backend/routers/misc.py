from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.database import get_db
from backend.models.backup_database import get_backup_db
from backend.models.models import PlannedExpense, CompanySettings, BackupSettings, AISettings, User, Income, Expense, IncomeCategory, ExpenseCategory, HourCategory, InvoiceNumberingSettings, APISettings
from backend.routers.auth import require_auth
from backend.services.ocr import process_receipt
from backend.services.files import save_receipts, UPLOAD_ROOT
from backend.services.auth import hash_password
from backend.services.ai_providers import PROVIDERS
from backend.services.crypto import decrypt, mask
from backend.services.invoice_numbering import get_next_invoice_number, get_numbering_settings
from backend.services.i18n import t
from datetime import datetime
from pathlib import Path
from typing import Optional
import re
import unicodedata

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

        # Duplicaatdetectie: waarschuw (blokkeer niet) als deze bon al bestaat.
        from backend.services.duplicates import compute_hash, find_duplicate
        warning = await find_duplicate(
            db,
            file_hash=compute_hash(file_content),
            invoice_number=result.get("invoice_number"),
            amount=result.get("amount"),
            date_str=result.get("date"),
        )
        if warning:
            result["_duplicate_warning"] = warning
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


# ── Inkomsten-categorieën ─────────────────────────────

def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", "_", text)
    return text or "categorie"


async def _unique_slug(db: AsyncSession, base_slug: str, exclude_id: int = None) -> str:
    slug = base_slug
    i = 2
    while True:
        q = select(IncomeCategory).where(IncomeCategory.slug == slug)
        if exclude_id:
            q = q.where(IncomeCategory.id != exclude_id)
        res = await db.execute(q)
        if not res.scalar_one_or_none():
            return slug
        slug = f"{base_slug}_{i}"
        i += 1


def _valid_email(email: str) -> bool:
    if not email.strip():
        return True
    return bool(re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email.strip()))


def _valid_phone(phone: str) -> bool:
    if not phone.strip():
        return True
    return bool(re.match(r"^[\+\d\s\(\)\-\.]{7,20}$", phone.strip()))


@router.post("/instellingen/inkomsten-categorieen/nieuw")
async def create_income_category(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    contact_firstname: str = Form(""),
    contact_lastname: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    name = name.strip()
    if not name:
        return RedirectResponse("/instellingen?cat_error=naam_verplicht", status_code=302)
    if not _valid_email(email):
        return RedirectResponse("/instellingen?cat_error=ongeldig_email", status_code=302)
    if not _valid_phone(phone):
        return RedirectResponse("/instellingen?cat_error=ongeldig_telefoon", status_code=302)

    existing = await db.execute(select(IncomeCategory).where(IncomeCategory.name == name))
    if existing.scalar_one_or_none():
        return RedirectResponse("/instellingen?cat_error=naam_bestaat", status_code=302)

    slug = await _unique_slug(db, _slugify(name))
    db.add(IncomeCategory(
        name=name,
        slug=slug,
        description=description.strip() or None,
        contact_firstname=contact_firstname.strip() or None,
        contact_lastname=contact_lastname.strip() or None,
        phone=phone.strip() or None,
        email=email.strip() or None,
    ))
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


@router.post("/instellingen/inkomsten-categorieen/{id}/bewerken")
async def update_income_category(
    id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    contact_firstname: str = Form(""),
    contact_lastname: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    cat = await db.get(IncomeCategory, id)
    if not cat:
        raise HTTPException(404)

    name = name.strip()
    if not name:
        return RedirectResponse(f"/instellingen?cat_error=naam_verplicht&cat_id={id}", status_code=302)
    if not _valid_email(email):
        return RedirectResponse(f"/instellingen?cat_error=ongeldig_email&cat_id={id}", status_code=302)
    if not _valid_phone(phone):
        return RedirectResponse(f"/instellingen?cat_error=ongeldig_telefoon&cat_id={id}", status_code=302)

    existing = await db.execute(select(IncomeCategory).where(IncomeCategory.name == name, IncomeCategory.id != id))
    if existing.scalar_one_or_none():
        return RedirectResponse(f"/instellingen?cat_error=naam_bestaat&cat_id={id}", status_code=302)

    if cat.name != name:
        cat.slug = await _unique_slug(db, _slugify(name), exclude_id=id)
    cat.name = name
    cat.description = description.strip() or None
    cat.contact_firstname = contact_firstname.strip() or None
    cat.contact_lastname = contact_lastname.strip() or None
    cat.phone = phone.strip() or None
    cat.email = email.strip() or None
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


@router.post("/instellingen/inkomsten-categorieen/{id}/verwijderen")
async def delete_income_category(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    cat = await db.get(IncomeCategory, id)
    if not cat:
        raise HTTPException(404)

    linked = await db.execute(select(Income).where(Income.category_id == id))
    if linked.scalar_one_or_none():
        return RedirectResponse("/instellingen?cat_error=heeft_inkomsten", status_code=302)

    await db.delete(cat)
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


# ── Uitgaven-categorieën ──────────────────────────────

async def _unique_expense_slug(db: AsyncSession, base_slug: str, exclude_id: int = None) -> str:
    slug = base_slug
    i = 2
    while True:
        q = select(ExpenseCategory).where(ExpenseCategory.slug == slug)
        if exclude_id:
            q = q.where(ExpenseCategory.id != exclude_id)
        res = await db.execute(q)
        if not res.scalar_one_or_none():
            return slug
        slug = f"{base_slug}_{i}"
        i += 1


@router.post("/instellingen/uitgaven-categorieen/nieuw")
async def create_expense_category(
    request: Request,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    name = name.strip()
    if not name:
        return RedirectResponse("/instellingen?expcat_error=naam_verplicht", status_code=302)

    existing = await db.execute(select(ExpenseCategory).where(ExpenseCategory.name == name))
    if existing.scalar_one_or_none():
        return RedirectResponse("/instellingen?expcat_error=naam_bestaat", status_code=302)

    slug = await _unique_expense_slug(db, _slugify(name))
    db.add(ExpenseCategory(name=name, slug=slug))
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


@router.post("/instellingen/uitgaven-categorieen/{id}/bewerken")
async def update_expense_category(
    id: int,
    request: Request,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    cat = await db.get(ExpenseCategory, id)
    if not cat:
        raise HTTPException(404)

    name = name.strip()
    if not name:
        return RedirectResponse(f"/instellingen?expcat_error=naam_verplicht&expcat_id={id}", status_code=302)

    existing = await db.execute(select(ExpenseCategory).where(ExpenseCategory.name == name, ExpenseCategory.id != id))
    if existing.scalar_one_or_none():
        return RedirectResponse(f"/instellingen?expcat_error=naam_bestaat&expcat_id={id}", status_code=302)

    if cat.name != name:
        cat.slug = await _unique_expense_slug(db, _slugify(name), exclude_id=id)
    cat.name = name
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


@router.post("/instellingen/uitgaven-categorieen/{id}/verwijderen")
async def delete_expense_category(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    cat = await db.get(ExpenseCategory, id)
    if not cat:
        raise HTTPException(404)

    linked = await db.execute(select(Expense).where(Expense.category_id == id))
    if linked.scalar_one_or_none():
        return RedirectResponse("/instellingen?expcat_error=heeft_uitgaven", status_code=302)

    await db.delete(cat)
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


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

    result = await db.execute(select(IncomeCategory).order_by(IncomeCategory.name))
    income_categories = result.scalars().all()

    result = await db.execute(select(ExpenseCategory).order_by(ExpenseCategory.name))
    expense_categories = result.scalars().all()

    result = await db.execute(select(HourCategory).order_by(HourCategory.name))
    hour_categories = result.scalars().all()

    result = await db.execute(select(APISettings))
    api_settings = result.scalar_one_or_none()

    return templates.TemplateResponse(request, "settings.html", {"user": user, "settings": settings,
        "backup_settings": backup_settings,
        "ai_settings": ai_settings,
        "ai_providers": PROVIDERS,
        "ai_provider_models": ai_provider_models,
        "ai_key_masks": ai_key_masks,
        "invoice_numbering_settings": invoice_numbering_settings,
        "income_categories": income_categories,
        "expense_categories": expense_categories,
        "hour_categories": hour_categories,
        "api_settings": api_settings,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
        "cat_error": request.query_params.get("cat_error"),
        "cat_id": request.query_params.get("cat_id"),
        "expcat_error": request.query_params.get("expcat_error"),
        "expcat_id": request.query_params.get("expcat_id"),
        "urencat_error": request.query_params.get("urencat_error"),
        "urencat_id": request.query_params.get("urencat_id")})


@router.post("/instellingen")
async def update_settings(
    request: Request,
    company_name: str = Form(...),
    default_km_rate: float = Form(0.23),
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
    settings.default_km_rate = default_km_rate

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


@router.post("/instellingen/api/genereer")
async def generate_api_key(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    import secrets
    result = await db.execute(select(APISettings))
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = APISettings()
        db.add(cfg)
    cfg.api_key = secrets.token_urlsafe(32)
    await db.commit()
    return RedirectResponse("/instellingen?success=1#api-instellingen", status_code=302)


@router.post("/instellingen/api/toggle")
async def toggle_api(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(APISettings))
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = APISettings()
        db.add(cfg)
    cfg.enabled = not cfg.enabled
    await db.commit()
    return RedirectResponse("/instellingen?success=1#api-instellingen", status_code=302)


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
