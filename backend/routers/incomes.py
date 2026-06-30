from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from backend.models.database import get_db
from backend.models.models import Income, IncomeCategory, IncomeReceipt
from backend.routers.auth import require_auth
from backend.services.files import save_receipts, delete_file, move_tmp_to_category
from backend.services.invoice_numbering import get_numbering_settings
from backend.services.fiscal_year import is_year_locked, get_locked_years
from backend.services.i18n import t
from datetime import date, datetime
from typing import Optional, List

router = APIRouter(prefix="/inkomsten")
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t

# Standaardopties voor "Ontvangen op". Nieuwe optie toevoegen = één regel hier
# + één <option> in het formulier.
RECEIVED_VIA_OPTIONS = {
    "zakelijke_rekening": "Zakelijke rekening",
    "priverekening": "Privérekening",
    "creditcard": "Creditcard",
    "rekening_partner": "Rekening partner",
    "externe_praktijk": "Externe praktijk",
    "sumup": "SumUp",
    "payt": "Payt debiteurenbeheer",
    "infomedics": "Infomedics",
    "overig": "Overig",
}


def parse_date(d: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Ongeldige datum: {d}")


def validate_received_via(received_via: str, received_via_other: str):
    """Retourneert (received_via, received_via_other, foutmelding_of_None)."""
    if received_via not in RECEIVED_VIA_OPTIONS:
        return received_via, None, f"Onbekende optie voor 'Ontvangen op': {received_via}"
    other = received_via_other.strip() if received_via == "overig" else None
    if received_via == "overig" and not other:
        return received_via, other, "Specificeer op welke rekening of via welk betaalmiddel de inkomst is ontvangen."
    return received_via, other, None


@router.get("", response_class=HTMLResponse)
async def list_incomes(request: Request, db: AsyncSession = Depends(get_db), q: str = ""):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    query = select(Income).options(
        selectinload(Income.category),
        selectinload(Income.receipts)
    ).order_by(Income.date.desc())
    if q:
        query = query.where(or_(
            Income.invoice_number.ilike(f"%{q}%"),
            Income.description.ilike(f"%{q}%"),
        ))
    result = await db.execute(query)
    incomes = result.scalars().all()
    cats = await db.execute(select(IncomeCategory))
    locked_years = await get_locked_years(db)
    return templates.TemplateResponse(request, "incomes/list.html", {
        "incomes": incomes, "categories": cats.scalars().all(), "q": q,
        "received_via_labels": RECEIVED_VIA_OPTIONS,
        "locked_years": locked_years,
    })


@router.get("/nieuw", response_class=HTMLResponse)
async def new_income_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cats = await db.execute(select(IncomeCategory))
    numbering_settings = await get_numbering_settings(db)
    return templates.TemplateResponse(request, "incomes/form.html", {
        "categories": cats.scalars().all(), "income": None,
        "auto_numbering_enabled": numbering_settings.auto_enabled,
        "received_via_options": RECEIVED_VIA_OPTIONS
    })


@router.post("/nieuw")
async def create_income(
    request: Request,
    invoice_number: str = Form(...),
    category_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    amount: float = Form(...),
    description: str = Form(""),
    status: str = Form("niet_betaald"),
    received_via: str = Form(...),
    received_via_other: str = Form(""),
    receipts: List[UploadFile] = File(default=[]),
    ocr_tmp_file: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    # Controleer of het boekjaar vergrendeld is
    income_year = parse_date(date_str).year
    if await is_year_locked(db, income_year):
        cats = await db.execute(select(IncomeCategory))
        return templates.TemplateResponse(request, "incomes/form.html", {
            "categories": cats.scalars().all(), "income": None,
            "received_via_options": RECEIVED_VIA_OPTIONS,
            "error": f"Boekjaar {income_year} is afgesloten. Inkomsten kunnen niet worden toegevoegd aan een afgesloten boekjaar."
        })

    received_via, received_via_other, via_error = validate_received_via(received_via, received_via_other)
    if via_error:
        cats = await db.execute(select(IncomeCategory))
        return templates.TemplateResponse(request, "incomes/form.html", {
            "categories": cats.scalars().all(), "income": None,
            "received_via_options": RECEIVED_VIA_OPTIONS,
            "error": via_error
        })

    # Check duplicate
    dup = await db.execute(select(Income).where(Income.invoice_number == invoice_number))
    if dup.scalar_one_or_none():
        cats = await db.execute(select(IncomeCategory))
        return templates.TemplateResponse(request, "incomes/form.html", {
            "categories": cats.scalars().all(), "income": None,
            "received_via_options": RECEIVED_VIA_OPTIONS,
            "error": f"Factuurnummer '{invoice_number}' bestaat al."
        })

    cat = await db.get(IncomeCategory, category_id)
    valid_files = [r for r in receipts if r and r.filename]
    saved = []
    if valid_files:
        saved = await save_receipts(valid_files, "inkomsten", cat.slug, invoice_number)
    elif ocr_tmp_file:
        p = await move_tmp_to_category(ocr_tmp_file, "inkomsten", cat.slug, invoice_number)
        if p:
            saved = [{"path": p, "suffix": None}]

    receipt_path = saved[0]["path"] if saved else None
    income = Income(
        invoice_number=invoice_number, category_id=category_id,
        date=parse_date(date_str), amount=amount, description=description,
        status=status, received_via=received_via, received_via_other=received_via_other,
        receipt_path=receipt_path
    )
    db.add(income)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        cats = await db.execute(select(IncomeCategory))
        return templates.TemplateResponse(request, "incomes/form.html", {
            "categories": cats.scalars().all(), "income": None,
            "received_via_options": RECEIVED_VIA_OPTIONS,
            "error": f"Factuurnummer '{invoice_number}' is zojuist al gebruikt door een andere registratie. Vernieuw de pagina en probeer opnieuw."
        })
    for r in saved:
        db.add(IncomeReceipt(income_id=income.id, file_path=r["path"], suffix=r["suffix"]))
    await db.commit()
    return RedirectResponse("/inkomsten", status_code=302)


@router.get("/{id}/bewerken", response_class=HTMLResponse)
async def edit_income_form(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Income).options(selectinload(Income.category), selectinload(Income.receipts))
        .where(Income.id == id)
    )
    income = result.scalar_one_or_none()
    if not income:
        raise HTTPException(404)
    cats = await db.execute(select(IncomeCategory))
    year_locked = await is_year_locked(db, income.date.year)
    return templates.TemplateResponse(request, "incomes/form.html", {
        "categories": cats.scalars().all(), "income": income,
        "received_via_options": RECEIVED_VIA_OPTIONS,
        "year_locked": year_locked,
    })


@router.post("/{id}/bewerken")
async def update_income(
    id: int, request: Request,
    invoice_number: str = Form(...),
    category_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    amount: float = Form(...),
    description: str = Form(""),
    status: str = Form("niet_betaald"),
    received_via: str = Form(...),
    received_via_other: str = Form(""),
    receipts: List[UploadFile] = File(default=[]),
    ocr_tmp_file: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Income).options(selectinload(Income.receipts)).where(Income.id == id)
    )
    income = result.scalar_one_or_none()
    if not income:
        raise HTTPException(404)

    if await is_year_locked(db, income.date.year):
        cats = await db.execute(select(IncomeCategory))
        return templates.TemplateResponse(request, "incomes/form.html", {
            "categories": cats.scalars().all(), "income": income,
            "received_via_options": RECEIVED_VIA_OPTIONS,
            "year_locked": True,
            "error": f"Boekjaar {income.date.year} is afgesloten. Wijzigingen zijn niet toegestaan."
        })

    received_via, received_via_other, via_error = validate_received_via(received_via, received_via_other)
    if via_error:
        cats = await db.execute(select(IncomeCategory))
        result2 = await db.execute(
            select(Income).options(selectinload(Income.category), selectinload(Income.receipts)).where(Income.id == id)
        )
        return templates.TemplateResponse(request, "incomes/form.html", {
            "categories": cats.scalars().all(), "income": result2.scalar_one_or_none(),
            "received_via_options": RECEIVED_VIA_OPTIONS,
            "error": via_error
        })

    # Check duplicate (exclude self)
    dup = await db.execute(select(Income).where(Income.invoice_number == invoice_number, Income.id != id))
    if dup.scalar_one_or_none():
        cats = await db.execute(select(IncomeCategory))
        result2 = await db.execute(
            select(Income).options(selectinload(Income.category), selectinload(Income.receipts)).where(Income.id == id)
        )
        return templates.TemplateResponse(request, "incomes/form.html", {
            "categories": cats.scalars().all(), "income": result2.scalar_one_or_none(),
            "received_via_options": RECEIVED_VIA_OPTIONS,
            "error": f"Factuurnummer '{invoice_number}' bestaat al."
        })

    cat = await db.get(IncomeCategory, category_id)
    valid_files = [r for r in receipts if r and r.filename]
    if valid_files:
        new_saved = await save_receipts(valid_files, "inkomsten", cat.slug, invoice_number)
        for r in new_saved:
            db.add(IncomeReceipt(income_id=income.id, file_path=r["path"], suffix=r["suffix"]))
        if not income.receipt_path and new_saved:
            income.receipt_path = new_saved[0]["path"]

    income.invoice_number = invoice_number
    income.category_id = category_id
    income.date = parse_date(date_str)
    income.amount = amount
    income.description = description
    income.status = status
    income.received_via = received_via
    income.received_via_other = received_via_other
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        cats = await db.execute(select(IncomeCategory))
        result2 = await db.execute(
            select(Income).options(selectinload(Income.category), selectinload(Income.receipts)).where(Income.id == id)
        )
        return templates.TemplateResponse(request, "incomes/form.html", {
            "categories": cats.scalars().all(), "income": result2.scalar_one_or_none(),
            "received_via_options": RECEIVED_VIA_OPTIONS,
            "error": f"Factuurnummer '{invoice_number}' is zojuist al gebruikt door een andere registratie. Vernieuw de pagina en probeer opnieuw."
        })
    return RedirectResponse("/inkomsten", status_code=302)


@router.post("/{id}/bon-verwijderen/{receipt_id}")
async def delete_receipt(id: int, receipt_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(IncomeReceipt).where(IncomeReceipt.id == receipt_id, IncomeReceipt.income_id == id))
    receipt = result.scalar_one_or_none()
    if receipt:
        delete_file(receipt.file_path)
        await db.delete(receipt)
        await db.commit()
    return RedirectResponse(f"/inkomsten/{id}/bewerken", status_code=302)


@router.post("/{id}/verwijderen")
async def delete_income(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Income).options(selectinload(Income.receipts)).where(Income.id == id)
    )
    income = result.scalar_one_or_none()
    if income and await is_year_locked(db, income.date.year):
        return RedirectResponse("/inkomsten?error=vergrendeld", status_code=302)
    if income:
        for r in income.receipts:
            delete_file(r.file_path)
        if income.receipt_path:
            delete_file(income.receipt_path)
        await db.delete(income)
        await db.commit()
    return RedirectResponse("/inkomsten", status_code=302)
