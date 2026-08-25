from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import date, datetime
from typing import Optional
import csv
import io

from backend.models.database import get_db
from backend.models.models import TimeEntry, HourCategory
from backend.routers.auth import require_auth
from backend.services.fiscal_year import is_year_locked, get_locked_years

router = APIRouter(prefix="/uren")
templates = Jinja2Templates(directory="backend/templates")

URENCRITERIUM_UREN = 1225


def parse_date(d: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Ongeldige datum: {d}")


def _apply_filters(query, from_date, to_date, category_id, year):
    if from_date:
        try:
            query = query.where(TimeEntry.date >= parse_date(from_date))
        except ValueError:
            pass
    if to_date:
        try:
            query = query.where(TimeEntry.date <= parse_date(to_date))
        except ValueError:
            pass
    if category_id:
        try:
            query = query.where(TimeEntry.category_id == int(category_id))
        except ValueError:
            pass
    if year:
        query = query.where(func.strftime("%Y", TimeEntry.date) == str(year))
    return query


@router.get("", response_class=HTMLResponse)
async def list_hours(
    request: Request, db: AsyncSession = Depends(get_db),
    from_date: str = "", to_date: str = "", category_id: str = "", year: str = "",
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    query = select(TimeEntry).options(selectinload(TimeEntry.category)).order_by(TimeEntry.date.desc())
    query = _apply_filters(query, from_date, to_date, category_id, year)
    result = await db.execute(query)
    entries = result.scalars().all()

    cats = await db.execute(select(HourCategory).order_by(HourCategory.name))
    locked_years = await get_locked_years(db)

    current_year = datetime.now().year
    year_total_result = await db.execute(
        select(func.sum(TimeEntry.hours)).where(func.strftime("%Y", TimeEntry.date) == str(current_year))
    )
    year_total = year_total_result.scalar() or 0.0

    filters = {"from_date": from_date, "to_date": to_date, "category_id": category_id, "year": year}
    active_filters = sum(1 for v in filters.values() if v)

    return templates.TemplateResponse(request, "hours/list.html", {
        "user": user,
        "entries": entries,
        "categories": cats.scalars().all(),
        "locked_years": locked_years,
        "filters": filters,
        "active_filters": active_filters,
        "year_total": year_total,
        "current_year": current_year,
        "urencriterium": URENCRITERIUM_UREN,
        "urencriterium_pct": min(100, round(year_total / URENCRITERIUM_UREN * 100)) if URENCRITERIUM_UREN else 0,
    })


@router.get("/export/csv")
async def export_hours_csv(
    request: Request, db: AsyncSession = Depends(get_db),
    from_date: str = "", to_date: str = "", category_id: str = "", year: str = "",
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    query = select(TimeEntry).options(selectinload(TimeEntry.category)).order_by(TimeEntry.date.desc())
    query = _apply_filters(query, from_date, to_date, category_id, year)
    result = await db.execute(query)
    entries = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Datum", "Categorie", "Uren", "Omschrijving"])
    for e in entries:
        writer.writerow([e.date.strftime("%d-%m-%Y"), e.category.name,
                         f"{e.hours:.2f}", e.description or ""])
    output.seek(0)
    return StreamingResponse(io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=urenregistratie.csv"})


@router.get("/nieuw", response_class=HTMLResponse)
async def new_hour_form(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cats = await db.execute(select(HourCategory).order_by(HourCategory.name))
    return templates.TemplateResponse(request, "hours/form.html", {
        "user": user, "categories": cats.scalars().all(), "entry": None,
    })


@router.post("/nieuw")
async def create_hour(
    request: Request,
    date_str: str = Form(..., alias="date"),
    hours: float = Form(...),
    category_id: int = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    try:
        record_date = parse_date(date_str)
    except ValueError:
        cats = await db.execute(select(HourCategory).order_by(HourCategory.name))
        return templates.TemplateResponse(request, "hours/form.html", {
            "user": user, "categories": cats.scalars().all(), "entry": None,
            "error": "Ongeldige datum.",
        })

    if await is_year_locked(db, record_date.year):
        cats = await db.execute(select(HourCategory).order_by(HourCategory.name))
        return templates.TemplateResponse(request, "hours/form.html", {
            "user": user, "categories": cats.scalars().all(), "entry": None,
            "error": f"Boekjaar {record_date.year} is afgesloten. Uren kunnen niet worden toegevoegd aan een afgesloten boekjaar.",
        })

    if hours <= 0:
        cats = await db.execute(select(HourCategory).order_by(HourCategory.name))
        return templates.TemplateResponse(request, "hours/form.html", {
            "user": user, "categories": cats.scalars().all(), "entry": None,
            "error": "Gewerkte uren moet groter zijn dan 0.",
        })

    entry = TimeEntry(date=record_date, hours=hours, category_id=category_id,
                      description=description.strip() or None)
    db.add(entry)
    await db.commit()
    return RedirectResponse("/uren", status_code=302)


@router.get("/{id}/bewerken", response_class=HTMLResponse)
async def edit_hour_form(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(
        select(TimeEntry).options(selectinload(TimeEntry.category)).where(TimeEntry.id == id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404)
    cats = await db.execute(select(HourCategory).order_by(HourCategory.name))
    year_locked = await is_year_locked(db, entry.date.year)
    return templates.TemplateResponse(request, "hours/form.html", {
        "user": user, "categories": cats.scalars().all(), "entry": entry,
        "year_locked": year_locked,
    })


@router.post("/{id}/bewerken")
async def update_hour(
    id: int, request: Request,
    date_str: str = Form(..., alias="date"),
    hours: float = Form(...),
    category_id: int = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(TimeEntry).where(TimeEntry.id == id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(404)

    if await is_year_locked(db, entry.date.year):
        cats = await db.execute(select(HourCategory).order_by(HourCategory.name))
        result2 = await db.execute(
            select(TimeEntry).options(selectinload(TimeEntry.category)).where(TimeEntry.id == id)
        )
        return templates.TemplateResponse(request, "hours/form.html", {
            "user": user, "categories": cats.scalars().all(), "entry": result2.scalar_one_or_none(),
            "year_locked": True,
            "error": f"Boekjaar {entry.date.year} is afgesloten. Wijzigingen zijn niet toegestaan.",
        })

    try:
        record_date = parse_date(date_str)
    except ValueError:
        cats = await db.execute(select(HourCategory).order_by(HourCategory.name))
        return templates.TemplateResponse(request, "hours/form.html", {
            "user": user, "categories": cats.scalars().all(), "entry": entry,
            "error": "Ongeldige datum.",
        })

    entry.date = record_date
    entry.hours = hours
    entry.category_id = category_id
    entry.description = description.strip() or None
    await db.commit()
    return RedirectResponse("/uren", status_code=302)


@router.post("/{id}/verwijderen")
async def delete_hour(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(TimeEntry).where(TimeEntry.id == id))
    entry = result.scalar_one_or_none()
    if entry and await is_year_locked(db, entry.date.year):
        return RedirectResponse("/uren?error=vergrendeld", status_code=302)
    if entry:
        await db.delete(entry)
        await db.commit()
    return RedirectResponse("/uren", status_code=302)


# ── Uren-categorieën (beheer via Instellingen) ─────────────────

def _slugify(text: str) -> str:
    import re, unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", "_", text)
    return text or "categorie"


async def _unique_slug(db: AsyncSession, base_slug: str, exclude_id: int = None) -> str:
    slug = base_slug
    i = 2
    while True:
        q = select(HourCategory).where(HourCategory.slug == slug)
        if exclude_id:
            q = q.where(HourCategory.id != exclude_id)
        res = await db.execute(q)
        if not res.scalar_one_or_none():
            return slug
        slug = f"{base_slug}_{i}"
        i += 1


@router.post("/categorieen/nieuw")
async def create_hour_category(
    request: Request, name: str = Form(...), db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    name = name.strip()
    if not name:
        return RedirectResponse("/instellingen?urencat_error=naam_verplicht", status_code=302)
    existing = await db.execute(select(HourCategory).where(HourCategory.name == name))
    if existing.scalar_one_or_none():
        return RedirectResponse("/instellingen?urencat_error=naam_bestaat", status_code=302)
    slug = await _unique_slug(db, _slugify(name))
    db.add(HourCategory(name=name, slug=slug))
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


@router.post("/categorieen/{id}/bewerken")
async def update_hour_category(
    id: int, request: Request, name: str = Form(...), db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cat = await db.get(HourCategory, id)
    if not cat:
        raise HTTPException(404)
    name = name.strip()
    if not name:
        return RedirectResponse(f"/instellingen?urencat_error=naam_verplicht&urencat_id={id}", status_code=302)
    existing = await db.execute(select(HourCategory).where(HourCategory.name == name, HourCategory.id != id))
    if existing.scalar_one_or_none():
        return RedirectResponse(f"/instellingen?urencat_error=naam_bestaat&urencat_id={id}", status_code=302)
    if cat.name != name:
        cat.slug = await _unique_slug(db, _slugify(name), exclude_id=id)
    cat.name = name
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


@router.post("/categorieen/{id}/verwijderen")
async def delete_hour_category(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    cat = await db.get(HourCategory, id)
    if not cat:
        raise HTTPException(404)
    linked = await db.execute(select(TimeEntry).where(TimeEntry.category_id == id))
    if linked.scalar_one_or_none():
        return RedirectResponse("/instellingen?urencat_error=heeft_uren", status_code=302)
    await db.delete(cat)
    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)
