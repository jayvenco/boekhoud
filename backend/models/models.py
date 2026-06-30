from sqlalchemy import Column, Integer, String, Float, Date, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func
import enum


class Base(DeclarativeBase):
    pass


class BackupBase(DeclarativeBase):
    """Apart van Base: backup-metadata leeft in een eigen database buiten
    DATA_DIR, zodat de backupcatalogus een restore van boekhoud.db overleeft."""
    pass


class PaymentStatus(str, enum.Enum):
    betaald = "betaald"
    niet_betaald = "niet_betaald"


class PlannedStatus(str, enum.Enum):
    gepland = "gepland"
    voltooid = "voltooid"
    geannuleerd = "geannuleerd"


class BackupFrequency(str, enum.Enum):
    handmatig = "handmatig"
    elk_uur = "elk_uur"
    dagelijks = "dagelijks"
    wekelijks = "wekelijks"
    maandelijks = "maandelijks"


class BackupRetention(str, enum.Enum):
    vijf = "5"
    tien = "10"
    vijfentwintig = "25"
    vijftig = "50"
    onbeperkt = "onbeperkt"
    aangepast = "aangepast"


class BackupType(str, enum.Enum):
    handmatig = "handmatig"
    automatisch = "automatisch"


class BackupStatus(str, enum.Enum):
    voltooid = "voltooid"
    mislukt = "mislukt"
    corrupt = "corrupt"


class AIProviderName(str, enum.Enum):
    openai = "openai"
    anthropic = "anthropic"


class AIConnectionStatus(str, enum.Enum):
    verbonden = "verbonden"
    ongeldig = "ongeldig"
    onbereikbaar = "onbereikbaar"
    niet_geconfigureerd = "niet_geconfigureerd"
    onbekend = "onbekend"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class CompanySettings(Base):
    __tablename__ = "company_settings"
    id = Column(Integer, primary_key=True)
    company_name = Column(String(200), default="Mijn Bedrijf")
    logo_path = Column(String(500), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class IncomeCategory(Base):
    __tablename__ = "income_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    description = Column(String(500), nullable=True)
    contact_firstname = Column(String(100), nullable=True)
    contact_lastname = Column(String(100), nullable=True)
    phone = Column(String(50), nullable=True)
    email = Column(String(200), nullable=True)
    incomes = relationship("Income", back_populates="category")


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    expenses = relationship("Expense", back_populates="category")


class ReceivedVia(str, enum.Enum):
    zakelijke_rekening = "zakelijke_rekening"
    priverekening = "priverekening"
    creditcard = "creditcard"
    rekening_partner = "rekening_partner"
    externe_praktijk = "externe_praktijk"
    sumup = "sumup"
    payt = "payt"
    infomedics = "infomedics"
    overig = "overig"


class Income(Base):
    __tablename__ = "incomes"
    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), nullable=False, unique=True)
    category_id = Column(Integer, ForeignKey("income_categories.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default=PaymentStatus.niet_betaald)
    received_via = Column(String(50), nullable=False, default=ReceivedVia.zakelijke_rekening)
    received_via_other = Column(String(200), nullable=True)
    receipt_path = Column(String(500), nullable=True)  # legacy single receipt
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    category = relationship("IncomeCategory", back_populates="incomes")
    receipts = relationship("IncomeReceipt", back_populates="income", cascade="all, delete-orphan")


class RecurringFrequency(str, enum.Enum):
    maandelijks = "maandelijks"
    per_kwartaal = "per_kwartaal"
    halfjaarlijks = "halfjaarlijks"
    jaarlijks = "jaarlijks"


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), nullable=False, unique=True)
    category_id = Column(Integer, ForeignKey("expense_categories.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    receipt_path = Column(String(500), nullable=True)
    is_depreciable = Column(Boolean, default=False)
    is_recurring = Column(Boolean, default=False)
    recurring_frequency = Column(String(20), nullable=True)
    recurring_start_date = Column(Date, nullable=True)
    recurring_end_date = Column(Date, nullable=True)
    recurring_active = Column(Boolean, default=True)
    parent_recurring_expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=True)
    is_auto_generated = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    category = relationship("ExpenseCategory", back_populates="expenses")
    depreciation = relationship("Depreciation", back_populates="expense", uselist=False)
    receipts = relationship("ExpenseReceipt", back_populates="expense", cascade="all, delete-orphan")


class IncomeReceipt(Base):
    __tablename__ = "income_receipts"
    id = Column(Integer, primary_key=True)
    income_id = Column(Integer, ForeignKey("incomes.id"), nullable=False)
    file_path = Column(String(500), nullable=False)
    suffix = Column(String(5), nullable=True)   # a, b, c, ...
    created_at = Column(DateTime, server_default=func.now())
    income = relationship("Income", back_populates="receipts")


class ExpenseReceipt(Base):
    __tablename__ = "expense_receipts"
    id = Column(Integer, primary_key=True)
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=False)
    file_path = Column(String(500), nullable=False)
    suffix = Column(String(5), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    expense = relationship("Expense", back_populates="receipts")


class Depreciation(Base):
    __tablename__ = "depreciations"
    id = Column(Integer, primary_key=True)
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    purchase_amount = Column(Float, nullable=False)
    duration_years = Column(Integer, default=5)
    annual_percentage = Column(Float, default=20.0)
    residual_value = Column(Float, default=0.0)
    expense = relationship("Expense", back_populates="depreciation")


class PlannedExpense(Base):
    __tablename__ = "planned_expenses"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    amount = Column(Float, nullable=True)
    planned_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(20), default=PlannedStatus.gepland)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BackupSettings(BackupBase):
    __tablename__ = "backup_settings"
    id = Column(Integer, primary_key=True)
    frequency = Column(String(20), default=BackupFrequency.wekelijks)
    retention_type = Column(String(20), default=BackupRetention.tien)
    retention_custom_count = Column(Integer, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BackupRecord(BackupBase):
    __tablename__ = "backup_records"
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    type = Column(String(20), nullable=False)
    status = Column(String(20), default=BackupStatus.voltooid)
    size_bytes = Column(Integer, default=0)
    checksum = Column(String(64), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class AISettings(Base):
    __tablename__ = "ai_settings"
    id = Column(Integer, primary_key=True)
    provider = Column(String(20), default=AIProviderName.openai)
    model = Column(String(100), nullable=True)
    openai_api_key_encrypted = Column(Text, nullable=True)
    anthropic_api_key_encrypted = Column(Text, nullable=True)
    last_status = Column(String(20), default=AIConnectionStatus.niet_geconfigureerd)
    last_status_message = Column(String(255), nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ChecklistStatus(str, enum.Enum):
    open = "open"
    voltooid = "voltooid"


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default=ChecklistStatus.open)
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class YearClosure(Base):
    __tablename__ = "year_closures"
    id = Column(Integer, primary_key=True)
    year = Column(Integer, unique=True, nullable=False)
    closed_at = Column(DateTime, server_default=func.now())
    open_items_at_closure = Column(Integer, default=0)


class InvoiceNumberingSettings(Base):
    __tablename__ = "invoice_numbering_settings"
    id = Column(Integer, primary_key=True)
    auto_enabled = Column(Boolean, default=True)
    padding = Column(Integer, default=3)
    format_template_income = Column(String(50), default="I-{year}-{number}")
    format_template_expense = Column(String(50), default="U-{year}-{number}")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
