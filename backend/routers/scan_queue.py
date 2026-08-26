from fastapi import APIRouter, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from pathlib import Path
import uuid
import shutil
import os
import logging

logger = logging.getLogger("boekhoud.scan_queue")

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
from backend.services.duplicates import compute_hash, find_duplicate
from backend.services.i18n import t
from backend.routers.incomes import RECEIVED_VIA_OPTIONS, parse_date as parse_date_inc

router = APIRouter(prefix="/scan-wachtrij")
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t

SCAN_DIR = UPLOAD_ROOT / "scan_queue"


def _scan_path(filename: str) -> Path:
    return SCAN_DIR / filename


def _parse_amount_draft(bedrag: str) -> Optional[float]:
    """Best-effort parse voor het concept-veld — geeft None terug bij een
    ongeldige waarde in plaats van te crashen; de echte validatie gebeurt apart."""
    try:
        return float(bedrag.replace(",", "."))
    except (ValueError, AttributeError):
        return None


async def _category_slug(db: AsyncSession, transaction_type: str, category_id: int) -> Optional[str]:
    """Zoekt de slug op van de gekozen categorie, zodat die bij een heropgebouwde
    pagina opnieuw als 'geselecteerd' wordt herkend (zie ocr_category_suggestion)."""
    model = IncomeCategory if transaction_type == "inkomst" else ExpenseCategory
    cat = await db.get(model, category_id)
    return cat.slug if cat else None


def _build_description(invoice_number, date_str, amount, ai_description=None) -> str:
    """Standaard omschrijving: begint met de door AI herkende artikelomschrijving,
    gevolgd door datum en bedrag, en eindigt met het originele factuurnummer."""
    parts = []
    if ai_description:
        parts.append(str(ai_description))
    if date_str:
        parts.append(f"Datum: {date_str}")
    if amount is not None:
        parts.append(f"Bedrag: € {amount:.2f}")
    if invoice_number:
        parts.append(f"Factuurnr.: {invoice_number}")
    return " | ".join(parts)


# ── Overzicht + upload formulier ─────────────────────────────

@router.get("", response_class=HTMLResponse)
async def scan_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    items = (await db.execute(
        select(ScanQueue).order_by(ScanQueue.created_at.desc())
    )).scalars().all()

    # Standaard omschrijving samenstellen uit origineel factuurnr., datum en bedrag.
    for it in items:
        it.default_description = _build_description(
            it.ocr_invoice_number, it.ocr_date, it.ocr_amount, it.ocr_description
        )

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
    batch_hashes: dict[str, str] = {}       # hash -> originele bestandsnaam (binnen deze upload)
    batch_invoices: dict[str, str] = {}     # origineel factuurnr -> bestandsnaam (binnen deze upload)

    for bestand in bestanden:
        try:
            if not bestand.filename:
                continue
            ext = Path(bestand.filename).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue

            content = await bestand.read()
            if not content or len(content) > 10 * 1024 * 1024:
                continue

            file_hash = compute_hash(content)
            safe_name = f"{uuid.uuid4().hex}{ext}"
            file_path = SCAN_DIR / safe_name
            file_path.write_bytes(content)

            try:
                result = await process_receipt(str(file_path), db)
            except Exception as e:
                logger.error(f"OCR-verwerking mislukt voor '{bestand.filename}': {type(e).__name__}: {e}")
                result = {"error": f"Uitlezen mislukt ({type(e).__name__}). Vul de gegevens handmatig in."}

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

            ocr_invoice = result.get("invoice_number")

            # Duplicaatdetectie: eerst binnen deze upload-batch, dan tegen bestaande records.
            warning = None
            if file_hash in batch_hashes:
                warning = f"Zelfde bestand zit ook in deze upload ({batch_hashes[file_hash]})."
            elif ocr_invoice and str(ocr_invoice).strip() in batch_invoices:
                warning = (f"Origineel factuurnummer '{ocr_invoice}' zit ook in deze upload "
                           f"({batch_invoices[str(ocr_invoice).strip()]}).")
            else:
                warning = await find_duplicate(
                    db, file_hash=file_hash, invoice_number=ocr_invoice,
                    amount=result.get("amount"), date_str=ocr_date_iso,
                )

            batch_hashes.setdefault(file_hash, bestand.filename)
            if ocr_invoice and str(ocr_invoice).strip():
                batch_invoices.setdefault(str(ocr_invoice).strip(), bestand.filename)

            item = ScanQueue(
                filename=safe_name,
                original_filename=bestand.filename,
                transaction_type=transaction_type,
                file_hash=file_hash,
                duplicate_warning=warning,
                ocr_date=ocr_date_iso,
                ocr_amount=result.get("amount"),
                ocr_description=result.get("description"),
                ocr_invoice_number=ocr_invoice,
                ocr_category_suggestion=result.get("category_suggestion"),
                ocr_error=result.get("error") or result.get("_ai_error"),
            )
            db.add(item)
            count += 1
        except Exception as e:
            # Eén beschadigd of onverwacht bestand mag de rest van de bulk-upload
            # nooit laten crashen — sla over, log het, en ga door met de volgende.
            logger.error(f"Verwerking van '{getattr(bestand, 'filename', '?')}' overgeslagen: {type(e).__name__}: {e}")
            continue

    await db.commit()
    return RedirectResponse(f"/scan-wachtrij?uploaded={count}", status_code=302)


