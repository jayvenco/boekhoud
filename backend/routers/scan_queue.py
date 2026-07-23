from fastapi import APIRouter, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from pathlib import Path
import uuid
import shutil
import os

from backend.models.database import get_db
from backend.models.models import (
    ScanQueue, Income, Expense, IncomeCategory, ExpenseCategory,
    IncomeReceipt, ExpenseReceipt,
)
from backend.routers.auth import require_auth
from backend.services.ocr import process_receipt
from backend.services.files import UPLOAD_ROOT, ALLOWED_EXTENSIONS
from backend.services.invoice_numbering import get_numbering_settings, get_next_invoice_number
from backend.services.fiscal_year import is_year_locked
from backend.services.i18n import t
from backend.routers.incomes import RECEIVED_VIA_OPTIONS, parse_date as parse_date_inc

router = APIRouter(prefix="/scan-wachtrij")
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t

SCAN_DIR = UPLOAD_ROOT / "scan_queue"


def _scan_path(filename: str) -> Path:
    return SCAN_DIR / filename


# ── Overzicht + upload formulier ─────────────────────────────

@router.get("", response_class=HTMLResponse)
async def scan_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    items = (await db.execute(
        select(ScanQueue).order_by(ScanQueue.created_at.desc())
    )).scalars().all()

    inc_cats = (await db.execute(select(IncomeCategory).order_by(IncomeCategory.name))).scalars().all()
    exp_cats = (await db.execute(select(ExpenseCategory).order_by(ExpenseCategory.name))).scalars().all()
    numbering_settings = await get_numbering_settings(db)

    return templates.TemplateResponse(request, "scan_queue.html", {
        "user": user,
        "items": items,
        "inc_cats": inc_cats,
        "exp_cats": exp_cats,
        "received_via_options": RECEIVED_VIA_OPTIONS,
        "auto_numbering_enabled": numbering_settings.auto_enabled,
        "uploaded": request.query_params.get("uploaded"),
        "error": request.query_params.get("error"),
    })


# ── Bulk upload + scannen ─────────────────────────────────────

