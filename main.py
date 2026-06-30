from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, FileResponse
from contextlib import asynccontextmanager
import os
import pathlib
import logging
import logging.handlers
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from backend.models.database import init_db
from backend.models.backup_database import init_backup_db, BackupSessionLocal
from backend.models.models import BackupSettings
from backend.routers import auth, incomes, expenses, dashboard, misc, backups, ai_settings, checklist
from backend.routers import fiscal_years
from backend.middleware import SettingsMiddleware
from backend.services.backup import configure_backup_job

UPLOAD_DIR = pathlib.Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", "/app/data"))
BACKUP_DIR = pathlib.Path(os.getenv("BACKUP_DIR", "/app/backups"))

# Create all required directories immediately at import time
LOG_DIR = pathlib.Path(os.getenv("LOG_DIR", "/app/logs"))

for _d in [
    UPLOAD_DIR, DATA_DIR, BACKUP_DIR, LOG_DIR, pathlib.Path("backend/static"),
    UPLOAD_DIR / "uitgaven" / "apparatuur",
    UPLOAD_DIR / "uitgaven" / "overige",
]:
    _d.mkdir(parents=True, exist_ok=True)


# ── Logging setup ────────────────────────────────────────────
def setup_logging():
    log_file = LOG_DIR / "boekhoud.log"
    # Rotate daily, keep 30 days
    handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)

    # Root logger — catches uvicorn, fastapi, sqlalchemy, and app logs
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    # Also keep console output
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root_logger.addHandler(console)

setup_logging()
logger = logging.getLogger("boekhoud")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Boekhoud App wordt gestart...")
    await init_db()
    await init_backup_db()
    logger.info("Database geïnitialiseerd")

    scheduler = AsyncIOScheduler()
    scheduler.start()
    app.state.scheduler = scheduler

    async with BackupSessionLocal() as session:
        result = await session.execute(select(BackupSettings))
        settings = result.scalar_one_or_none()
        frequency = settings.frequency if settings else "wekelijks"
    configure_backup_job(scheduler, frequency)
    logger.info(f"Backup scheduler gestart (frequentie: {frequency})")

    yield
    logger.info("Boekhoud App wordt afgesloten")
    scheduler.shutdown()


app = FastAPI(title="Boekhoud App", lifespan=lifespan)
app.add_middleware(SettingsMiddleware)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")


@app.get("/uploads/{full_path:path}")
async def serve_upload(full_path: str):
    from fastapi import HTTPException
    file_path = (UPLOAD_DIR / full_path).resolve()
    if not str(file_path).startswith(str(UPLOAD_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Toegang geweigerd")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Bestand niet gevonden")
    return FileResponse(file_path)


app.include_router(auth.router)
app.include_router(incomes.router)
app.include_router(expenses.router)
app.include_router(dashboard.router)
app.include_router(misc.router)
app.include_router(backups.router)
app.include_router(ai_settings.router)
app.include_router(checklist.router)
app.include_router(fiscal_years.router)


@app.get("/manifest.json")
async def manifest():
    return FileResponse("backend/static/manifest.json", media_type="application/manifest+json")

@app.get("/sw.js")
async def service_worker():
    resp = FileResponse("backend/static/sw.js", media_type="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp

@app.get("/health")
async def health():
    return {"status": "ok"}
