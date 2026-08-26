from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.models import FiscalYear, FiscalYearAuditLog, Income, Expense
from datetime import date, datetime


async def get_fiscal_year(db: AsyncSession, year: int):
    result = await db.execute(select(FiscalYear).where(FiscalYear.year == year))
    return result.scalar_one_or_none()


async def is_year_locked(db: AsyncSession, year: int) -> bool:
    result = await db.execute(
        select(FiscalYear).where(FiscalYear.year == year, FiscalYear.status == "afgesloten")
    )
    return result.scalar_one_or_none() is not None


async def get_locked_years(db: AsyncSession) -> set:
    result = await db.execute(
        select(FiscalYear.year).where(FiscalYear.status == "afgesloten")
    )
    return {row[0] for row in result.all()}


async def get_year_stats(db: AsyncSession, year: int) -> dict:
    inc = await db.execute(
        select(func.sum(Income.amount), func.count(Income.id)).where(
            func.strftime("%Y", Income.date) == str(year)
        )
    )
    inc_row = inc.one()
    total_income = float(inc_row[0] or 0)
    income_count = inc_row[1] or 0

    exp = await db.execute(
        select(func.sum(Expense.amount), func.count(Expense.id)).where(
            func.strftime("%Y", Expense.date) == str(year),
            Expense.is_depreciable.isnot(True),
        )
    )
    exp_row = exp.one()
    total_expenses = float(exp_row[0] or 0)
    expense_count = exp_row[1] or 0

    return {
        "total_income": total_income,
        "total_expenses": total_expenses,
        "balance": total_income - total_expenses,
        "transaction_count": income_count + expense_count,
        "income_count": income_count,
        "expense_count": expense_count,
    }


async def log_action(db: AsyncSession, fiscal_year: FiscalYear, action: str,
                     performed_by: str, reason: str = None, notes: str = None):
    db.add(FiscalYearAuditLog(
        fiscal_year_id=fiscal_year.id,
        year=fiscal_year.year,
        action=action,
        performed_by=performed_by,
        performed_at=datetime.now(),
        reason=reason,
        notes=notes,
    ))
