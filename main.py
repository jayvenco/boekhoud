from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
import os

from backend.models.database import init_db
from backend.routers import auth, incomes, expenses, dashboard, misc
from backend.middleware import SettingsMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Boekhoud App", lifespan=lifespan)
app.add_middleware(SettingsMiddleware)

# Static files - create dirs if missing
import pathlib
for d in ["/app/uploads", "/app/backups", "/app/data", "backend/static"]:
    pathlib.Path(d).mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")
try:
    app.mount("/uploads", StaticFiles(directory=os.getenv("UPLOAD_DIR", "/app/uploads")), name="uploads")
except Exception:
    pass

# Routers
app.include_router(auth.router)
app.include_router(incomes.router)
app.include_router(expenses.router)
app.include_router(dashboard.router)
app.include_router(misc.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
