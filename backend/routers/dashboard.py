from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, extract
from sqlalchemy.orm import selectinload
from backend.models.database import get_db
from backend.models.models import Income, Expense, ExpenseCategory, IncomeCategory, Depreciation
from backend.routers.auth import require_auth
from datetime import date, datetime
import csv
import io

router = APIRouter()
templates = Jinja2Templates(directory="backend/templates")


def calc_depreciation_for_year(dep, year: int) -> float:
    """Calculate the depreciation amount for a specific year."""
    start_year = dep.start_date.year
    end_year = start_year + dep.duration_years
    if year < start_year or year >= end_year:
        return 0.0
    annual = dep.purchase_amount * dep.annual_percentage / 100
    return annual


def calc_dep_overview(dep):
    """Full depreciation overview with per-year breakdown."""
    annual = dep.purchase_amount * dep.annual_percentage / 100
    today = date.today()
    years_elapsed = max(0, today.year - dep.start_date.year)
    # Only count full years up to duration
    years_elapsed = min(years_elapsed, dep.duration_years)
    depreciated = annual * years_elapsed
    remaining = max(dep.purchase_amount - depreciated, 0)
    end_year = dep.start_date.year + dep.duration_years

    # Per-year schedule
    schedule = []
    cumulative = 0
    for y in range(dep.start_date.year, end_year):
        cumulative += annual
        schedule.append({
            "year": y,
            "amount": annual,
            "cumulative": min(cumulative, dep.purchase_amount),
            "remaining": max(dep.purchase_amount - cumulative, 0),
            "is_current": y == today.year,
            "is_past": y < today.year,
        })

    return {
        "description": dep.expense.description or dep.expense.invoice_number,
        "invoice": dep.expense.invoice_number,
        "purchase": dep.purchase_amount,
        "annual": annual,
        "percentage": dep.annual_percentage,
        "depreciated": depreciated,
        "remaining": remaining,
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

    now = datetime.now()
    year = now.year

    inc_result = await db.execute(
        select(func.sum(Income.amount)).where(extract("year", Income.date) == year)
    )
    total_income = inc_result.scalar() or 0

    exp_result = await db.execute(
        select(func.sum(Expense.amount)).where(extract("year", Expense.date) == year)
    )
    total_expenses = exp_result.scalar() or 0

    unpaid_result = await db.execute(
        select(func.sum(Income.amount)).where(
            Income.status == "niet_betaald",
            extract("year", Income.date) == year
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
        select(extract("month", Income.date).label("month"), func.sum(Income.amount).label("total"))
        .where(extract("year", Income.date) == year)
        .group_by("month").order_by("month")
    )
    monthly_exp = await db.execute(
        select(extract("month", Expense.date).label("month"), func.sum(Expense.amount).label("total"))
        .where(extract("year", Expense.date) == year)
        .group_by("month").order_by("month")
    )

    inc_by_month = {int(r.month): float(r.total) for r in monthly_inc}
    exp_by_month = {int(r.month): float(r.total) for r in monthly_exp}
    months = ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]

    return templates.TemplateResponse(request, "dashboard.html", {
        "total_income": total_income,
        "total_expenses": total_expenses,
        "profit": total_income - total_expenses,
        "unpaid": unpaid,
        "recent_incomes": recent_inc.scalars().all(),
        "recent_expenses": recent_exp.scalars().all(),
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
        .where(extract("year", Income.date) == year)
        .group_by(IncomeCategory.name)
    )
    exp_by_cat = await db.execute(
        select(ExpenseCategory.name, func.sum(Expense.amount).label("total"))
        .join(Expense, Expense.category_id == ExpenseCategory.id)
        .where(extract("year", Expense.date) == year)
        .group_by(ExpenseCategory.name)
    )
    inc_total = await db.execute(
        select(func.sum(Income.amount)).where(extract("year", Income.date) == year)
    )
    exp_total = await db.execute(
        select(func.sum(Expense.amount)).where(extract("year", Expense.date) == year)
    )

    # All depreciations with full schedule
    dep_result = await db.execute(
        select(Depreciation).options(selectinload(Depreciation.expense))
    )
    depreciations = dep_result.scalars().all()
    dep_data = [calc_dep_overview(d) for d in depreciations]

    # Total depreciation cost for selected year
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

    # Monthly income
    monthly_inc = await db.execute(
        select(extract("month", Income.date).label("month"), func.sum(Income.amount).label("total"))
        .where(extract("year", Income.date) == year)
        .group_by("month").order_by("month")
    )
    # Monthly expenses
    monthly_exp = await db.execute(
        select(extract("month", Expense.date).label("month"), func.sum(Expense.amount).label("total"))
        .where(extract("year", Expense.date) == year)
        .group_by("month").order_by("month")
    )
    # All transactions for the year
    all_inc = await db.execute(
        select(Income).options(selectinload(Income.category))
        .where(extract("year", Income.date) == year)
        .order_by(Income.date)
    )
    all_exp = await db.execute(
        select(Expense).options(selectinload(Expense.category))
        .where(extract("year", Expense.date) == year)
        .order_by(Expense.date)
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
            "month": m,
            "name": month_names[m - 1],
            "income": inc,
            "expense": exp,
            "profit": profit,
            "cumulative": cumulative_profit,
        })

    total_inc = sum(r["income"] for r in rows)
    total_exp = sum(r["expense"] for r in rows)

    # Income by category for year
    inc_by_cat = await db.execute(
        select(IncomeCategory.name, func.sum(Income.amount).label("total"))
        .join(Income, Income.category_id == IncomeCategory.id)
        .where(extract("year", Income.date) == year)
        .group_by(IncomeCategory.name)
    )
    exp_by_cat = await db.execute(
        select(ExpenseCategory.name, func.sum(Expense.amount).label("total"))
        .join(Expense, Expense.category_id == ExpenseCategory.id)
        .where(extract("year", Expense.date) == year)
        .group_by(ExpenseCategory.name)
    )

    # Unpaid invoices
    unpaid = await db.execute(
        select(Income).options(selectinload(Income.category))
        .where(Income.status == "niet_betaald", extract("year", Income.date) == year)
        .order_by(Income.date)
    )

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
    })


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
        writer.writerow([i.invoice_number, i.category.name, i.date.strftime("%d.%m.%Y"),
                         f"{i.amount:.2f}", i.description or "", i.status])
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
        writer.writerow([e.invoice_number, e.category.name, e.date.strftime("%d.%m.%Y"),
                         f"{e.amount:.2f}", e.description or "", "Ja" if e.is_depreciable else "Nee"])
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
        select(extract("month", Income.date).label("month"), func.sum(Income.amount).label("total"))
        .where(extract("year", Income.date) == year).group_by("month")
    )
    monthly_exp = await db.execute(
        select(extract("month", Expense.date).label("month"), func.sum(Expense.amount).label("total"))
        .where(extract("year", Expense.date) == year).group_by("month")
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
        writer.writerow([month_names[m-1], f"{inc:.2f}", f"{exp:.2f}", f"{profit:.2f}", f"{cumulative:.2f}"])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=jaaroverzicht_{year}.csv"})
