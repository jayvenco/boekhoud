from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from backend.models.models import Base, User, IncomeCategory, ExpenseCategory, CompanySettings, AISettings, InvoiceNumberingSettings
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
        try:
            await conn.exec_driver_sql(
                "ALTER TABLE depreciations ADD COLUMN residual_value FLOAT DEFAULT 0.0"
            )
        except Exception:
            pass
        for ddl in [
            "ALTER TABLE expenses ADD COLUMN is_recurring BOOLEAN DEFAULT 0",
            "ALTER TABLE expenses ADD COLUMN recurring_frequency VARCHAR(20)",
            "ALTER TABLE expenses ADD COLUMN recurring_start_date DATE",
            "ALTER TABLE expenses ADD COLUMN recurring_end_date DATE",
            "ALTER TABLE expenses ADD COLUMN recurring_active BOOLEAN DEFAULT 1",
            "ALTER TABLE expenses ADD COLUMN parent_recurring_expense_id INTEGER",
            "ALTER TABLE expenses ADD COLUMN is_auto_generated BOOLEAN DEFAULT 0",
        ]:
            try:
                await conn.exec_driver_sql(ddl)
            except Exception:
                pass

        # Unieke index om dubbele factuurnummers te voorkomen (race-condition-bescherming).
        # Bestaande duplicaten (zou niet mogen voorkomen) blokkeren dit niet de opstart.
        for ddl in [
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_incomes_invoice_number ON incomes(invoice_number)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_expenses_invoice_number ON expenses(invoice_number)",
        ]:
            try:
                await conn.exec_driver_sql(ddl)
            except Exception:
                pass

        # Gescheiden nummerformaten per documenttype (vervangt het oude gedeelde format_template).
        for ddl in [
            "ALTER TABLE invoice_numbering_settings ADD COLUMN format_template_income VARCHAR(50) DEFAULT 'I-{year}-{number}'",
            "ALTER TABLE invoice_numbering_settings ADD COLUMN format_template_expense VARCHAR(50) DEFAULT 'U-{year}-{number}'",
        ]:
            try:
                await conn.exec_driver_sql(ddl)
            except Exception:
                pass

        # Rekening/betaalmiddel waarmee een inkomst is ontvangen.
        for ddl in [
            "ALTER TABLE incomes ADD COLUMN received_via VARCHAR(50) DEFAULT 'zakelijke_rekening'",
            "ALTER TABLE incomes ADD COLUMN received_via_other VARCHAR(200)",
        ]:
            try:
                await conn.exec_driver_sql(ddl)
            except Exception:
                pass

        # Uitgebreide inkomsten-categorieën: omschrijving en contactgegevens.
        for ddl in [
            "ALTER TABLE income_categories ADD COLUMN description VARCHAR(500)",
            "ALTER TABLE income_categories ADD COLUMN contact_firstname VARCHAR(100)",
            "ALTER TABLE income_categories ADD COLUMN contact_lastname VARCHAR(100)",
            "ALTER TABLE income_categories ADD COLUMN phone VARCHAR(50)",
            "ALTER TABLE income_categories ADD COLUMN email VARCHAR(200)",
        ]:
            try:
                await conn.exec_driver_sql(ddl)
            except Exception:
                pass

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        # Seed income categories
        result = await session.execute(select(IncomeCategory))
        if not result.scalars().first():
            cats = [
                IncomeCategory(name="Behandelingen", slug="behandelingen"),
                IncomeCategory(name="Debiteuren", slug="debiteuren"),
            ]
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
                ExpenseCategory(name="Apparatuur", slug="apparatuur"),
                ExpenseCategory(name="Overige", slug="overige"),
            ]
            session.add_all(ecats)

        # Seed default admin user
        result = await session.execute(select(User))
        if not result.scalars().first():
            pw_hash = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode()
            admin = User(username="admin", password_hash=pw_hash)
            session.add(admin)

        # Add new categories if they don't exist yet (migration for existing DBs)
        for name, slug, model in [
            ("Apparatuur", "apparatuur", "expense"),
            ("Overige", "overige", "expense"),
            ("Debiteuren", "debiteuren", "income"),
        ]:
            if model == "expense":
                exists = await session.execute(select(ExpenseCategory).where(ExpenseCategory.slug == slug))
                if not exists.scalar_one_or_none():
                    session.add(ExpenseCategory(name=name, slug=slug))
            else:
                exists = await session.execute(select(IncomeCategory).where(IncomeCategory.slug == slug))
                if not exists.scalar_one_or_none():
                    session.add(IncomeCategory(name=name, slug=slug))

        # Seed company settings
        result = await session.execute(select(CompanySettings))
        if not result.scalars().first():
            settings = CompanySettings(company_name="Mijn Praktijk")
            session.add(settings)

        # Seed AI settings
        result = await session.execute(select(AISettings))
        if not result.scalars().first():
            session.add(AISettings(provider="openai", model="gpt-4.1-mini"))

        # Seed factuurnummering-instellingen
        result = await session.execute(select(InvoiceNumberingSettings))
        if not result.scalars().first():
            session.add(InvoiceNumberingSettings())

        await session.commit()
