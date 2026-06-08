from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.models.database import get_db
from backend.models.models import Expense, ExpenseCategory, Depreciation
from backend.routers.auth import require_auth
from backend.services.files import save_receipt, delete_file
from datetime import date, datetime
from typing import Optional

router = APIRouter(prefix="/uitgaven")
templates = Jinja2Templates(directory="backend/templates")


def parse_date(d: str) -> date:
    try:
        return datetime.strptime(d, "%d.%m.%Y").date()
    except ValueError:
        return datetime.strptime(d, "%Y-%m-%d").date()


@router.get("", response_class=HTMLResponse)
async def list_expenses(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Expense).options(selectinload(Expense.category), selectinload(Expense.depreciation))
        .order_by(Expense.date.desc())
    )
    expenses = result.scalars().all()
    cats = await db.execute(select(ExpenseCategory))
    return templates.TemplateResponse(request, "expenses/list.html", {"expenses": expenses,
        "categories": cats.scalars().all(), "user": user})


@router.get("/nieuw", response_class=HTMLResponse)
async def new_expense_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cats = await db.execute(select(ExpenseCategory))
    return templates.TemplateResponse(request, "expenses/form.html", {"categories": cats.scalars().all(), "expense": None, "user": user})


@router.post("/nieuw")
async def create_expense(
    request: Request,
    invoice_number: str = Form(...),
    category_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    amount: float = Form(...),
    description: str = Form(""),
    receipt: Optional[UploadFile] = File(None),
    is_depreciable: Optional[str] = Form(None),
    dep_start_date: Optional[str] = Form(None),
    dep_duration: Optional[int] = Form(5),
    dep_percentage: Optional[float] = Form(20.0),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    cat = await db.get(ExpenseCategory, category_id)
    receipt_path = None
    if receipt and receipt.filename:
        try:
            receipt_path = await save_receipt(receipt, "uitgaven", cat.slug, invoice_number)
        except ValueError as e:
            cats = await db.execute(select(ExpenseCategory))
            return templates.TemplateResponse(request, "expenses/form.html", {"categories": cats.scalars().all(),
                "error": str(e), "expense": None, "user": user})

    depreciable = is_depreciable == "on"
    expense = Expense(
        invoice_number=invoice_number,
        category_id=category_id,
        date=parse_date(date_str),
        amount=amount,
        description=description,
        receipt_path=receipt_path,
        is_depreciable=depreciable
    )
    db.add(expense)
    await db.flush()

    if depreciable and dep_start_date:
        dep = Depreciation(
            expense_id=expense.id,
            start_date=parse_date(dep_start_date),
            purchase_amount=amount,
            duration_years=dep_duration or 5,
            annual_percentage=dep_percentage or 20.0
        )
        db.add(dep)

    await db.commit()
    return RedirectResponse("/uitgaven", status_code=302)


@router.get("/{id}/bewerken", response_class=HTMLResponse)
async def edit_expense_form(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Expense).options(selectinload(Expense.category), selectinload(Expense.depreciation))
        .where(Expense.id == id)
    )
    expense = result.scalar_one_or_none()
    if not expense:
        raise HTTPException(404)
    cats = await db.execute(select(ExpenseCategory))
    return templates.TemplateResponse(request, "expenses/form.html", {"categories": cats.scalars().all(), "expense": expense, "user": user})


@router.post("/{id}/bewerken")
async def update_expense(
    id: int,
    request: Request,
    invoice_number: str = Form(...),
    category_id: int = Form(...),
    date_str: str = Form(..., alias="date"),
    amount: float = Form(...),
    description: str = Form(""),
    receipt: Optional[UploadFile] = File(None),
    is_depreciable: Optional[str] = Form(None),
    dep_start_date: Optional[str] = Form(None),
    dep_duration: Optional[int] = Form(5),
    dep_percentage: Optional[float] = Form(20.0),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Expense).options(selectinload(Expense.depreciation)).where(Expense.id == id)
    )
    expense = result.scalar_one_or_none()
    if not expense:
        raise HTTPException(404)

    cat = await db.get(ExpenseCategory, category_id)
    if receipt and receipt.filename:
        try:
            new_path = await save_receipt(receipt, "uitgaven", cat.slug, invoice_number)
            if expense.receipt_path:
                delete_file(expense.receipt_path)
            expense.receipt_path = new_path
        except ValueError:
            pass

    depreciable = is_depreciable == "on"
    expense.invoice_number = invoice_number
    expense.category_id = category_id
    expense.date = parse_date(date_str)
    expense.amount = amount
    expense.description = description
    expense.is_depreciable = depreciable

    if depreciable and dep_start_date:
        if expense.depreciation:
            expense.depreciation.start_date = parse_date(dep_start_date)
            expense.depreciation.purchase_amount = amount
            expense.depreciation.duration_years = dep_duration or 5
            expense.depreciation.annual_percentage = dep_percentage or 20.0
        else:
            dep = Depreciation(
                expense_id=expense.id,
                start_date=parse_date(dep_start_date),
                purchase_amount=amount,
                duration_years=dep_duration or 5,
                annual_percentage=dep_percentage or 20.0
            )
            db.add(dep)
    elif not depreciable and expense.depreciation:
        await db.delete(expense.depreciation)

    await db.commit()
    return RedirectResponse("/uitgaven", status_code=302)


@router.post("/{id}/verwijderen")
async def delete_expense(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(Expense).options(selectinload(Expense.depreciation)).where(Expense.id == id)
    )
    expense = result.scalar_one_or_none()
    if expense:
        if expense.receipt_path:
            delete_file(expense.receipt_path)
        if expense.depreciation:
            await db.delete(expense.depreciation)
        await db.delete(expense)
        await db.commit()
    return RedirectResponse("/uitgaven", status_code=302)