# ── Concept opslaan (live, per veldwijziging) ──────────────────
#
# De scan-wachtrij toont meerdere items op één pagina. Zolang wijzigingen
# (zoals een handmatig gekozen categorie) alleen in de browser leven, gaan ze
# verloren zodra een ANDER item wordt goedgekeurd — dat stuurt de hele pagina
# naar de server en terug, en de server kent alleen wat al is opgeslagen. Dit
# endpoint slaat elke wijziging direct op zodra de gebruiker 'm maakt, zodat
# een goedkeuring van item A de nog-openstaande wijzigingen van item B, C, ...
# niet meer terugzet naar de oorspronkelijke AI-suggestie.
@router.post("/{id}/concept")
async def save_draft(
    id: int,
    request: Request,
    transaction_type: str = Form(...),
    datum: str = Form(""),
    bedrag: str = Form(""),
    category_id: str = Form(""),
    omschrijving: str = Form(""),
    factuurnummer_leverancier: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return JSONResponse({"ok": False, "error": "niet ingelogd"}, status_code=401)

    item = await db.get(ScanQueue, id)
    if not item:
        return JSONResponse({"ok": False, "error": "niet gevonden"}, status_code=404)

    item.transaction_type = transaction_type
    if datum:
        item.ocr_date = datum
    if bedrag:
        item.ocr_amount = _parse_amount_draft(bedrag)
    item.ocr_description = omschrijving.strip() or None
    item.ocr_invoice_number = factuurnummer_leverancier.strip() or None
    if category_id:
        try:
            cat_slug = await _category_slug(db, transaction_type, int(category_id))
            if cat_slug:
                item.ocr_category_suggestion = cat_slug
        except ValueError:
            pass
    await db.commit()
    return JSONResponse({"ok": True})


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

    # Sla de ingevoerde waarden meteen op als concept op het wachtrij-item, vóórdat
    # er wordt gevalideerd. Faalt een latere check (bijv. ongeldig bedrag), dan
    # toont de heropgebouwde pagina hierdoor de LAATST ingevoerde waarden — niet
    # de oorspronkelijke AI-suggestie — zodat een handmatige wijziging (zoals de
    # categorie) nooit ongemerkt wordt teruggedraaid door een mislukte poging.
    item.transaction_type = transaction_type
    item.ocr_date = datum
    item.ocr_amount = _parse_amount_draft(bedrag)
    item.ocr_description = omschrijving.strip() or None
    item.ocr_invoice_number = factuurnummer_leverancier.strip() or None
    cat_slug = await _category_slug(db, transaction_type, category_id)
    if cat_slug:
        item.ocr_category_suggestion = cat_slug
    await db.commit()

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
            db.add(IncomeReceipt(income_id=rec.id, file_path=rel_path, file_hash=item.file_hash))

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
            db.add(ExpenseReceipt(expense_id=rec.id, file_path=rel_path, file_hash=item.file_hash))

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
