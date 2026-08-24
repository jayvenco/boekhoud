from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select
from backend.models.database import AsyncSessionLocal
from backend.models.models import CompanySettings, User
from backend.services.auth import verify_session_token
from backend.services.i18n import set_locale, DEFAULT_LOCALE


class SettingsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        async with AsyncSessionLocal() as db:
            try:
                # Load company settings
                result = await db.execute(select(CompanySettings))
                request.state.settings = result.scalar_one_or_none()

                # Zet de interface-taal voor deze request (i18n).
                settings = request.state.settings
                set_locale(getattr(settings, "language", None) or DEFAULT_LOCALE)

                # Load current user from session cookie
                token = request.cookies.get("session")
                if token:
                    user_id = verify_session_token(token)
                    if user_id:
                        u = await db.execute(select(User).where(User.id == user_id))
                        request.state.user = u.scalar_one_or_none()
                    else:
                        request.state.user = None
                else:
                    request.state.user = None
            except Exception:
                request.state.settings = None
                request.state.user = None
                set_locale(DEFAULT_LOCALE)
        return await call_next(request)
