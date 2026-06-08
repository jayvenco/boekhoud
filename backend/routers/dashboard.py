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


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    now = datetime.now()
    year = now.year

    # Totals for current year
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

    # Recent transactions (last 5 of each)
    recent_inc = await db.execute(
        select(Income).options(selectinload(Income.category))
        .order_by(Income.date.desc()).limit(5)
    )
    recent_exp = await db.execute(
        select(Expense).options(selectinload(Expense.category))
        .order_by(Expense.date.desc()).limit(5)
    )

    # Monthly data for chart (current year)
    monthly_inc = await db.execute(
        select(
            extract("month", Income.date).label("month"),
            func.sum(Income.amount).label("total")
        ).where(extract("year", Income.date) == year)
        .group_by("month").order_by("month")
    )
    monthly_exp = await db.execute(
        select(
            extract("month", Expense.date).label("month"),
            func.sum(Expense.amount).label("total")
        ).where(extract("year", Expense.date) == year)
        .group_by("month").order_by("month")
    )

    inc_by_month = {int(r.month): float(r.total) for r in monthly_inc}
    exp_by_month = {int(r.month): float(r.total) for r in monthly_exp}

    months = ["Jan", "Feb", "Mrt", "Apr", "Mei", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dec"]
    chart_income = [inc_by_month.get(i, 0) for i in range(1, 13)]
    chart_expenses = [exp_by_month.get(i, 0) for i in range(1, 13)]

    return templates.TemplateResponse(request, "dashboard.html", {"user": user,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "profit": total_income - total_expenses,
        "unpaid": unpaid,
        "recent_incomes": recent_inc.scalars().all(),
        "recent_expenses": recent_exp.scalars().all(),
        "chart_months": months,
        "chart_income": chart_income,
        "chart_expenses": chart_expenses,
        "year": year})


@router.get("/rapportages", response_class=HTMLResponse)
async def reports(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    year = int(request.query_params.get("jaar", datetime.now().year))

    # Income by category
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

    # Depreciation overview
    dep_result = await db.execute(
        select(Depreciation).options(selectinload(Depreciation.expense))
    )
    depreciations = dep_result.scalars().all()

    dep_data = []
    for dep in depreciations:
        annual = dep.purchase_amount * dep.annual_percentage / 100
        years_elapsed = (date.today().year - dep.start_date.year)
        depreciated = min(annual * years_elapsed, dep.purchase_amount)
        remaining = max(dep.purchase_amount - depreciated, 0)
        dep_data.append({
            "description": dep.expense.description or dep.expense.invoice_number,
            "purchase": dep.purchase_amount,
            "annual": annual,
            "depreciated": depreciated,
            "remaining": remaining,
            "end_year": dep.start_date.year + dep.duration_years,
        })

    total_inc = inc_total.scalar() or 0
    total_exp = exp_total.scalar() or 0

    return templates.TemplateResponse(request, "reports.html", {"user": user,
        "year": year,
        "years": list(range(2022, datetime.now().year + 2)),
        "income_by_cat": [(r.name, float(r.total)) for r in inc_by_cat],
        "expense_by_cat": [(r.name, float(r.total)) for r in exp_by_cat],
        "total_income": total_inc,
        "total_expenses": total_exp,
        "profit": total_inc - total_exp,
        "depreciations": dep_data})


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
        writer.writerow([
            i.invoice_number, i.category.name,
            i.date.strftime("%d.%m.%Y"), f"{i.amount:.2f}",
            i.description or "", i.status
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inkomsten.csv"}
    )


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
        writer.writerow([
            e.invoice_number, e.category.name,
            e.date.strftime("%d.%m.%Y"), f"{e.amount:.2f}",
            e.description or "", "Ja" if e.is_depreciable else "Nee"
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=uitgaven.csv"}
    )
