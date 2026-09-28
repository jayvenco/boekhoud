from datetime import timedelta

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.models.database import get_db
from backend.models.models import (
    BankImportLine, Income, Expense, IncomeCategory, ExpenseCategory,
)
from backend.routers.auth import require_auth
from backend.routers.incomes import RECEIVED_VIA_OPTIONS
from backend.services.fiscal_year import is_year_locked
from backend.services.invoice_numbering import get_numbering_settings, get_next_invoice_number
from backend.services.i18n import t
from backend.services import bank_csv

router = APIRouter(prefix="/bank-import")
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t

MATCH_TOLERANCE_DAYS = 3

# Het geüploade bestand leeft alleen tussen de preview-stap en de bevestiging;
# er is maar één actieve gebruiker, dus een moduleglobal volstaat (net als
# elders in de app geen sessies/queues nodig zijn voor dit soort tussenstappen).
_pending_upload: dict = {}


async def _find_match(db: AsyncSession, row_date, amount: float):
    """Zoekt een bestaande inkomst/uitgave met hetzelfde bedrag binnen een
    kleine datummarge (afschrijving bij de bank loopt vaak een dag of wat
    achter op de boekingsdatum). Bij precies één treffer: gematcht."""
    lo, hi = row_date - timedelta(days=MATCH_TOLERANCE_DAYS), row_date + timedelta(days=MATCH_TOLERANCE_DAYS)
    model = Income if amount > 0 else Expense
    result = await db.execute(
        select(model).where(model.amount == abs(amount), model.date >= lo, model.date <= hi)
    )
    matches = result.scalars().all()
    if len(matches) == 1:
        return ("inkomst" if amount > 0 else "uitgave"), matches[0].id
    return None, None


@router.get("", response_class=HTMLResponse)
async def review_queue(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    nieuw = (await db.execute(
        select(BankImportLine).where(BankImportLine.status == "nieuw")
        .order_by(BankImportLine.date.desc())
    )).scalars().all()
    gematcht_count = (await db.execute(
        select(BankImportLine).where(BankImportLine.status == "gematcht")
    )).scalars().all()

    inc_cats = (await db.execute(select(IncomeCategory).order_by(IncomeCategory.name))).scalars().all()
    exp_cats = (await db.execute(select(ExpenseCategory).order_by(ExpenseCategory.name))).scalars().all()

    return templates.TemplateResponse(request, "bank_import/queue.html", {
        "user": user,
        "items": nieuw,
        "gematcht_count": len(gematcht_count),
        "inc_cats": inc_cats,
        "exp_cats": exp_cats,
        "uploaded": request.query_params.get("uploaded"),
        "matched": request.query_params.get("matched"),
        "dupe": request.query_params.get("dupe"),
        "invalid": request.query_params.get("invalid"),
        "error": request.query_params.get("error"),
    })


@router.get("/upload", response_class=HTMLResponse)
async def upload_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "bank_import/upload.html", {"user": user})


