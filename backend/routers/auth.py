from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.models.database import get_db
from backend.models.models import User
from backend.services.auth import verify_password, hash_password, create_session_token, verify_session_token

router = APIRouter()
templates = Jinja2Templates(directory="backend/templates")


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("session")
    if not token:
        return None
    user_id = verify_session_token(token)
    if not user_id:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def require_auth(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return user


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Ongeldige gebruikersnaam of wachtwoord."
        })
    token = create_session_token(user.id)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("session", token, httponly=True, max_age=86400 * 7, samesite="lax")
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session")
    return response


@router.post("/settings/password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    user = await require_auth(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if not verify_password(current_password, user.password_hash):
        result = await db.execute(select(User))
        return templates.TemplateResponse("settings.html", {
            "request": request,
            "user": user,
            "pw_error": "Huidig wachtwoord is onjuist."
        })
    user.password_hash = hash_password(new_password)
    await db.commit()
    return RedirectResponse("/settings?success=1", status_code=302)
