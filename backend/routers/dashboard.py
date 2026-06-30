from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from backend.models.database import get_db
from backend.models.models import Income, Expense, ExpenseCategory, IncomeCategory, Depreciation, ChecklistItem, YearClosure
from backend.routers.auth import require_auth
from backend.routers.checklist import get_checklist_summary
from backend.services.i18n import t
from datetime import date, datetime
from typing import Optional
import csv
import io

router = APIRouter()
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t


def year_filter(model, year: int):
    """SQLite-compatible year filter using strftime."""
    from sqlalchemy import func
    return func.strftime("%Y", model.date) == str(year)



def calc_depreciation_for_year(dep, year: int) -> float:
    start_year = dep.start_date.year
    end_year = start_year + dep.duration_years
    if year < start_year or year >= end_year:
        return 0.0
    residual = dep.residual_value or 0.0
    depreciable_base = dep.purchase_amount - residual
    return depreciable_base / dep.duration_years if dep.duration_years else 0.0


def calc_dep_overview(dep):
    residual = dep.residual_value or 0.0
    depreciable_base = dep.purchase_amount - residual
    annual = depreciable_base / dep.duration_years if dep.duration_years else 0.0
    today = date.today()
    years_elapsed = min(max(0, today.year - dep.start_date.year), dep.duration_years)
    depreciated = annual * years_elapsed
    book_value = max(dep.purchase_amount - depreciated, residual)
    end_year = dep.start_date.year + dep.duration_years

    schedule = []
    cumulative = 0
    for y in range(dep.start_date.year, end_year):
        cumulative += annual
        cumulative_capped = min(cumulative, depreciable_base)
        schedule.append({
            "year": y,
            "amount": annual,
            "cumulative": cumulative_capped,
            "book_value": max(dep.purchase_amount - cumulative_capped, residual),
            "is_current": y == today.year,
            "is_past": y < today.year,
        })

    return {
        "description": dep.expense.description or dep.expense.invoice_number,
        "invoice": dep.expense.invoice_number,
        "purchase": dep.purchase_amount,
        "residual": residual,
        "annual": annual,
        "depreciated": depreciated,
        "book_value": book_value,
        "start_year": dep.start_date.year,
        "end_year": end_year,
        "duration": dep.duration_years,
        "schedule": schedule,
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    year = datetime.now().year

    inc_result = await db.execute(
        select(func.sum(Income.amount)).where(year_filter(Income, year))
    )
    total_income = inc_result.scalar() or 0

    exp_result = await db.execute(
        select(func.sum(Expense.amount)).where(year_filter(Expense, year))
    )
    total_expenses = exp_result.scalar() or 0

    unpaid_result = await db.execute(
        select(func.sum(Income.amount)).where(
            Income.status == "niet_betaald",
            year_filter(Income, year)
        )
    )
    unpaid = unpaid_result.scalar() or 0

    recent_inc = await db.execute(
        select(Income).options(selectinload(Income.category))
        .order_by(Income.date.desc()).limit(5)
    )
    recent_exp = await db.execute(
        select(Expense).options(selectinload(Expense.category))
        .order_by(Expense.date.desc()).limit(5)
    )

    monthly_inc = await db.execute(
        select(
            func.strftime("%m", Income.date).label("month"),
            func.sum(Income.amount).label("total")
        ).where(year_filter(Income, year))
        .group_by(func.strftime("%m", Income.date))
        .order_by(func.strftime("%m", Income.date))
    )
    monthly_exp = await db.execute(
        select(
            func.strftime("%m", Expense.date).label("month"),
            func.sum(Expense.amount).label("total")
        ).where(year_filter(Expense, year))
        .group_by(func.strftime("%m", Expense.date))
        .order_by(func.strftime("%m", Expense.date))
    )

    inc_by_month = {int(r.month): float(r.total) for r in monthly_inc}
    exp_by_month = {int(r.month): float(r.total) for r in monthly_exp}
    months = ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]

    today = date.today()
    missing_receipt_result = await db.execute(
        select(Expense).options(selectinload(Expense.receipts)).where(
            Expense.is_recurring == True,
            Expense.date <= today,
            Expense.receipt_path.is_(None),
        )
    )
    missing_receipts = [e for e in missing_receipt_result.scalars().all() if not e.receipts]

    checklist_summary = await get_checklist_summary(db)

    return templates.TemplateResponse(request, "dashboard.html", {
        "total_income": total_income,
        "total_expenses": total_expenses,
        "profit": total_income - total_expenses,
        "unpaid": unpaid,
        "recent_incomes": recent_inc.scalars().all(),
        "recent_expenses": recent_exp.scalars().all(),
        "checklist_summary": checklist_summary,
        "missing_receipts_count": len(missing_receipts),
        "chart_months": months,
        "chart_income": [inc_by_month.get(i, 0) for i in range(1, 13)],
        "chart_expenses": [exp_by_month.get(i, 0) for i in range(1, 13)],
        "year": year,
    })


