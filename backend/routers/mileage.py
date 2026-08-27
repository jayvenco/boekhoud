from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import date, datetime
from typing import Optional
import csv
import io

from backend.models.database import get_db
from backend.models.models import MileageEntry, CompanySettings
from backend.routers.auth import require_auth
from backend.services.fiscal_year import is_year_locked, get_locked_years

router = APIRouter(prefix="/kilometers")
templates = Jinja2Templates(directory="backend/templates")

DEFAULT_KM_RATE = 0.23


def parse_date(d: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Ongeldige datum: {d}")


async def _default_rate(db: AsyncSession) -> float:
    result = await db.execute(select(CompanySettings))
    settings = result.scalar_one_or_none()
    return settings.default_km_rate if settings and settings.default_km_rate else DEFAULT_KM_RATE


def _apply_filters(query, from_date, to_date, year):
    if from_date:
        try:
            query = query.where(MileageEntry.date >= parse_date(from_date))
        except ValueError:
            pass
    if to_date:
        try:
            query = query.where(MileageEntry.date <= parse_date(to_date))
        except ValueError:
            pass
    if year:
        query = query.where(func.strftime("%Y", MileageEntry.date) == str(year))
    return query


@router.get("", response_class=HTMLResponse)
async def list_mileage(
    request: Request, db: AsyncSession = Depends(get_db),
    from_date: str = "", to_date: str = "", year: str = "",
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    query = select(MileageEntry).order_by(MileageEntry.date.desc())
    query = _apply_filters(query, from_date, to_date, year)
    result = await db.execute(query)
    entries = result.scalars().all()

    locked_years = await get_locked_years(db)

    current_year = datetime.now().year
    year_entries_result = await db.execute(
        select(MileageEntry).where(func.strftime("%Y", MileageEntry.date) == str(current_year))
    )
    year_entries = year_entries_result.scalars().all()
    year_total_km = sum(e.total_km for e in year_entries)
    year_total_amount = sum(e.amount for e in year_entries)

    filters = {"from_date": from_date, "to_date": to_date, "year": year}
    active_filters = sum(1 for v in filters.values() if v)

    return templates.TemplateResponse(request, "mileage/list.html", {
        "user": user,
        "entries": entries,
        "locked_years": locked_years,
        "filters": filters,
        "active_filters": active_filters,
        "current_year": current_year,
        "year_total_km": year_total_km,
        "year_total_amount": year_total_amount,
    })


@router.get("/export/csv")
async def export_mileage_csv(
    request: Request, db: AsyncSession = Depends(get_db),
    from_date: str = "", to_date: str = "", year: str = "",
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    query = select(MileageEntry).order_by(MileageEntry.date.desc())
    query = _apply_filters(query, from_date, to_date, year)
    result = await db.execute(query)
    entries = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Datum", "Van", "Naar", "Zakelijk doel", "Km heen", "Km terug",
                     "Totaal km", "Tarief", "Bedrag"])
    for e in entries:
        writer.writerow([
            e.date.strftime("%d-%m-%Y"), e.from_location, e.to_location,
            e.business_purpose or "", f"{e.km_outbound:.1f}", f"{e.km_return:.1f}",
            f"{e.total_km:.1f}", f"{e.rate:.2f}", f"{e.amount:.2f}",
        ])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=kilometerregistratie.csv"})


@router.get("/nieuw", response_class=HTMLResponse)
async def new_mileage_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(request, "mileage/form.html", {
        "user": user, "entry": None, "default_rate": await _default_rate(db),
    })


@router.post("/nieuw")
async def create_mileage(
    request: Request,
    date_str: str = Form(..., alias="date"),
    from_location: str = Form(...),
    to_location: str = Form(...),
    business_purpose: str = Form(""),
    km_outbound: float = Form(...),
    km_return: float = Form(0.0),
    rate: float = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    try:
        record_date = parse_date(date_str)
    except ValueError:
        return templates.TemplateResponse(request, "mileage/form.html", {
            "user": user, "entry": None, "default_rate": await _default_rate(db),
            "error": "Ongeldige datum.",
        })

    if await is_year_locked(db, record_date.year):
        return templates.TemplateResponse(request, "mileage/form.html", {
            "user": user, "entry": None, "default_rate": await _default_rate(db),
            "error": f"Boekjaar {record_date.year} is afgesloten. Ritten kunnen niet worden toegevoegd aan een afgesloten boekjaar.",
        })

    if km_outbound <= 0:
        return templates.TemplateResponse(request, "mileage/form.html", {
            "user": user, "entry": None, "default_rate": await _default_rate(db),
            "error": "Km heen moet groter zijn dan 0.",
        })

    entry = MileageEntry(
        date=record_date, from_location=from_location.strip(), to_location=to_location.strip(),
        business_purpose=business_purpose.strip() or None,
        km_outbound=km_outbound, km_return=km_return or 0.0, rate=rate,
    )
    db.add(entry)
    await db.commit()
    return RedirectResponse("/kilometers", status_code=302)


@router.get("/{id}/bewerken", response_class=HTMLResponse)
async def edit_mileage_form(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    entry = await db.get(MileageEntry, id)
    if not entry:
        raise HTTPException(404)
    year_locked = await is_year_locked(db, entry.date.year)
    return templates.TemplateResponse(request, "mileage/form.html", {
        "user": user, "entry": entry, "default_rate": await _default_rate(db),
        "year_locked": year_locked,
    })


@router.post("/{id}/bewerken")
async def update_mileage(
    id: int, request: Request,
    date_str: str = Form(..., alias="date"),
    from_location: str = Form(...),
    to_location: str = Form(...),
    business_purpose: str = Form(""),
    km_outbound: float = Form(...),
    km_return: float = Form(0.0),
    rate: float = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    entry = await db.get(MileageEntry, id)
    if not entry:
        raise HTTPException(404)

    if await is_year_locked(db, entry.date.year):
        return templates.TemplateResponse(request, "mileage/form.html", {
            "user": user, "entry": entry, "default_rate": await _default_rate(db),
            "year_locked": True,
            "error": f"Boekjaar {entry.date.year} is afgesloten. Wijzigingen zijn niet toegestaan.",
        })

    try:
        record_date = parse_date(date_str)
    except ValueError:
        return templates.TemplateResponse(request, "mileage/form.html", {
            "user": user, "entry": entry, "default_rate": await _default_rate(db),
            "error": "Ongeldige datum.",
        })

    if km_outbound <= 0:
        return templates.TemplateResponse(request, "mileage/form.html", {
            "user": user, "entry": entry, "default_rate": await _default_rate(db),
            "error": "Km heen moet groter zijn dan 0.",
        })

    entry.date = record_date
    entry.from_location = from_location.strip()
    entry.to_location = to_location.strip()
    entry.business_purpose = business_purpose.strip() or None
    entry.km_outbound = km_outbound
    entry.km_return = km_return or 0.0
    entry.rate = rate
    await db.commit()
    return RedirectResponse("/kilometers", status_code=302)


@router.post("/{id}/verwijderen")
async def delete_mileage(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    entry = await db.get(MileageEntry, id)
    if entry and await is_year_locked(db, entry.date.year):
        return RedirectResponse("/kilometers?error=vergrendeld", status_code=302)
    if entry:
        await db.delete(entry)
        await db.commit()
    return RedirectResponse("/kilometers", status_code=302)
