"""Detectie van dubbele bonnen/facturen bij AI/OCR-verwerking.

Waarschuwt (blokkeert niet) wanneer een geüploade bon waarschijnlijk al is
geregistreerd, op basis van:
  1. Bestandshash (SHA-256) — exact hetzelfde bestand is al gekoppeld.
  2. Origineel factuurnummer — dezelfde factuur, ongeacht het bestand.
  3. Zelfde bedrag én datum — mogelijk duplicaat zonder herkend factuurnummer.
"""
import hashlib
from datetime import date as _date, datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.models import (
    Income, Expense, IncomeReceipt, ExpenseReceipt,
)


def compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _match_file_hash(db: AsyncSession, file_hash: str) -> Optional[str]:
    if not file_hash:
        return None
    # Inkomsten-bonnen
    r = await db.execute(
        select(Income.invoice_number)
        .join(IncomeReceipt, IncomeReceipt.income_id == Income.id)
        .where(IncomeReceipt.file_hash == file_hash)
    )
    inv = r.scalars().first()
    if inv:
        return f"Ditzelfde bestand is al gekoppeld aan inkomst {inv}."
    # Uitgaven-bonnen
    r = await db.execute(
        select(Expense.invoice_number)
        .join(ExpenseReceipt, ExpenseReceipt.expense_id == Expense.id)
        .where(ExpenseReceipt.file_hash == file_hash)
    )
    exp = r.scalars().first()
    if exp:
        return f"Ditzelfde bestand is al gekoppeld aan uitgave {exp}."
    return None


async def _match_invoice_number(db: AsyncSession, invoice_number: str) -> Optional[str]:
    if not invoice_number:
        return None
    num = invoice_number.strip()
    if not num:
        return None
    r = await db.execute(
        select(Income.invoice_number).where(Income.supplier_invoice_number == num)
    )
    inv = r.scalars().first()
    if inv:
        return f"Origineel factuurnummer '{num}' is al geregistreerd als inkomst {inv}."
    r = await db.execute(
        select(Expense.invoice_number).where(Expense.supplier_invoice_number == num)
    )
    exp = r.scalars().first()
    if exp:
        return f"Origineel factuurnummer '{num}' is al geregistreerd als uitgave {exp}."
    return None


async def _match_amount_date(db: AsyncSession, amount, record_date) -> Optional[str]:
    if amount is None or record_date is None:
        return None
    r = await db.execute(
        select(Income.invoice_number).where(
            Income.amount == amount, Income.date == record_date
        )
    )
    inv = r.scalars().first()
    if inv:
        return f"Er bestaat al een inkomst ({inv}) met hetzelfde bedrag en dezelfde datum."
    r = await db.execute(
        select(Expense.invoice_number).where(
            Expense.amount == amount, Expense.date == record_date
        )
    )
    exp = r.scalars().first()
    if exp:
        return f"Er bestaat al een uitgave ({exp}) met hetzelfde bedrag en dezelfde datum."
    return None


def _parse_iso(date_str) -> Optional[_date]:
    if not date_str:
        return None
    s = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


async def find_duplicate(
    db: AsyncSession,
    *,
    file_hash: Optional[str] = None,
    invoice_number: Optional[str] = None,
    amount: Optional[float] = None,
    date_str: Optional[str] = None,
) -> Optional[str]:
    """Retourneert de sterkste waarschuwing of None. Volgorde = sterkste eerst."""
    warning = await _match_file_hash(db, file_hash)
    if warning:
        return warning
    warning = await _match_invoice_number(db, invoice_number)
    if warning:
        return warning
    return await _match_amount_date(db, amount, _parse_iso(date_str))
