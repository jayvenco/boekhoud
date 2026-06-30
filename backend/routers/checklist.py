from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from typing import Optional

from backend.models.database import get_db
from backend.models.models import ChecklistItem
from backend.routers.auth import require_auth

router = APIRouter(prefix="/checklist")
templates = Jinja2Templates(directory="backend/templates")

# Standaard controlepunten — optioneel met één klik toe te voegen bij een
# nieuw boekjaar. Volgorde bepaalt de weergavevolgorde bij aanmaken.
DEFAULT_ITEMS = [
    "Alle bonnetjes toegevoegd",
    "Alle abonnementskosten gecontroleerd",
    "Alle facturen geüpload",
    "Alle banktransacties verwerkt",
    "Alle inkomsten gecontroleerd",
    "Alle uitgaven gecontroleerd",
    "Alle terugkerende kosten gecontroleerd",
    "Ontbrekende bonnen of facturen aangevuld",
    "BTW-administratie gecontroleerd",
    "Jaarafsluiting gereed",
]


async def get_checklist_summary(db: AsyncSession) -> dict:
    result = await db.execute(select(ChecklistItem))
    items = result.scalars().all()
    total = len(items)
    completed = len([i for i in items if i.status == "voltooid"])
    open_count = total - completed
    percentage = round((completed / total) * 100) if total else 0
    return {
        "total": total, "completed": completed,
        "open": open_count, "percentage": percentage,
    }


@router.get("", response_class=HTMLResponse)
async def checklist_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    result = await db.execute(select(ChecklistItem))
    items = result.scalars().all()
    # Open items eerst, daarna voltooid; binnen elke groep nieuwste eerst.
    items = sorted(items, key=lambda i: (i.status == "voltooid", -i.id))
    summary = await get_checklist_summary(db)
    return templates.TemplateResponse(request, "checklist.html", {
        "items": items, "summary": summary,
        "default_items": DEFAULT_ITEMS,
    })


@router.post("/nieuw")
async def create_checklist_item(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    db.add(ChecklistItem(title=title.strip(), description=description.strip() or None))
    await db.commit()
    return RedirectResponse("/checklist", status_code=302)


@router.post("/standaard")
async def add_default_items(request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    existing = await db.execute(select(ChecklistItem.title))
    existing_titles = {row[0] for row in existing.all()}
    for title in DEFAULT_ITEMS:
        if title not in existing_titles:
            db.add(ChecklistItem(title=title))
    await db.commit()
    return RedirectResponse("/checklist", status_code=302)


@router.post("/{id}/bewerken")
async def update_checklist_item(
    id: int, request: Request,
    title: str = Form(...),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    item = await db.get(ChecklistItem, id)
    if not item:
        raise HTTPException(404)
    item.title = title.strip()
    item.description = description.strip() or None
    await db.commit()
    return RedirectResponse("/checklist", status_code=302)


@router.post("/{id}/toggle")
async def toggle_checklist_item(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    item = await db.get(ChecklistItem, id)
    if not item:
        raise HTTPException(404)
    if item.status == "voltooid":
        item.status = "open"
        item.completed_at = None
    else:
        item.status = "voltooid"
        item.completed_at = datetime.now()
    await db.commit()
    return RedirectResponse("/checklist", status_code=302)


@router.post("/{id}/verwijderen")
async def delete_checklist_item(id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    item = await db.get(ChecklistItem, id)
    if item:
        await db.delete(item)
        await db.commit()
    return RedirectResponse("/checklist", status_code=302)
