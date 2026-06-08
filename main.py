from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse
from contextlib import asynccontextmanager
import os
import pathlib

from backend.models.database import init_db
from backend.routers import auth, incomes, expenses, dashboard, misc
from backend.middleware import SettingsMiddleware

UPLOAD_DIR = pathlib.Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", "/app/data"))
BACKUP_DIR = pathlib.Path(os.getenv("BACKUP_DIR", "/app/backups"))

# Create all required directories immediately at import time
for _d in [UPLOAD_DIR, DATA_DIR, BACKUP_DIR, pathlib.Path("backend/static")]:
    _d.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Boekhoud App", lifespan=lifespan)
app.add_middleware(SettingsMiddleware)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")


@app.get("/uploads/{full_path:path}")
async def serve_upload(full_path: str):
    file_path = UPLOAD_DIR / full_path
    if not file_path.exists() or not file_path.is_file():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Bestand niet gevonden")
    return FileResponse(file_path)


app.include_router(auth.router)
app.include_router(incomes.router)
app.include_router(expenses.router)
app.include_router(dashboard.router)
app.include_router(misc.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