@router.post("/upload")
async def upload_preview(request: Request, bestand: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    content = await bestand.read()
    if not content:
        return RedirectResponse("/bank-import/upload?error=leeg", status_code=302)

    preview = bank_csv.read_preview(content)
    if not preview["header"]:
        return RedirectResponse("/bank-import/upload?error=onleesbaar", status_code=302)

    _pending_upload.clear()
    _pending_upload["content"] = content
    _pending_upload["filename"] = bestand.filename

    return templates.TemplateResponse(request, "bank_import/mapping.html", {
        "user": user,
        "filename": bestand.filename,
        "header": preview["header"],
        "sample": preview["sample"],
        "layout": preview["layout"],
        "has_header": preview["has_header"],
        "delimiter": preview["delimiter"],
    })


@router.post("/importeren")
async def confirm_import(
    request: Request,
    date_col: str = Form(""),
    amount_col: str = Form(""),
    description_col: str = Form(""),
    counterparty_col: str = Form(""),
    afbij_col: str = Form(""),
    delimiter: str = Form(";"),
    has_header: str = Form("1"),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if "content" not in _pending_upload:
        return RedirectResponse("/bank-import/upload?error=verlopen", status_code=302)

    def _to_idx(v):
        return int(v) if v not in ("", None) else None

    mapping = {
        "date": _to_idx(date_col), "amount": _to_idx(amount_col),
        "description": _to_idx(description_col), "counterparty": _to_idx(counterparty_col),
        "afbij": _to_idx(afbij_col),
    }
    if mapping["date"] is None or mapping["amount"] is None:
        return RedirectResponse("/bank-import/upload?error=kolommen", status_code=302)

    content = _pending_upload["content"]
    filename = _pending_upload.get("filename")
    rows = bank_csv.parse_rows(content, mapping, delimiter, has_header == "1")
    _pending_upload.clear()

    added, skipped_dupe, skipped_invalid, matched = 0, 0, 0, 0
    for row in rows:
        if row.date is None or row.amount is None:
            skipped_invalid += 1
            continue
        row_hash = bank_csv.compute_row_hash(row.date, row.amount, row.description, row.counterparty)
        exists = await db.execute(select(BankImportLine).where(BankImportLine.row_hash == row_hash))
        if exists.scalar_one_or_none():
            skipped_dupe += 1
            continue

        matched_type, matched_id = await _find_match(db, row.date, row.amount)
        status = "gematcht" if matched_type else "nieuw"
        if matched_type:
            matched += 1
        db.add(BankImportLine(
            date=row.date, description=row.description or None, counterparty=row.counterparty or None,
            amount=row.amount, row_hash=row_hash, status=status,
            matched_type=matched_type, matched_id=matched_id, source_filename=filename,
        ))
        added += 1

    await db.commit()
    return RedirectResponse(
        f"/bank-import?uploaded={added}&matched={matched}&dupe={skipped_dupe}&invalid={skipped_invalid}",
        status_code=302,
    )


@router.post("/{id}/negeren")
async def ignore_line(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    line = await db.get(BankImportLine, id)
    if line:
        line.status = "genegeerd"
        await db.commit()
    return RedirectResponse("/bank-import", status_code=302)


@router.post("/{id}/boeken")
async def book_line(
    id: int, request: Request,
    transaction_type: str = Form(...),
    category_id: int = Form(...),
    omschrijving: str = Form(""),
    received_via: str = Form("zakelijke_rekening"),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    line = await db.get(BankImportLine, id)
    if not line:
        return RedirectResponse("/bank-import", status_code=302)

    if await is_year_locked(db, line.date.year):
        return RedirectResponse("/bank-import?error=vergrendeld", status_code=302)

    ns = await get_numbering_settings(db)
    inv_type = "inkomsten" if transaction_type == "inkomst" else "uitgaven"
    model = Income if transaction_type == "inkomst" else Expense
    invoice_number = await get_next_invoice_number(db, line.date.year, inv_type, ns)
    description = omschrijving.strip() or line.description or line.counterparty or None

    if transaction_type == "inkomst":
        cat = await db.get(IncomeCategory, category_id)
        if not cat:
            return RedirectResponse("/bank-import?error=categorie", status_code=302)
        rec = Income(
            invoice_number=invoice_number, category_id=category_id, date=line.date,
            amount=abs(line.amount), description=description, status="betaald",
            received_via=received_via if received_via in RECEIVED_VIA_OPTIONS else "zakelijke_rekening",
        )
    else:
        cat = await db.get(ExpenseCategory, category_id)
        if not cat:
            return RedirectResponse("/bank-import?error=categorie", status_code=302)
        rec = Expense(
            invoice_number=invoice_number, category_id=category_id, date=line.date,
            amount=abs(line.amount), description=description,
        )
    db.add(rec)
    await db.flush()

    line.status = "geboekt"
    line.matched_type = transaction_type
    line.matched_id = rec.id
    await db.commit()
    return RedirectResponse("/bank-import", status_code=302)