@router.get("/rapportages", response_class=HTMLResponse)
async def reports(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    year = int(request.query_params.get("jaar", datetime.now().year))

    inc_by_cat = await db.execute(
        select(IncomeCategory.name, func.sum(Income.amount).label("total"))
        .join(Income, Income.category_id == IncomeCategory.id)
        .where(year_filter(Income, year))
        .group_by(IncomeCategory.name)
    )
    exp_by_cat = await db.execute(
        select(ExpenseCategory.name, func.sum(Expense.amount).label("total"))
        .join(Expense, Expense.category_id == ExpenseCategory.id)
        .where(year_filter(Expense, year))
        .group_by(ExpenseCategory.name)
    )
    inc_total = await db.execute(
        select(func.sum(Income.amount)).where(year_filter(Income, year))
    )
    exp_total = await db.execute(
        select(func.sum(Expense.amount)).where(year_filter(Expense, year))
    )

    dep_result = await db.execute(
        select(Depreciation).options(selectinload(Depreciation.expense))
    )
    depreciations = dep_result.scalars().all()
    dep_data = [calc_dep_overview(d) for d in depreciations]
    dep_this_year = sum(calc_depreciation_for_year(d, year) for d in depreciations)

    total_inc = inc_total.scalar() or 0
    total_exp = exp_total.scalar() or 0

    return templates.TemplateResponse(request, "reports.html", {
        "year": year,
        "years": list(range(2022, datetime.now().year + 2)),
        "income_by_cat": [(r.name, float(r.total)) for r in inc_by_cat],
        "expense_by_cat": [(r.name, float(r.total)) for r in exp_by_cat],
        "total_income": total_inc,
        "total_expenses": total_exp,
        "profit": total_inc - total_exp,
        "depreciations": dep_data,
        "dep_this_year": dep_this_year,
    })


@router.get("/jaaroverzicht", response_class=HTMLResponse)
async def yearly_overview(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    year = int(request.query_params.get("jaar", datetime.now().year))

    monthly_inc = await db.execute(
        select(
            func.strftime("%m", Income.date).label("month"),
            func.sum(Income.amount).label("total")
        ).where(year_filter(Income, year))
        .group_by(func.strftime("%m", Income.date))
    )
    monthly_exp = await db.execute(
        select(
            func.strftime("%m", Expense.date).label("month"),
            func.sum(Expense.amount).label("total")
        ).where(year_filter(Expense, year))
        .group_by(func.strftime("%m", Expense.date))
    )
    all_inc = await db.execute(
        select(Income).options(selectinload(Income.category))
        .where(year_filter(Income, year)).order_by(Income.date)
    )
    all_exp = await db.execute(
        select(Expense).options(selectinload(Expense.category))
        .where(year_filter(Expense, year)).order_by(Expense.date)
    )

    inc_by_month = {int(r.month): float(r.total) for r in monthly_inc}
    exp_by_month = {int(r.month): float(r.total) for r in monthly_exp}

    month_names = ["Januari", "Februari", "Maart", "April", "Mei", "Juni",
                   "Juli", "Augustus", "September", "Oktober", "November", "December"]

    rows = []
    cumulative_profit = 0
    for m in range(1, 13):
        inc = inc_by_month.get(m, 0)
        exp = exp_by_month.get(m, 0)
        profit = inc - exp
        cumulative_profit += profit
        rows.append({
            "month": m, "name": month_names[m - 1],
            "income": inc, "expense": exp,
            "profit": profit, "cumulative": cumulative_profit,
        })

    total_inc = sum(r["income"] for r in rows)
    total_exp = sum(r["expense"] for r in rows)

    inc_by_cat = await db.execute(
        select(IncomeCategory.name, func.sum(Income.amount).label("total"))
        .join(Income, Income.category_id == IncomeCategory.id)
        .where(year_filter(Income, year))
        .group_by(IncomeCategory.name)
    )
    exp_by_cat = await db.execute(
        select(ExpenseCategory.name, func.sum(Expense.amount).label("total"))
        .join(Expense, Expense.category_id == ExpenseCategory.id)
        .where(year_filter(Expense, year))
        .group_by(ExpenseCategory.name)
    )
    unpaid = await db.execute(
        select(Income).options(selectinload(Income.category))
        .where(Income.status == "niet_betaald", year_filter(Income, year))
        .order_by(Income.date)
    )

    open_items_result = await db.execute(select(ChecklistItem).where(ChecklistItem.status == "open"))
    open_checklist_items = open_items_result.scalars().all()

    closure_result = await db.execute(select(YearClosure).where(YearClosure.year == year))
    closure = closure_result.scalar_one_or_none()

    return templates.TemplateResponse(request, "yearly.html", {
        "year": year,
        "years": list(range(2022, datetime.now().year + 2)),
        "rows": rows,
        "total_income": total_inc,
        "total_expenses": total_exp,
        "profit": total_inc - total_exp,
        "income_by_cat": [(r.name, float(r.total)) for r in inc_by_cat],
        "expense_by_cat": [(r.name, float(r.total)) for r in exp_by_cat],
        "unpaid_invoices": unpaid.scalars().all(),
        "all_incomes": all_inc.scalars().all(),
        "all_expenses": all_exp.scalars().all(),
        "chart_months": [r["name"][:3] for r in rows],
        "chart_income": [r["income"] for r in rows],
        "chart_expenses": [r["expense"] for r in rows],
        "chart_cumulative": [r["cumulative"] for r in rows],
        "open_checklist_items": open_checklist_items,
        "closure": closure,
        "warn_open_items": request.query_params.get("warn") == "1",
    })


@router.post("/jaaroverzicht/afsluiten")
async def close_year(
    request: Request,
    jaar: int = Form(...),
    force: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    open_items_result = await db.execute(select(ChecklistItem).where(ChecklistItem.status == "open"))
    open_items = open_items_result.scalars().all()

    if open_items and force != "on":
        return RedirectResponse(f"/jaaroverzicht?jaar={jaar}&warn=1", status_code=302)

    result = await db.execute(select(YearClosure).where(YearClosure.year == jaar))
    closure = result.scalar_one_or_none()
    if not closure:
        closure = YearClosure(year=jaar, open_items_at_closure=len(open_items))
        db.add(closure)
    else:
        closure.open_items_at_closure = len(open_items)
        closure.closed_at = datetime.now()
    await db.commit()
    return RedirectResponse(f"/jaaroverzicht?jaar={jaar}&closed=1", status_code=302)


@router.get("/export/inkomsten")
async def export_incomes(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Income).options(selectinload(Income.category)).order_by(Income.date.desc())
    )
    incomes = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Factuurnummer", "Categorie", "Datum", "Bedrag", "Omschrijving", "Status"])
    for i in incomes:
        writer.writerow([i.invoice_number, i.category.name,
                         i.date.strftime("%d-%m-%Y"), f"{i.amount:.2f}",
                         i.description or "", i.status])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inkomsten.csv"})


@router.get("/export/uitgaven")
async def export_expenses(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Expense).options(selectinload(Expense.category)).order_by(Expense.date.desc())
    )
    expenses = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Factuurnummer", "Categorie", "Datum", "Bedrag", "Omschrijving", "Afschrijving"])
    for e in expenses:
        writer.writerow([e.invoice_number, e.category.name,
                         e.date.strftime("%d-%m-%Y"), f"{e.amount:.2f}",
                         e.description or "", "Ja" if e.is_depreciable else "Nee"])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=uitgaven.csv"})


@router.get("/export/jaaroverzicht")
async def export_yearly(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    year = int(request.query_params.get("jaar", datetime.now().year))

    monthly_inc = await db.execute(
        select(func.strftime("%m", Income.date).label("month"), func.sum(Income.amount).label("total"))
        .where(year_filter(Income, year)).group_by(func.strftime("%m", Income.date))
    )
    monthly_exp = await db.execute(
        select(func.strftime("%m", Expense.date).label("month"), func.sum(Expense.amount).label("total"))
        .where(year_filter(Expense, year)).group_by(func.strftime("%m", Expense.date))
    )
    inc_by_month = {int(r.month): float(r.total) for r in monthly_inc}
    exp_by_month = {int(r.month): float(r.total) for r in monthly_exp}
    month_names = ["Januari","Februari","Maart","April","Mei","Juni",
                   "Juli","Augustus","September","Oktober","November","December"]

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Maand", "Inkomsten", "Uitgaven", "Winst/Verlies", "Cumulatief"])
    cumulative = 0
    for m in range(1, 13):
        inc = inc_by_month.get(m, 0)
        exp = exp_by_month.get(m, 0)
        profit = inc - exp
        cumulative += profit
        writer.writerow([month_names[m-1], f"{inc:.2f}", f"{exp:.2f}",
                         f"{profit:.2f}", f"{cumulative:.2f}"])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=jaaroverzicht_{year}.csv"})
