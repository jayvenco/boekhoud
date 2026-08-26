import os
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select
from backend.models.database import AsyncSessionLocal
from backend.models.models import CompanySettings, User
from backend.services.auth import verify_session_token

# Versie-informatie wordt bij de Docker-build meegegeven (zie Dockerfile +
# .github/workflows/docker-publish.yml) en verandert niet tijdens runtime,
# dus eenmalig berekenen bij het opstarten van de app is voldoende.
_GIT_SHA = os.getenv("GIT_SHA", "lokaal")
_GIT_BRANCH = os.getenv("GIT_BRANCH", "onbekend")
APP_VERSION = _GIT_SHA if _GIT_SHA == "lokaal" else _GIT_SHA[:7]
APP_BRANCH_LABEL = {"main": "productie", "staging": "staging"}.get(_GIT_BRANCH, _GIT_BRANCH)


class SettingsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.app_version = APP_VERSION
        request.state.app_branch = APP_BRANCH_LABEL
        async with AsyncSessionLocal() as db:
            try:
                # Load company settings
                result = await db.execute(select(CompanySettings))
                request.state.settings = result.scalar_one_or_none()

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
        return await call_next(request)
