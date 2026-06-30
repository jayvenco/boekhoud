from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from backend.models.database import get_db
from backend.models.models import AISettings
from backend.routers.auth import require_auth
from backend.services.ai_providers import PROVIDERS, get_provider
from backend.services.crypto import encrypt, decrypt

router = APIRouter()


@router.post("/instellingen/ai")
async def update_ai_settings(
    request: Request,
    provider: str = Form(...),
    model: str = Form(...),
    openai_api_key: Optional[str] = Form(None),
    anthropic_api_key: Optional[str] = Form(None),
    openai_api_key_clear: Optional[str] = Form(None),
    anthropic_api_key_clear: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user

    if provider not in PROVIDERS:
        provider = "openai"

    result = await db.execute(select(AISettings))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = AISettings()
        db.add(settings)

    settings.provider = provider
    settings.model = model

    if openai_api_key_clear == "on":
        settings.openai_api_key_encrypted = None
    elif openai_api_key and openai_api_key.strip():
        settings.openai_api_key_encrypted = encrypt(openai_api_key.strip())

    if anthropic_api_key_clear == "on":
        settings.anthropic_api_key_encrypted = None
    elif anthropic_api_key and anthropic_api_key.strip():
        settings.anthropic_api_key_encrypted = encrypt(anthropic_api_key.strip())

    active_key_encrypted = (
        settings.openai_api_key_encrypted if provider == "openai"
        else settings.anthropic_api_key_encrypted
    )
    if not active_key_encrypted:
        settings.last_status = "niet_geconfigureerd"
        settings.last_status_message = "Geen API-sleutel ingesteld."
    else:
        settings.last_status = "onbekend"
        settings.last_status_message = "Nog niet getest — klik op 'Testverbinding'."

    await db.commit()
    return RedirectResponse("/instellingen?success=1", status_code=302)


@router.post("/instellingen/ai/test")
async def test_ai_connection(
    request: Request,
    provider: str = Form(...),
    api_key: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return JSONResponse({"ok": False, "status": "niet_geconfigureerd", "message": "Niet ingelogd"}, status_code=401)

    prov = get_provider(provider)
    if not prov:
        return JSONResponse({"ok": False, "status": "onbereikbaar", "message": "Onbekende provider."})

    key = api_key.strip()
    result = await db.execute(select(AISettings))
    settings = result.scalar_one_or_none()

    if not key and settings:
        stored = (
            settings.openai_api_key_encrypted if provider == "openai"
            else settings.anthropic_api_key_encrypted
        )
        key = decrypt(stored) if stored else ""

    status, message = await prov.test_connection(key)

    if not settings:
        settings = AISettings(provider=provider)
        db.add(settings)
    settings.last_status = status
    settings.last_status_message = message
    await db.commit()

    return JSONResponse({"ok": status == "verbonden", "status": status, "message": message})
