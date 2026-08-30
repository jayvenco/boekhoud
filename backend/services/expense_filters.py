from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.models import Expense, ExpenseCategory

# Een uitgave is een "afschrijvingsregel" als het de oorspronkelijke,
# gemarkeerde aankoop is (is_depreciable=True) óf een automatisch
# gegenereerde jaarlijkse afschrijvingsregel (-AFW<jaar> suffix). Beide tellen
# NIET mee als reguliere uitgave of als huisvestingskosten, maar wel als
# aparte "Afschrijvingen"-post — ongeacht in welke categorie de
# oorspronkelijke aankoop viel.
AFSCHRIJVING_ROW = or_(Expense.is_depreciable.is_(True), Expense.invoice_number.like("%-AFW%"))
NOT_AFSCHRIJVING_ROW = and_(Expense.is_depreciable.isnot(True), ~Expense.invoice_number.like("%-AFW%"))

# Voor het TOTAAL "Afschrijvingen" tellen alleen de gegenereerde jaarlijkse
# installments mee (-AFW<jaar>) — NIET de oorspronkelijke aankoopregel zelf,
# anders wordt het volledige aankoopbedrag dubbel meegeteld (eenmaal als
# aankoop, eenmaal als afschrijving). De aankoopregel zelf telt nergens mee,
# hij blijft alleen zichtbaar als losse regel voor de administratie.
AFSCHRIJVING_INSTALLMENT_ROW = and_(Expense.is_depreciable.isnot(True), Expense.invoice_number.like("%-AFW%"))

HUISVESTINGSKOSTEN_SLUG = "huisvestingskosten"


async def huisvestingskosten_id(db: AsyncSession) -> int:
    """Id van de vaste categorie 'Huisvestingskosten', of -1 als die (nog)
    niet bestaat — zodat vergelijkingen ermee altijd veilig blijven."""
    result = await db.execute(select(ExpenseCategory.id).where(ExpenseCategory.slug == HUISVESTINGSKOSTEN_SLUG))
    cat_id = result.scalar_one_or_none()
    return cat_id if cat_id is not None else -1


async def regular_expense_filter(db: AsyncSession):
    """Voorwaarde voor 'reguliere uitgaven': geen afschrijvingsregel en niet
    in de categorie Huisvestingskosten — die twee hebben hun eigen totaal."""
    huisvesting_id = await huisvestingskosten_id(db)
    return and_(NOT_AFSCHRIJVING_ROW, Expense.category_id != huisvesting_id)


async def huisvesting_filter(db: AsyncSession):
    huisvesting_id = await huisvestingskosten_id(db)
    return and_(Expense.category_id == huisvesting_id, NOT_AFSCHRIJVING_ROW)


def is_afschrijving_expense(e) -> bool:
    """Python-level equivalent van AFSCHRIJVING_ROW, voor plekken waar al
    ORM-objecten in een lijst zitten (bijv. exports) i.p.v. een SQL-query.
    Gebruik dit om afschrijvingsregels UIT te sluiten van andere totalen."""
    return bool(e.is_depreciable) or "-AFW" in (e.invoice_number or "")


def is_afschrijving_installment(e) -> bool:
    """Python-level equivalent van AFSCHRIJVING_INSTALLMENT_ROW: alleen de
    gegenereerde jaarlijkse regel, NIET de oorspronkelijke aankoop. Gebruik dit
    om het totaal 'Afschrijvingen' te berekenen (voorkomt dubbeltelling)."""
    return (not e.is_depreciable) and "-AFW" in (e.invoice_number or "")


def is_huisvesting_expense(e) -> bool:
    return e.category is not None and getattr(e.category, "slug", None) == HUISVESTINGSKOSTEN_SLUG
