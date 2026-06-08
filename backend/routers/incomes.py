from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from backend.models.database import get_db
from backend.models.models import Income, IncomeCategory
from backend.routers.auth import require_auth
from backend.services.files import save_receipt, delete_file
from datetime import date, datetime
from typing import Optional
import os

router = APIRouter(prefix="/inkomsten")
templates = Jinja2Templates(directory="backend/templates")


def parse_date(d: str) -> date:
    """Parse DD.MM.YYYY"""
    try:
        return datetime.strptime(d, "%d.%m.%Y").date()
    except ValueError:
        return datetime.strptime(d, "%Y-%m-%d").date()


@router.get("", response_class=HTMLResponse)
async def list_incomes(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Income).options(selectinload(Income.category)).order_by(Income.date.desc())
    )
    incomes = result.scalars().all()
    cats = await db.execute(select(IncomeCategory))
    categories = cats.scalars().all()
    return templates.TemplateResponse(request, "incomes/list.html", {"incomes": incomes, "categories": categories, "user": user})


@router.get("/nieuw", response_class=HTMLResponse)
async def new_income_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cats = await db.execute(select(IncomeCategory))
    categories = cats.scalars().all()
    return templates.TemplateResponse(request, "incomes/form.html", {"categories": categories, "income": None, "user": user})


@router.post("/nieuw")
async def create_income(
    request: Request,
    invoice_number: str = Form(...),
    category_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    amount: float = Form(...),
    description: str = Form(""),
    status: str = Form("niet_betaald"),
    receipt: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    cat = await db.get(IncomeCategory, category_id)
    receipt_path = None
    if receipt and receipt.filename:
        try:
            receipt_path = await save_receipt(receipt, "inkomsten", cat.slug, invoice_number)
        except ValueError as e:
            cats = await db.execute(select(IncomeCategory))
            return templates.TemplateResponse(request, "incomes/form.html", {"categories": cats.scalars().all(),
                "error": str(e), "income": None, "user": user})

    income = Income(
        invoice_number=invoice_number,
        category_id=category_id,
        date=parse_date(date_str),
        amount=amount,
        description=description,
        status=status,
        receipt_path=receipt_path
    )
    db.add(income)
    await db.commit()
    return RedirectResponse("/inkomsten", status_code=302)


@router.get("/{id}/bewerken", response_class=HTMLResponse)
async def edit_income_form(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Income).options(selectinload(Income.category)).where(Income.id == id)
    )
    income = result.scalar_one_or_none()
    if not income:
        raise HTTPException(404)
    cats = await db.execute(select(IncomeCategory))
    return templates.TemplateResponse(request, "incomes/form.html", {"categories": cats.scalars().all(), "income": income, "user": user})


@router.post("/{id}/bewerken")
async def update_income(
    id: int,
    request: Request,
    invoice_number: str = Form(...),
    category_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    amount: float = Form(...),
    description: str = Form(""),
    status: str = Form("niet_betaald"),
    receipt: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(Income).where(Income.id == id))
    income = result.scalar_one_or_none()
    if not income:
        raise HTTPException(404)

    cat = await db.get(IncomeCategory, category_id)
    if receipt and receipt.filename:
        try:
            new_path = await save_receipt(receipt, "inkomsten", cat.slug, invoice_number)
            if income.receipt_path:
                delete_file(income.receipt_path)
            income.receipt_path = new_path
        except ValueError as e:
            pass

    income.invoice_number = invoice_number
    income.category_id = category_id
    income.date = parse_date(date_str)
    income.amount = amount
    income.description = description
    income.status = status
    await db.commit()
    return RedirectResponse("/inkomsten", status_code=302)


@router.post("/{id}/verwijderen")
async def delete_income(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(Income).where(Income.id == id))
    income = result.scalar_one_or_none()
    if income:
        if income.receipt_path:
            delete_file(income.receipt_path)
        await db.delete(income)
        await db.commit()
    return RedirectResponse("/inkomsten", status_code=302)
