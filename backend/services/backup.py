import asyncio
import hashlib
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from backend.models.models import BackupRecord, BackupSettings

logger = logging.getLogger("boekhoud.backup")

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/app/backups"))

AUTO_JOB_ID = "auto_backup"


class BackupError(Exception):
    pass


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_backup_zip(zip_path: Path):
    db_path = DATA_DIR / "boekhoud.db"
    if not db_path.exists():
        raise BackupError("Database bestand niet gevonden.")

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = Path(tmp) / "boekhoud.db"
        src_conn = sqlite3.connect(str(db_path))
        dst_conn = sqlite3.connect(str(snapshot_path))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot_path, arcname="boekhoud.db")
            if UPLOAD_DIR.exists():
                for root, dirs, files in os.walk(UPLOAD_DIR):
                    dirs[:] = [d for d in dirs if d != "tmp"]
                    for fname in files:
                        full = Path(root) / fname
                        arcname = Path("uploads") / full.relative_to(UPLOAD_DIR)
                        zf.write(full, arcname=str(arcname))


def _verify_backup_file(path: Path, expected_checksum: str = None):
    if not path.exists():
        return False, "Back-upbestand niet gevonden."
    if not zipfile.is_zipfile(path):
        return False, "Back-upbestand is geen geldig zip-archief."
    try:
        with zipfile.ZipFile(path) as zf:
            bad_file = zf.testzip()
            if bad_file:
                return False, f"Corrupt bestand in back-up: {bad_file}"
            if "boekhoud.db" not in zf.namelist():
                return False, "Back-up bevat geen database-bestand."
    except zipfile.BadZipFile:
        return False, "Back-upbestand is corrupt."

    if expected_checksum:
        actual = _sha256_of_file(path)
        if actual != expected_checksum:
            return False, "Checksum komt niet overeen — back-up is mogelijk gewijzigd of corrupt."

    return True, "OK"


def _extract_backup(zip_path: Path):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)

        extracted_db = tmp_path / "boekhoud.db"
        if not extracted_db.exists():
            raise BackupError("Geëxtraheerde back-up bevat geen database-bestand.")

        conn = sqlite3.connect(str(extracted_db))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupError("Database-integriteitscheck van de back-up is mislukt.")
        finally:
            conn.close()

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted_db, DATA_DIR / "boekhoud.db")

        extracted_uploads = tmp_path / "uploads"
        if extracted_uploads.exists():
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            for item in UPLOAD_DIR.iterdir():
                if item.name == "tmp":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            for item in extracted_uploads.iterdir():
                dest = UPLOAD_DIR / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)


async def list_backups(db):
    result = await db.execute(select(BackupRecord).order_by(BackupRecord.id.desc()))
    return result.scalars().all()


async def apply_retention(db):
    settings = (await db.execute(select(BackupSettings))).scalar_one_or_none()
    if not settings or settings.retention_type == "onbeperkt":
        return

    if settings.retention_type == "aangepast":
        keep = settings.retention_custom_count or 10
    else:
        keep = int(settings.retention_type)

    result = await db.execute(select(BackupRecord).order_by(BackupRecord.id.desc()))
    records = result.scalars().all()
    for old in records[keep:]:
        path = BACKUP_DIR / old.filename
        if path.exists():
            path.unlink(missing_ok=True)
        await db.delete(old)
        logger.info(f"Backup verwijderd door retentiebeleid: {old.filename}")
    await db.commit()


async def create_backup(db, backup_type: str, note: str = None) -> BackupRecord:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    filename = f"backup_{backup_type}_{ts}_{uid}.zip"
    zip_path = BACKUP_DIR / filename

    try:
        await asyncio.to_thread(_build_backup_zip, zip_path)
        size_bytes = zip_path.stat().st_size
        checksum = await asyncio.to_thread(_sha256_of_file, zip_path)
        record = BackupRecord(
            filename=filename, type=backup_type, status="voltooid",
            size_bytes=size_bytes, checksum=checksum, note=note,
        )
        logger.info(f"Backup aangemaakt: {filename} ({backup_type}, {size_bytes} bytes)")
    except Exception as e:
        record = BackupRecord(
            filename=filename, type=backup_type, status="mislukt",
            size_bytes=0, note=f"Back-up mislukt: {e}",
        )
        logger.error(f"Backup mislukt ({backup_type}): {e}")

    db.add(record)
    await db.flush()
    await apply_retention(db)
    await db.commit()
    return record


async def restore_backup(db, backup_id: int) -> BackupRecord:
    record = await db.get(BackupRecord, backup_id)
    if not record:
        raise BackupError("Back-up niet gevonden.")
    if record.status != "voltooid":
        raise BackupError("Deze back-up heeft geen voltooide status en kan niet worden hersteld.")

    zip_path = BACKUP_DIR / record.filename
    ok, message = await asyncio.to_thread(_verify_backup_file, zip_path, record.checksum)
    if not ok:
        logger.error(f"Herstel geweigerd voor backup {record.filename}: {message}")
        raise BackupError(message)

    safety = await create_backup(
        db, "automatisch", note=f"Veiligheidsback-up vóór herstel van back-up #{record.id}"
    )
    if safety.status != "voltooid":
        raise BackupError("Veiligheidsback-up mislukt — herstel afgebroken om dataverlies te voorkomen.")

    try:
        await asyncio.to_thread(_extract_backup, zip_path)
    except Exception as e:
        logger.error(f"Herstel mislukt voor backup {record.filename}: {e}")
        raise BackupError(f"Herstellen mislukt: {e}")

    from backend.models.database import engine
    await engine.dispose()

    logger.info(f"Backup #{record.id} ({record.filename}) succesvol herstreld.")
    return record


async def _run_scheduled_backup():
    from backend.models.backup_database import BackupSessionLocal
    async with BackupSessionLocal() as session:
        await create_backup(session, "automatisch")


def configure_backup_job(scheduler, frequency: str):
    try:
        scheduler.remove_job(AUTO_JOB_ID)
    except Exception:
        pass

    if frequency == "elk_uur":
        scheduler.add_job(_run_scheduled_backup, "interval", hours=1, id=AUTO_JOB_ID, replace_existing=True)
    elif frequency == "dagelijks":
        scheduler.add_job(_run_scheduled_backup, "cron", hour=2, minute=0, id=AUTO_JOB_ID, replace_existing=True)
    elif frequency == "wekelijks":
        scheduler.add_job(_run_scheduled_backup, "cron", day_of_week="sun", hour=2, minute=0,
                           id=AUTO_JOB_ID, replace_existing=True)
    elif frequency == "maandelijks":
        scheduler.add_job(_run_scheduled_backup, "cron", day=1, hour=2, minute=0,
                           id=AUTO_JOB_ID, replace_existing=True)
    # "handmatig" -> geen geplande job
    logger.info(f"Backupschema ingesteld op: {frequency}")
