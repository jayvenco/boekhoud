"""
Middleware to inject company settings into all template responses.
We patch Jinja2's TemplateResponse to include global context.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select
from backend.models.database import AsyncSessionLocal
from backend.models.models import CompanySettings


class SettingsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Inject settings into request state so templates can access it
        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(select(CompanySettings))
                settings = result.scalar_one_or_none()
                request.state.settings = settings
            except Exception:
                request.state.settings = None
        return await call_next(request)
