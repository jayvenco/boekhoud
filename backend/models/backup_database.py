from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select
from backend.models.models import BackupBase, BackupSettings
from pathlib import Path
import os

BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/app/backups"))
BACKUP_DB_URL = f"sqlite+aiosqlite:///{BACKUP_DIR}/backup_meta.db"

backup_engine = create_async_engine(BACKUP_DB_URL, echo=False, connect_args={"check_same_thread": False})
BackupSessionLocal = async_sessionmaker(backup_engine, expire_on_commit=False)


async def get_backup_db():
    async with BackupSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_backup_db():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    async with backup_engine.begin() as conn:
        await conn.run_sync(BackupBase.metadata.create_all)

    async with BackupSessionLocal() as session:
        result = await session.execute(select(BackupSettings))
        if not result.scalars().first():
            session.add(BackupSettings(frequency="wekelijks", retention_type="10"))
            await session.commit()
