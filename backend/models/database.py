from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from backend.models.models import Base, User, IncomeCategory, ExpenseCategory, CompanySettings
import bcrypt
import os

DATABASE_URL = f"sqlite+aiosqlite:///{os.getenv('DATA_DIR', '/app/data')}/boekhoud.db"

engine = create_async_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        # Seed income categories
        result = await session.execute(select(IncomeCategory))
        if not result.scalars().first():
            cats = [IncomeCategory(name="Behandelingen", slug="behandelingen")]
            session.add_all(cats)

        # Seed expense categories
        result = await session.execute(select(ExpenseCategory))
        if not result.scalars().first():
            ecats = [
                ExpenseCategory(name="Praktijkinrichting", slug="praktijkinrichting"),
                ExpenseCategory(name="Vaste lasten", slug="vaste_lasten"),
                ExpenseCategory(name="Abonnementen", slug="abonnementen"),
                ExpenseCategory(name="Materiaal", slug="materiaal"),
                ExpenseCategory(name="Materieel", slug="materieel"),
                ExpenseCategory(name="Marketing", slug="marketing"),
                ExpenseCategory(name="Reiskosten", slug="reiskosten"),
            ]
            session.add_all(ecats)

        # Seed default admin user
        result = await session.execute(select(User))
        if not result.scalars().first():
            pw_hash = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode()
            admin = User(username="admin", password_hash=pw_hash)
            session.add(admin)

        # Seed company settings
        result = await session.execute(select(CompanySettings))
        if not result.scalars().first():
            settings = CompanySettings(company_name="Mijn Praktijk")
            session.add(settings)

        await session.commit()