@router.post("/upload")
async def upload_and_scan(
    request: Request,
    bestanden: List[UploadFile] = File(...),
    transaction_type: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    count = 0

    for bestand in bestanden:
        if not bestand.filename:
            continue
        ext = Path(bestand.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue

        content = await bestand.read()
        if not content or len(content) > 10 * 1024 * 1024:
            continue

        safe_name = f"{uuid.uuid4().hex}{ext}"
        file_path = SCAN_DIR / safe_name
        file_path.write_bytes(content)

        try:
            result = await process_receipt(str(file_path), db)
        except Exception as e:
            result = {"error": str(e)}

        # Normaliseer de door AI herkende datum (vaak DD-MM-YYYY) naar ISO-formaat
        # (YYYY-MM-DD), zodat het <input type="date"> veld correct vult en de
        # automatische factuurnummering op basis van het jaartal kan starten.
        ocr_date_iso = None
        raw_ocr_date = result.get("date")
        if raw_ocr_date:
            try:
                ocr_date_iso = parse_date_inc(str(raw_ocr_date)).isoformat()
            except ValueError:
                ocr_date_iso = None

        item = ScanQueue(
            filename=safe_name,
            original_filename=bestand.filename,
            transaction_type=transaction_type,
            ocr_date=ocr_date_iso,
            ocr_amount=result.get("amount"),
            ocr_description=result.get("description"),
            ocr_invoice_number=result.get("invoice_number"),
            ocr_category_suggestion=result.get("category_suggestion"),
            ocr_error=result.get("error") or result.get("_ai_error"),
        )
        db.add(item)
        count += 1

    await db.commit()
    return RedirectResponse(f"/scan-wachtrij?uploaded={count}", status_code=302)


# ── Goedkeuren ────────────────────────────────────────────────

@router.post("/{id}/goedkeuren")
async def approve_item(
    id: int,
    request: Request,
    transaction_type: str = Form(...),
    datum: str = Form(...),
    bedrag: str = Form(...),
    category_id: int = Form(...),
    omschrijving: str = Form(""),
    factuurnummer: str = Form(""),
    factuurnummer_leverancier: str = Form(""),
    received_via: str = Form("zakelijke_rekening"),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    item = await db.get(ScanQueue, id)
    if not item:
        return RedirectResponse("/scan-wachtrij", status_code=302)

    # Valideer datum
    try:
        record_date = parse_date_inc(datum)
    except ValueError:
        return RedirectResponse("/scan-wachtrij?error=datum", status_code=302)

    # Boekjaar vergrendeld?
    if await is_year_locked(db, record_date.year):
        return RedirectResponse("/scan-wachtrij?error=vergrendeld", status_code=302)

    # Bedrag
    try:
        amount = float(bedrag.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        return RedirectResponse("/scan-wachtrij?error=bedrag", status_code=302)

    # Factuurnummer — volgt onze eigen naming-conventie op basis van het jaartal.
    # Een leeg veld of een (door de JS voor-ingevuld / handmatig getypt) nummer dat
    # al bestaat, wordt vervangen door het eerstvolgende vrije nummer. Zo krijgen
    # meerdere scans van hetzelfde jaar/type bij bulk-goedkeuren nooit een duplicaat.
    ns = await get_numbering_settings(db)
    inv_type = "inkomsten" if transaction_type == "inkomst" else "uitgaven"
    model = Income if transaction_type == "inkomst" else Expense
    invoice_number = factuurnummer.strip()

    async def _number_taken(num: str) -> bool:
        r = await db.execute(select(model).where(model.invoice_number == num))
        return r.scalar_one_or_none() is not None

    if not invoice_number:
        if not ns.auto_enabled:
            return RedirectResponse("/scan-wachtrij?error=factuurnummer", status_code=302)
        invoice_number = await get_next_invoice_number(db, record_date.year, inv_type, ns)
    elif await _number_taken(invoice_number):
        invoice_number = await get_next_invoice_number(db, record_date.year, inv_type, ns)

    description = omschrijving.strip() or None
    supplier_invoice = factuurnummer_leverancier.strip() or None

    if transaction_type == "inkomst":
        cat = await db.get(IncomeCategory, category_id)
        if not cat:
            return RedirectResponse("/scan-wachtrij?error=categorie", status_code=302)

        rec = Income(
            invoice_number=invoice_number,
            supplier_invoice_number=supplier_invoice,
            category_id=category_id,
            date=record_date,
            amount=amount,
            description=description,
            status="niet_betaald",
            received_via=received_via if received_via in RECEIVED_VIA_OPTIONS else "zakelijke_rekening",
        )
        db.add(rec)
        await db.flush()

        rel_path = _move_scan_file(item.filename, "inkomsten", cat.slug, invoice_number)
        if rel_path:
            db.add(IncomeReceipt(income_id=rec.id, file_path=rel_path))

    else:
        cat = await db.get(ExpenseCategory, category_id)
        if not cat:
            return RedirectResponse("/scan-wachtrij?error=categorie", status_code=302)

        rec = Expense(
            invoice_number=invoice_number,
            supplier_invoice_number=supplier_invoice,
            category_id=category_id,
            date=record_date,
            amount=amount,
            description=description,
        )
        db.add(rec)
        await db.flush()

        rel_path = _move_scan_file(item.filename, "uitgaven", cat.slug, invoice_number)
        if rel_path:
            db.add(ExpenseReceipt(expense_id=rec.id, file_path=rel_path))

    await db.delete(item)
    await db.commit()
    return RedirectResponse("/scan-wachtrij", status_code=302)


# ── Afwijzen (één item) ───────────────────────────────────────

@router.post("/{id}/afwijzen")
async def reject_item(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    item = await db.get(ScanQueue, id)
    if item:
        _delete_scan_file(item.filename)
        await db.delete(item)
        await db.commit()
    return RedirectResponse("/scan-wachtrij", status_code=302)


# ── Afwijzen (alles) ──────────────────────────────────────────

@router.post("/bulk-afwijzen")
async def reject_all(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    items = (await db.execute(select(ScanQueue))).scalars().all()
    for item in items:
        _delete_scan_file(item.filename)
        await db.delete(item)
    await db.commit()
    return RedirectResponse("/scan-wachtrij", status_code=302)


# ── Bestandshulpfuncties ──────────────────────────────────────

def _move_scan_file(filename: str, type_: str, category_slug: str, invoice_number: str) -> Optional[str]:
    src = SCAN_DIR / filename
    if not src.exists():
        return None
    ext = src.suffix
    safe_inv = "".join(c for c in invoice_number if c.isalnum() or c in "-_.")
    dest_dir = UPLOAD_ROOT / type_ / category_slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{safe_inv}{ext}"
    shutil.move(str(src), str(dest))
    return str(dest.relative_to(UPLOAD_ROOT))


def _delete_scan_file(filename: str):
    path = SCAN_DIR / filename
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass
