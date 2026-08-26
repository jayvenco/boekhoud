"""JSON API for external AI-agent access.

Authentication: X-API-Key header must match the stored key and enabled=True.
All routes are prefixed /api/v1/.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import date

from backend.models.database import get_db
from backend.models.models import (
    APISettings, Income, Expense, IncomeCategory, ExpenseCategory,
    PaymentStatus, ReceivedVia,
)
from backend.services.invoice_numbering import get_next_invoice_number, get_numbering_settings

router = APIRouter(prefix="/api/v1", tags=["API"])


# ── Auth dependency ───────────────────────────────────────────

async def require_api_key(
    x_api_key: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(APISettings))
    cfg = result.scalar_one_or_none()
    if not cfg or not cfg.enabled or not cfg.api_key:
        raise HTTPException(status_code=403, detail="API is uitgeschakeld")
    if x_api_key != cfg.api_key:
        raise HTTPException(status_code=401, detail="Ongeldige API-sleutel")
    return cfg


# ── Pydantic schemas ──────────────────────────────────────────

class CategoryOut(BaseModel):
    id: int
    name: str
    slug: str

    model_config = {"from_attributes": True}


class IncomeOut(BaseModel):
    id: int
    invoice_number: str
    category_id: int
    category_name: str
    date: date
    amount: float
    description: Optional[str]
    status: str
    received_via: str

    model_config = {"from_attributes": True}


class ExpenseOut(BaseModel):
    id: int
    invoice_number: str
    category_id: int
    category_name: str
    date: date
    amount: float
    description: Optional[str]

    model_config = {"from_attributes": True}


class IncomeIn(BaseModel):
    invoice_number: Optional[str] = None
    category_id: int
    date: date
    amount: float
    description: Optional[str] = None
    status: str = "niet_betaald"
    received_via: str = "zakelijke_rekening"

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Bedrag moet groter zijn dan 0")
        return v


class ExpenseIn(BaseModel):
    invoice_number: Optional[str] = None
    category_id: int
    date: date
    amount: float
    description: Optional[str] = None

    @field_validator("amount")
    @classmethod
    def positive_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Bedrag moet groter zijn dan 0")
        return v


# ── Helper ────────────────────────────────────────────────────

def _income_out(inc: Income) -> dict:
    return {
        "id": inc.id,
        "invoice_number": inc.invoice_number,
        "category_id": inc.category_id,
        "category_name": inc.category.name if inc.category else "",
        "date": inc.date,
        "amount": inc.amount,
        "description": inc.description,
        "status": inc.status,
        "received_via": inc.received_via,
    }


def _expense_out(exp: Expense) -> dict:
    return {
        "id": exp.id,
        "invoice_number": exp.invoice_number,
        "category_id": exp.category_id,
        "category_name": exp.category.name if exp.category else "",
        "date": exp.date,
        "amount": exp.amount,
        "description": exp.description,
    }


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    jaar: Optional[int] = Query(None, description="Boekjaar (standaard: huidig jaar)"),
    db: AsyncSession = Depends(get_db),
    _: APISettings = Depends(require_api_key),
):
    """Totalen inkomsten en uitgaven voor een boekjaar."""
    from datetime import datetime
    year = jaar or datetime.now().year

    inc_result = await db.execute(
        select(func.sum(Income.amount), func.count(Income.id))
        .where(func.strftime("%Y", Income.date) == str(year))
    )
    inc_total, inc_count = inc_result.one()

    exp_result = await db.execute(
        select(func.sum(Expense.amount), func.count(Expense.id))
        .where(func.strftime("%Y", Expense.date) == str(year), Expense.is_depreciable.isnot(True))
    )
    exp_total, exp_count = exp_result.one()

    inc_total = inc_total or 0.0
    exp_total = exp_total or 0.0
    return {
        "jaar": year,
        "inkomsten": {"totaal": round(inc_total, 2), "aantal": inc_count or 0},
        "uitgaven": {"totaal": round(exp_total, 2), "aantal": exp_count or 0},
        "resultaat": round(inc_total - exp_total, 2),
    }


@router.get("/inkomsten")
async def list_incomes(
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    jaar: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: APISettings = Depends(require_api_key),
):
    from sqlalchemy.orm import selectinload
    q = select(Income).options(selectinload(Income.category)).order_by(Income.date.desc())
    if from_date:
        q = q.where(Income.date >= from_date)
    if to_date:
        q = q.where(Income.date <= to_date)
    if jaar:
        q = q.where(func.strftime("%Y", Income.date) == str(jaar))
    if category_id:
        q = q.where(Income.category_id == category_id)
    if status:
        q = q.where(Income.status == status)
    q = q.limit(limit)
    result = await db.execute(q)
    return [_income_out(r) for r in result.scalars().all()]


@router.post("/inkomsten", status_code=201)
async def create_income(
    body: IncomeIn,
    db: AsyncSession = Depends(get_db),
    _: APISettings = Depends(require_api_key),
):
    # Validate category
    cat = await db.get(IncomeCategory, body.category_id)
    if not cat:
        raise HTTPException(status_code=422, detail=f"Categorie {body.category_id} bestaat niet")

    # Auto invoice number if not provided
    invoice_number = body.invoice_number
    if not invoice_number:
        ns = await get_numbering_settings(db)
        if ns.auto_enabled:
            invoice_number = await get_next_invoice_number(db, body.date.year, "inkomsten", ns)
        else:
            raise HTTPException(status_code=422, detail="invoice_number is verplicht (auto-nummering is uitgeschakeld)")

    # Check uniqueness
    existing = await db.execute(select(Income).where(Income.invoice_number == invoice_number))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Factuurnummer '{invoice_number}' bestaat al")

    inc = Income(
        invoice_number=invoice_number,
        category_id=body.category_id,
        date=body.date,
        amount=body.amount,
        description=body.description,
        status=body.status,
        received_via=body.received_via,
    )
    db.add(inc)
    await db.flush()
    await db.refresh(inc)
    # Load category for response
    inc.category = cat
    await db.commit()
    return _income_out(inc)


@router.get("/uitgaven")
async def list_expenses(
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    jaar: Optional[int] = Query(None),
    category_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: APISettings = Depends(require_api_key),
):
    from sqlalchemy.orm import selectinload
    q = select(Expense).options(selectinload(Expense.category)).order_by(Expense.date.desc())
    if from_date:
        q = q.where(Expense.date >= from_date)
    if to_date:
        q = q.where(Expense.date <= to_date)
    if jaar:
        q = q.where(func.strftime("%Y", Expense.date) == str(jaar))
    if category_id:
        q = q.where(Expense.category_id == category_id)
    q = q.limit(limit)
    result = await db.execute(q)
    return [_expense_out(r) for r in result.scalars().all()]


@router.post("/uitgaven", status_code=201)
async def create_expense(
    body: ExpenseIn,
    db: AsyncSession = Depends(get_db),
    _: APISettings = Depends(require_api_key),
):
    cat = await db.get(ExpenseCategory, body.category_id)
    if not cat:
        raise HTTPException(status_code=422, detail=f"Categorie {body.category_id} bestaat niet")

    invoice_number = body.invoice_number
    if not invoice_number:
        ns = await get_numbering_settings(db)
        if ns.auto_enabled:
            invoice_number = await get_next_invoice_number(db, body.date.year, "uitgaven", ns)
        else:
            raise HTTPException(status_code=422, detail="invoice_number is verplicht (auto-nummering is uitgeschakeld)")

    existing = await db.execute(select(Expense).where(Expense.invoice_number == invoice_number))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Factuurnummer '{invoice_number}' bestaat al")

    exp = Expense(
        invoice_number=invoice_number,
        category_id=body.category_id,
        date=body.date,
        amount=body.amount,
        description=body.description,
    )
    db.add(exp)
    await db.flush()
    await db.refresh(exp)
    exp.category = cat
    await db.commit()
    return _expense_out(exp)


@router.get("/categorieen")
async def list_categories(
    db: AsyncSession = Depends(get_db),
    _: APISettings = Depends(require_api_key),
):
    """Alle inkomsten- en uitgavencategorieën."""
    inc_result = await db.execute(select(IncomeCategory).order_by(IncomeCategory.name))
    exp_result = await db.execute(select(ExpenseCategory).order_by(ExpenseCategory.name))
    return {
        "inkomsten": [CategoryOut.model_validate(c) for c in inc_result.scalars().all()],
        "uitgaven": [CategoryOut.model_validate(c) for c in exp_result.scalars().all()],
    }
