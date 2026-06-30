from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from backend.models.database import get_db
from backend.models.models import FiscalYear, FiscalYearAuditLog
from backend.routers.auth import require_auth
from backend.services.fiscal_year import get_fiscal_year, get_year_stats, log_action
from backend.services.i18n import t
from datetime import date, datetime
from typing import Optional

router = APIRouter(prefix="/boekjaren")
templates = Jinja2Templates(directory="backend/templates")
templates.env.globals["t"] = t

STATUS_LABELS = {
    "concept": "Concept",
    "actief": "Actief",
    "afgesloten": "Afgesloten",
}

STATUS_BADGE = {
    "concept": "badge-neutral",
    "actief": "badge-success",
    "afgesloten": "badge-warning",
}


@router.get("", response_class=HTMLResponse)
async def list_fiscal_years(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    result = await db.execute(
        select(FiscalYear)
        .options(selectinload(FiscalYear.audit_logs))
        .order_by(FiscalYear.year.desc())
    )
    fiscal_years = result.scalars().all()

    active = next((fy for fy in fiscal_years if fy.status == "actief"), None)

    return templates.TemplateResponse(request, "fiscal_years.html", {
        "fiscal_years": fiscal_years,
        "active_year": active,
        "status_labels": STATUS_LABELS,
        "status_badge": STATUS_BADGE,
        "current_year": datetime.now().year,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@router.post("/nieuw")
async def create_fiscal_year(
    request: Request,
    year: int = Form(...),
    opening_balance: float = Form(0.0),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    existing = await get_fiscal_year(db, year)
    if existing:
        return RedirectResponse(f"/boekjaren?error=bestaat", status_code=302)

    fy = FiscalYear(
        year=year,
        status="concept",
        start_date=date(year, 1, 1),
        end_date=date(year, 12, 31),
        opening_balance=opening_balance,
        notes=notes.strip() or None,
    )
    db.add(fy)
    await db.flush()
    await log_action(db, fy, "aangemaakt", user.username,
                     notes=f"Boekjaar {year} aangemaakt.")
    await db.commit()
    return RedirectResponse("/boekjaren?success=aangemaakt", status_code=302)


@router.post("/{year}/activeren")
async def activate_fiscal_year(year: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    fy = await get_fiscal_year(db, year)
    if not fy or fy.status == "afgesloten":
        return RedirectResponse("/boekjaren?error=ongeldig", status_code=302)

    # Deactivate currently active year
    result = await db.execute(select(FiscalYear).where(FiscalYear.status == "actief"))
    for other in result.scalars().all():
        if other.year != year:
            other.status = "concept"
            await log_action(db, other, "gedeactiveerd", user.username)

    fy.status = "actief"
    await log_action(db, fy, "geactiveerd", user.username)
    await db.commit()
    return RedirectResponse("/boekjaren?success=geactiveerd", status_code=302)


@router.get("/{year}/afsluiten", response_class=HTMLResponse)
async def confirm_close_year(year: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    fy = await get_fiscal_year(db, year)
    if not fy:
        raise HTTPException(404)
    if fy.status == "afgesloten":
        return RedirectResponse(f"/boekjaren?error=al_afgesloten", status_code=302)

    stats = await get_year_stats(db, year)

    return templates.TemplateResponse(request, "fiscal_year_confirm.html", {
        "fy": fy,
        "year": year,
        "stats": stats,
    })


@router.post("/{year}/afsluiten")
async def close_fiscal_year(
    request: Request,
    year: int,
    reason: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    fy = await get_fiscal_year(db, year)
    if not fy:
        raise HTTPException(404)
    if fy.status == "afgesloten":
        return RedirectResponse("/boekjaren?error=al_afgesloten", status_code=302)

    stats = await get_year_stats(db, year)

    fy.status = "afgesloten"
    fy.closed_at = datetime.now()
    fy.closed_by = user.username
    fy.total_income = stats["total_income"]
    fy.total_expenses = stats["total_expenses"]
    fy.closing_balance = stats["balance"]
    fy.total_transactions = stats["transaction_count"]

    await log_action(db, fy, "afgesloten", user.username,
                     reason=reason.strip() or None,
                     notes=f"Inkomsten: €{stats['total_income']:.2f} | "
                           f"Uitgaven: €{stats['total_expenses']:.2f} | "
                           f"Saldo: €{stats['balance']:.2f}")

    # Auto-create next fiscal year if it doesn't exist
    next_year = year + 1
    next_fy = await get_fiscal_year(db, next_year)
    if not next_fy:
        next_fy = FiscalYear(
            year=next_year,
            status="concept",
            start_date=date(next_year, 1, 1),
            end_date=date(next_year, 12, 31),
            opening_balance=stats["balance"],
        )
        db.add(next_fy)
        await db.flush()
        await log_action(db, next_fy, "aangemaakt", user.username,
                         notes=f"Automatisch aangemaakt bij afsluiting boekjaar {year}. "
                               f"Beginsaldo: €{stats['balance']:.2f}")

    await db.commit()
    return RedirectResponse(f"/boekjaren?success=afgesloten&jaar={year}", status_code=302)


@router.post("/{year}/heropenen")
async def reopen_fiscal_year(
    request: Request,
    year: int,
    reason: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    fy = await get_fiscal_year(db, year)
    if not fy or fy.status != "afgesloten":
        return RedirectResponse("/boekjaren?error=ongeldig", status_code=302)

    fy.status = "concept"
    await log_action(db, fy, "heropend", user.username,
                     reason=reason.strip(),
                     notes=f"Boekjaar {year} heropend door {user.username}.")
    await db.commit()
    return RedirectResponse(f"/boekjaren?success=heropend&jaar={year}", status_code=302)
