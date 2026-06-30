import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.models import Expense, Income, InvoiceNumberingSettings


async def get_numbering_settings(db: AsyncSession) -> InvoiceNumberingSettings:
    result = await db.execute(select(InvoiceNumberingSettings))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = InvoiceNumberingSettings()
        db.add(settings)
        await db.commit()
    return settings


def get_template_for_type(settings: InvoiceNumberingSettings, doc_type: str) -> str:
    return settings.format_template_expense if doc_type == "uitgaven" else settings.format_template_income


def format_invoice_number(template: str, year: int, number: int, padding: int) -> str:
    padded = str(number).zfill(max(1, padding))
    try:
        return template.format(year=year, number=padded)
    except (KeyError, IndexError):
        return f"{year}-{padded}"


def _template_to_regex(template: str) -> re.Pattern:
    """Vertaalt een nummerformaat (bijv. 'I-{year}-{number}') naar een regex die
    bestaande factuurnummers herkent, ongeacht waar de prefix/plaatshouders staan."""
    escaped = re.escape(template)
    escaped = escaped.replace(re.escape("{year}"), r"(?P<year>\d{4})")
    escaped = escaped.replace(re.escape("{number}"), r"(?P<number>\d+)")
    return re.compile(f"^{escaped}$")


async def get_next_invoice_number(
    db: AsyncSession, year: int, doc_type: str, settings: InvoiceNumberingSettings = None
) -> str:
    """Bepaalt het volgende vrije nummer binnen het opgegeven boekjaar, op basis van
    het per documenttype ingestelde nummerformaat (doc_type: 'inkomsten' of 'uitgaven').
    Afschrijvings- en abonnement-vervolgregels (achtervoegsels zoals -AFW2027 / -REC1)
    voldoen nooit aan het volledige patroon en worden dus automatisch overgeslagen."""
    settings = settings or await get_numbering_settings(db)
    template = get_template_for_type(settings, doc_type)
    pattern = _template_to_regex(template)

    if doc_type == "uitgaven":
        result = await db.execute(select(Expense.invoice_number))
    else:
        result = await db.execute(select(Income.invoice_number))

    used_numbers = set()
    for inv in result.scalars().all():
        m = pattern.match(inv)
        if m and int(m.group("year")) == year:
            used_numbers.add(int(m.group("number")))

    next_num = 1
    while next_num in used_numbers:
        next_num += 1

    return format_invoice_number(template, year, next_num, settings.padding)
