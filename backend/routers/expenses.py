from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from backend.models.database import get_db
from backend.models.models import Expense, ExpenseCategory, Depreciation, ExpenseReceipt
from backend.routers.auth import require_auth
from backend.services.files import save_receipts, delete_file, move_tmp_to_category
from backend.services.i18n import t
from backend.services.invoice_numbering import get_numbering_settings
from backend.services.fiscal_year import is_year_locked, get_locked_years
from backend.services.pdf_export import generate_expenses_pdf
from datetime import date, datetime
import calendar
from typing import Optional, List

router = APIRouter(prefix="/uitgaven")
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t

# Aantal occurrences dat 12 maanden vooruit gegenereerd wordt, per frequentie.
# Nieuwe frequenties toevoegen = één regel hier + één <option> in het formulier.
RECURRING_INTERVALS = {
    "maandelijks": (1, 12),
    "per_kwartaal": (3, 4),
    "halfjaarlijks": (6, 2),
    "jaarlijks": (12, 1),
}


def parse_date(d: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Ongeldige datum: {d}")


def add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


async def generate_recurring_occurrences(db: AsyncSession, parent: Expense) -> int:
    """Genereert toekomstige uitgaven voor een abonnement, 12 maanden vooruit
    vanaf recurring_start_date, op basis van de gekozen frequentie."""
    interval_months, count = RECURRING_INTERVALS.get(parent.recurring_frequency, (1, 12))
    base_date = parent.recurring_start_date or parent.date

    existing = await db.execute(
        select(Expense.date).where(Expense.parent_recurring_expense_id == parent.id)
    )
    existing_dates = {row[0] for row in existing.all()}

    created = 0
    for i in range(1, count + 1):
        occ_date = add_months(base_date, interval_months * i)
        if parent.recurring_end_date and occ_date > parent.recurring_end_date:
            break
        if occ_date in existing_dates:
            continue
        db.add(Expense(
            invoice_number=f"{parent.invoice_number}-REC{i}",
            category_id=parent.category_id,
            date=occ_date,
            amount=parent.amount,
            description=parent.description,
            is_recurring=True,
            is_auto_generated=True,
            parent_recurring_expense_id=parent.id,
            recurring_frequency=parent.recurring_frequency,
        ))
        created += 1
    return created


async def regenerate_future_occurrences(db: AsyncSession, parent: Expense):
    """Verwijdert nog niet gepasseerde auto-gegenereerde occurrences en bouwt ze
    opnieuw op basis van de huidige instellingen. Al gepasseerde (historische)
    occurrences blijven altijd behouden."""
    today = date.today()
    old = await db.execute(
        select(Expense).where(
            Expense.parent_recurring_expense_id == parent.id,
            Expense.is_auto_generated == True,
            Expense.date >= today,
        )
    )
    for o in old.scalars().all():
        await db.delete(o)
    await db.flush()
    if parent.recurring_active:
        await generate_recurring_occurrences(db, parent)


async def get_series_root(db: AsyncSession, expense: Expense) -> Expense:
    if expense.parent_recurring_expense_id:
        root = await db.get(Expense, expense.parent_recurring_expense_id)
        return root or expense
    return expense


def _apply_expense_filters(query, q, from_date, to_date, category_id, year,
                           min_amount, max_amount, contact):
    def try_date(s):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(s.strip(), fmt).date()
            except ValueError:
                continue
        return None

    if q:
        query = query.where(or_(
            Expense.invoice_number.ilike(f"%{q}%"),
            Expense.description.ilike(f"%{q}%"),
        ))
    if from_date:
        d = try_date(from_date)
        if d:
            query = query.where(Expense.date >= d)
    if to_date:
        d = try_date(to_date)
        if d:
            query = query.where(Expense.date <= d)
    if category_id:
        try:
            query = query.where(Expense.category_id == int(category_id))
        except ValueError:
            pass
    if year:
        query = query.where(func.strftime("%Y", Expense.date) == str(year))
    if min_amount:
        try:
            query = query.where(Expense.amount >= float(min_amount))
        except ValueError:
            pass
    if max_amount:
        try:
            query = query.where(Expense.amount <= float(max_amount))
        except ValueError:
            pass
    if contact:
        query = query.where(or_(
            Expense.invoice_number.ilike(f"%{contact}%"),
            Expense.description.ilike(f"%{contact}%"),
        ))
    return query


@router.get("", response_class=HTMLResponse)
async def list_expenses(
    request: Request, db: AsyncSession = Depends(get_db),
    q: str = "", from_date: str = "", to_date: str = "",
    category_id: str = "", year: str = "",
    min_amount: str = "", max_amount: str = "", contact: str = "",
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    query = select(Expense).options(
        selectinload(Expense.category),
        selectinload(Expense.depreciation),
        selectinload(Expense.receipts)
    ).order_by(Expense.date.desc())
    query = _apply_expense_filters(query, q, from_date, to_date, category_id,
                                   year, min_amount, max_amount, contact)
    result = await db.execute(query)
    expenses = result.scalars().all()
    cats = await db.execute(select(ExpenseCategory))
    locked_years = await get_locked_years(db)
    filters = {"q": q, "from_date": from_date, "to_date": to_date,
               "category_id": category_id, "year": year,
               "min_amount": min_amount, "max_amount": max_amount, "contact": contact}
    return templates.TemplateResponse(request, "expenses/list.html", {
        "expenses": expenses, "categories": cats.scalars().all(),
        "today": date.today(), "locked_years": locked_years,
        "filters": filters,
        "active_filters": sum(1 for v in filters.values() if v),
    })


@router.get("/export/pdf")
async def export_expenses_pdf(
    request: Request, db: AsyncSession = Depends(get_db),
    q: str = "", from_date: str = "", to_date: str = "",
    category_id: str = "", year: str = "",
    min_amount: str = "", max_amount: str = "", contact: str = "",
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    query = select(Expense).options(
        selectinload(Expense.category),
        selectinload(Expense.receipts)
    ).order_by(Expense.date.desc())
    query = _apply_expense_filters(query, q, from_date, to_date, category_id,
                                   year, min_amount, max_amount, contact)
    result = await db.execute(query)
    expenses = result.scalars().all()

    parts = []
    if q:           parts.append(f"Zoeken: {q}")
    if from_date:   parts.append(f"Van: {from_date}")
    if to_date:     parts.append(f"Tot: {to_date}")
    if year:        parts.append(f"Boekjaar: {year}")
    if min_amount:  parts.append(f"Min: €{min_amount}")
    if max_amount:  parts.append(f"Max: €{max_amount}")
    if contact:     parts.append(f"Contact: {contact}")
    if category_id:
        cat_r = await db.get(ExpenseCategory, int(category_id))
        if cat_r:   parts.append(f"Categorie: {cat_r.name}")
    filters_desc = " | ".join(parts)

    settings = request.state.settings
    company = settings.company_name if settings else ""
    buf = generate_expenses_pdf(expenses, company_name=company, filters_desc=filters_desc)
    filename = f"uitgaven_{datetime.now().strftime('%Y%m%d')}.pdf"
    return StreamingResponse(buf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"})


@router.get("/nieuw", response_class=HTMLResponse)
async def new_expense_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cats = await db.execute(select(ExpenseCategory))
    numbering_settings = await get_numbering_settings(db)
    return templates.TemplateResponse(request, "expenses/form.html", {
        "categories": cats.scalars().all(), "expense": None,
        "auto_numbering_enabled": numbering_settings.auto_enabled
    })


@router.post("/nieuw")
async def create_expense(
    request: Request,
    invoice_number: str = Form(...),
    supplier_invoice_number: str = Form(""),
    category_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    amount: float = Form(...),
    description: str = Form(""),
    receipts: List[UploadFile] = File(default=[]),
    ocr_tmp_file: Optional[str] = Form(None),
    is_depreciable: Optional[str] = Form(None),
    dep_start_date: Optional[str] = Form(None),
    dep_duration: Optional[int] = Form(5),
    dep_residual_value: Optional[float] = Form(0.0),
    is_recurring: Optional[str] = Form(None),
    recurring_start_date: Optional[str] = Form(None),
    recurring_end_date: Optional[str] = Form(None),
    recurring_frequency: Optional[str] = Form("maandelijks"),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    expense_year = parse_date(date_str).year
    if await is_year_locked(db, expense_year):
        cats = await db.execute(select(ExpenseCategory))
        return templates.TemplateResponse(request, "expenses/form.html", {
            "categories": cats.scalars().all(), "expense": None,
            "error": f"Boekjaar {expense_year} is afgesloten. Uitgaven kunnen niet worden toegevoegd aan een afgesloten boekjaar."
        })

    cat = await db.get(ExpenseCategory, category_id)

    # Check duplicate invoice number
    dup = await db.execute(select(Expense).where(Expense.invoice_number == invoice_number))
    if dup.scalar_one_or_none():
        cats = await db.execute(select(ExpenseCategory))
        return templates.TemplateResponse(request, "expenses/form.html", {
            "categories": cats.scalars().all(), "expense": None,
            "error": f"Factuurnummer '{invoice_number}' bestaat al."
        })

    # Handle file uploads
    valid_files = [r for r in receipts if r and r.filename]
    saved = []
    if valid_files:
        saved = await save_receipts(valid_files, "uitgaven", cat.slug, invoice_number)
    elif ocr_tmp_file:
        p = await move_tmp_to_category(ocr_tmp_file, "uitgaven", cat.slug, invoice_number)
        if p:
            saved = [{"path": p, "suffix": None}]

    receipt_path = saved[0]["path"] if saved else None
    depreciable = is_depreciable == "on"
    duration = max(2, min(10, dep_duration or 5))
    residual_value = dep_residual_value or 0.0
    start_date = parse_date(dep_start_date) if depreciable and dep_start_date else parse_date(date_str)
    annual_amount = round((amount - residual_value) / duration, 2)

    recurring = is_recurring == "on"
    rec_frequency = recurring_frequency if recurring_frequency in RECURRING_INTERVALS else "maandelijks"
    rec_start = parse_date(recurring_start_date) if recurring and recurring_start_date else parse_date(date_str)
    rec_end = parse_date(recurring_end_date) if recurring and recurring_end_date else None

    expense = Expense(
        invoice_number=invoice_number,
        supplier_invoice_number=supplier_invoice_number.strip() or None,
        category_id=category_id,
        date=parse_date(date_str), amount=amount, description=description,
        receipt_path=receipt_path, is_depreciable=depreciable,
        is_recurring=recurring,
        recurring_frequency=rec_frequency if recurring else None,
        recurring_start_date=rec_start if recurring else None,
        recurring_end_date=rec_end,
        recurring_active=True,
        is_auto_generated=False,
    )
    db.add(expense)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        cats = await db.execute(select(ExpenseCategory))
        return templates.TemplateResponse(request, "expenses/form.html", {
            "categories": cats.scalars().all(), "expense": None,
            "error": f"Factuurnummer '{invoice_number}' is zojuist al gebruikt door een andere registratie. Vernieuw de pagina en probeer opnieuw."
        })

    # Save all receipt files to receipt table
    for r in saved:
        db.add(ExpenseReceipt(expense_id=expense.id, file_path=r["path"], suffix=r["suffix"]))

    if recurring:
        await generate_recurring_occurrences(db, expense)

    if depreciable:
        dep = Depreciation(
            expense_id=expense.id, start_date=start_date,
            purchase_amount=amount, duration_years=duration, residual_value=residual_value
        )
        db.add(dep)
        for i in range(duration):
            year = start_date.year + i
            dep_date = start_date.replace(year=year)
            db.add(Expense(
                invoice_number=f"{invoice_number}-AFW{year}",
                category_id=category_id,
                date=dep_date,
                amount=annual_amount,
                description=f"Afschrijving {year} – {description or invoice_number}",
                is_depreciable=False,
            ))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        cats = await db.execute(select(ExpenseCategory))
        return templates.TemplateResponse(request, "expenses/form.html", {
            "categories": cats.scalars().all(), "expense": None,
            "error": f"Factuurnummer '{invoice_number}' is zojuist al gebruikt door een andere registratie. Vernieuw de pagina en probeer opnieuw."
        })
    return RedirectResponse("/uitgaven", status_code=302)


@router.get("/{id}/bewerken", response_class=HTMLResponse)
async def edit_expense_form(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Expense).options(
            selectinload(Expense.category),
            selectinload(Expense.depreciation),
            selectinload(Expense.receipts)
        ).where(Expense.id == id)
    )
    expense = result.scalar_one_or_none()
    if not expense:
        raise HTTPException(404)
    cats = await db.execute(select(ExpenseCategory))
    year_locked = await is_year_locked(db, expense.date.year)
    return templates.TemplateResponse(request, "expenses/form.html", {
        "categories": cats.scalars().all(), "expense": expense,
        "year_locked": year_locked,
    })


@router.post("/{id}/bewerken")
async def update_expense(
    id: int, request: Request,
    invoice_number: str = Form(...),
    supplier_invoice_number: str = Form(""),
    category_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    amount: float = Form(...),
    description: str = Form(""),
    receipts: List[UploadFile] = File(default=[]),
    ocr_tmp_file: Optional[str] = Form(None),
    is_depreciable: Optional[str] = Form(None),
    dep_start_date: Optional[str] = Form(None),
    dep_duration: Optional[int] = Form(5),
    dep_residual_value: Optional[float] = Form(0.0),
    is_recurring: Optional[str] = Form(None),
    recurring_start_date: Optional[str] = Form(None),
    recurring_end_date: Optional[str] = Form(None),
    recurring_frequency: Optional[str] = Form("maandelijks"),
    edit_scope: str = Form("alleen_deze"),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Expense).options(
            selectinload(Expense.depreciation),
            selectinload(Expense.receipts)
        ).where(Expense.id == id)
    )
    expense = result.scalar_one_or_none()
    if not expense:
        raise HTTPException(404)

    if await is_year_locked(db, expense.date.year):
        cats = await db.execute(select(ExpenseCategory))
        return templates.TemplateResponse(request, "expenses/form.html", {
            "categories": cats.scalars().all(), "expense": expense,
            "year_locked": True,
            "error": f"Boekjaar {expense.date.year} is afgesloten. Wijzigingen zijn niet toegestaan."
        })

    # Check duplicate invoice (exclude self)
    dup = await db.execute(select(Expense).where(Expense.invoice_number == invoice_number, Expense.id != id))
    if dup.scalar_one_or_none():
        cats = await db.execute(select(ExpenseCategory))
        result2 = await db.execute(
            select(Expense).options(selectinload(Expense.category), selectinload(Expense.depreciation), selectinload(Expense.receipts))
            .where(Expense.id == id)
        )
        return templates.TemplateResponse(request, "expenses/form.html", {
            "categories": cats.scalars().all(), "expense": result2.scalar_one_or_none(),
            "error": f"Factuurnummer '{invoice_number}' bestaat al."
        })

    cat = await db.get(ExpenseCategory, category_id)

    # Add new uploads to existing receipts
    valid_files = [r for r in receipts if r and r.filename]
    if valid_files:
        new_saved = await save_receipts(valid_files, "uitgaven", cat.slug, invoice_number)
        for r in new_saved:
            db.add(ExpenseReceipt(expense_id=expense.id, file_path=r["path"], suffix=r["suffix"]))
        if not expense.receipt_path and new_saved:
            expense.receipt_path = new_saved[0]["path"]

    depreciable = is_depreciable == "on"
    duration = max(2, min(10, dep_duration or 5))
    residual_value = dep_residual_value or 0.0
    start_date = parse_date(dep_start_date) if depreciable and dep_start_date else expense.date
    annual_amount = round((amount - residual_value) / duration, 2)

    expense.invoice_number = invoice_number
    expense.supplier_invoice_number = supplier_invoice_number.strip() or None
    expense.category_id = category_id
    expense.date = parse_date(date_str)
    expense.amount = amount
    expense.description = description
    expense.is_depreciable = depreciable

    if depreciable:
        if expense.depreciation:
            expense.depreciation.start_date = start_date
            expense.depreciation.purchase_amount = amount
            expense.depreciation.duration_years = duration
            expense.depreciation.residual_value = residual_value
        else:
            db.add(Depreciation(expense_id=expense.id, start_date=start_date,
                purchase_amount=amount, duration_years=duration, residual_value=residual_value))

        # Rebuild yearly entries
        old_deps = await db.execute(select(Expense).where(Expense.invoice_number.like(f"{invoice_number}-AFW%")))
        for old_dep in old_deps.scalars().all():
            await db.delete(old_dep)
        for i in range(duration):
            year = start_date.year + i
            db.add(Expense(
                invoice_number=f"{invoice_number}-AFW{year}",
                category_id=category_id,
                date=start_date.replace(year=year),
                amount=annual_amount,
                description=f"Afschrijving {year} – {description or invoice_number}",
                is_depreciable=False,
            ))
    elif not depreciable and expense.depreciation:
        await db.delete(expense.depreciation)
        old_deps = await db.execute(select(Expense).where(Expense.invoice_number.like(f"{invoice_number}-AFW%")))
        for old_dep in old_deps.scalars().all():
            await db.delete(old_dep)

    # Terugkerende kosten: instellingen leven alleen op de oorspronkelijke
    # (niet-automatisch-gegenereerde) registratie van de reeks.
    if not expense.is_auto_generated:
        recurring = is_recurring == "on"
        rec_frequency = recurring_frequency if recurring_frequency in RECURRING_INTERVALS else "maandelijks"
        rec_start = parse_date(recurring_start_date) if recurring and recurring_start_date else expense.date
        rec_end = parse_date(recurring_end_date) if recurring and recurring_end_date else None

        expense.is_recurring = recurring
        expense.recurring_frequency = rec_frequency if recurring else None
        expense.recurring_start_date = rec_start if recurring else None
        expense.recurring_end_date = rec_end
        expense.recurring_active = recurring
        await db.flush()
        await regenerate_future_occurrences(db, expense)

    # Wijzigingen propageren naar de reeks (categorie/bedrag/omschrijving),
    # afhankelijk van de gekozen bewerkingsscope.
    if expense.is_recurring and edit_scope != "alleen_deze":
        root = await get_series_root(db, expense)
        sibling_query = select(Expense).where(
            Expense.id != expense.id,
            or_(Expense.id == root.id, Expense.parent_recurring_expense_id == root.id),
        )
        if edit_scope == "deze_en_toekomstig":
            sibling_query = sibling_query.where(Expense.date >= expense.date)
        siblings = (await db.execute(sibling_query)).scalars().all()
        for sib in siblings:
            sib.category_id = category_id
            sib.amount = amount
            sib.description = description

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        cats = await db.execute(select(ExpenseCategory))
        result2 = await db.execute(
            select(Expense).options(selectinload(Expense.category), selectinload(Expense.depreciation), selectinload(Expense.receipts))
            .where(Expense.id == id)
        )
        return templates.TemplateResponse(request, "expenses/form.html", {
            "categories": cats.scalars().all(), "expense": result2.scalar_one_or_none(),
            "error": f"Factuurnummer '{invoice_number}' is zojuist al gebruikt door een andere registratie. Vernieuw de pagina en probeer opnieuw."
        })
    return RedirectResponse("/uitgaven", status_code=302)


@router.post("/{id}/abonnement/stopzetten")
async def stop_recurring_expense(
    id: int, request: Request,
    delete_future: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    expense = await db.get(Expense, id)
    if not expense:
        raise HTTPException(404)

    root = await get_series_root(db, expense)
    root.recurring_active = False

    if delete_future == "on":
        today = date.today()
        future = await db.execute(select(Expense).where(
            Expense.parent_recurring_expense_id == root.id,
            Expense.is_auto_generated == True,
            Expense.date > today,
        ))
        for f in future.scalars().all():
            await db.delete(f)

    await db.commit()
    return RedirectResponse("/uitgaven", status_code=302)


@router.post("/{id}/bon-verwijderen/{receipt_id}")
async def delete_receipt(id: int, receipt_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a single receipt from an expense."""
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(ExpenseReceipt).where(ExpenseReceipt.id == receipt_id, ExpenseReceipt.expense_id == id))
    receipt = result.scalar_one_or_none()
    if receipt:
        delete_file(receipt.file_path)
        await db.delete(receipt)
        await db.commit()
    return RedirectResponse(f"/uitgaven/{id}/bewerken", status_code=302)


@router.post("/{id}/verwijderen")
async def delete_expense(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Expense).options(selectinload(Expense.depreciation), selectinload(Expense.receipts))
        .where(Expense.id == id)
    )
    expense = result.scalar_one_or_none()
    if expense and await is_year_locked(db, expense.date.year):
        return RedirectResponse("/uitgaven?error=vergrendeld", status_code=302)
    if expense:
        for r in expense.receipts:
            delete_file(r.file_path)
        if expense.receipt_path:
            delete_file(expense.receipt_path)
        if expense.depreciation:
            await db.delete(expense.depreciation)
        old_deps = await db.execute(select(Expense).where(Expense.invoice_number.like(f"{expense.invoice_number}-AFW%")))
        for old_dep in old_deps.scalars().all():
            await db.delete(old_dep)

        if expense.is_recurring and not expense.is_auto_generated:
            today = date.today()
            future = await db.execute(select(Expense).where(
                Expense.parent_recurring_expense_id == expense.id,
                Expense.is_auto_generated == True,
                Expense.date > today,
            ))
            for f in future.scalars().all():
                await db.delete(f)

        await db.delete(expense)
        await db.commit()
    return RedirectResponse("/uitgaven", status_code=302)
