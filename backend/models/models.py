from sqlalchemy import Column, Integer, String, Float, Date, DateTime, Boolean, Text, ForeignKey, Enum
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func
import enum


class Base(DeclarativeBase):
    pass


class PaymentStatus(str, enum.Enum):
    betaald = "betaald"
    niet_betaald = "niet_betaald"


class PlannedStatus(str, enum.Enum):
    gepland = "gepland"
    voltooid = "voltooid"
    geannuleerd = "geannuleerd"


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
    incomes = relationship("Income", back_populates="category")


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    expenses = relationship("Expense", back_populates="category")


class Income(Base):
    __tablename__ = "incomes"
    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("income_categories.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default=PaymentStatus.niet_betaald)
    receipt_path = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    category = relationship("IncomeCategory", back_populates="incomes")


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    invoice_number = Column(String(100), nullable=False)
    category_id = Column(Integer, ForeignKey("expense_categories.id"), nullable=False)
    date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    receipt_path = Column(String(500), nullable=True)
    is_depreciable = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    category = relationship("ExpenseCategory", back_populates="expenses")
    depreciation = relationship("Depreciation", back_populates="expense", uselist=False)


class Depreciation(Base):
    __tablename__ = "depreciations"
    id = Column(Integer, primary_key=True)
    expense_id = Column(Integer, ForeignKey("expenses.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    purchase_amount = Column(Float, nullable=False)
    duration_years = Column(Integer, default=5)
    annual_percentage = Column(Float, default=20.0)
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
