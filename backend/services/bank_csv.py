"""Bankafschrift-CSV importeren: bestandsdetectie, parsing en matching tegen
bestaande inkomsten/uitgaven.

We gokken NOOIT blind naar een kolomvolgorde voor bankformaten die we niet aan
een herkenbare koprij kunnen ophangen — een verkeerde gok in een financiële
app is erger dan geen ondersteuning. ING's CSV-export heeft een vaste,
herkenbare koprij en wordt daarom met hoge zekerheid gedetecteerd. Voor elk
ander bestand met een koprij doen we een beste-poging op kolomnamen
(Rabobank/ABN AMRO exporteren desgewenst óók met koprij); zonder koprij
toont de importpagina de rauwe kolommen zodat de gebruiker ze zelf koppelt.
In alle gevallen ziet de gebruiker een voorbeeld en bevestigt de koppeling
vóórdat er iets wordt geïmporteerd."""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from backend.services.amounts import parse_amount


@dataclass
class ParsedRow:
    date: Optional[date]
    description: str
    counterparty: str
    amount: Optional[float]
    raw: dict = field(default_factory=dict)


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        return ";" if sample.count(";") >= sample.count(",") else ","


def decode_csv_bytes(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


# Bekende, exacte koprij-signaturen per bank — alleen bij een volledige match
# vertrouwen we de kolomvolgorde automatisch.
ING_HEADER = ["Datum", "Naam / Omschrijving", "Rekening", "Tegenrekening", "Code",
              "Af Bij", "Bedrag (EUR)", "Mutatiesoort", "Mededelingen"]

# Kolomnaam-varianten (kleine letters) die op de relevante velden wijzen, voor de
# beste-poging-detectie bij een onbekende maar wél aanwezige koprij.
DATE_HINTS = ["datum", "date", "transactiedatum", "boekdatum"]
AMOUNT_HINTS = ["bedrag", "amount", "bedrag (eur)"]
DESC_HINTS = ["omschrijving", "mededelingen", "description", "naam / omschrijving",
              "omschrijving-1", "notes"]
COUNTERPARTY_HINTS = ["naam tegenpartij", "tegenrekening", "counterparty", "naam"]
AFBIJ_HINTS = ["af bij", "af/bij"]


def parse_date_flexible(value: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    if value.isdigit() and len(value) == 8:  # YYYYMMDD (ING)
        value = f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def detect_layout(header: list, sample_row: list) -> dict:
    """Probeert een kolomkoppeling te bepalen. Geeft altijd een dict terug met
    de sleutels date/amount/description/counterparty/afbij (index of None) en
    een 'bank'-label voor weergave. Nooit een gok bij een lege/afwezige koprij."""
    if not header:
        return {"bank": None, "date": None, "amount": None, "description": None,
                "counterparty": None, "afbij": None}

    normalized = [h.strip() for h in header]
    if normalized == ING_HEADER:
        return {
            "bank": "ING",
            "date": normalized.index("Datum"),
            "amount": normalized.index("Bedrag (EUR)"),
            "description": normalized.index("Mededelingen"),
            "counterparty": normalized.index("Naam / Omschrijving"),
            "afbij": normalized.index("Af Bij"),
        }

    lower = [h.strip().lower() for h in header]

    def find(hints):
        for hint in hints:
            if hint in lower:
                return lower.index(hint)
        return None

    return {
        "bank": None,
        "date": find(DATE_HINTS),
        "amount": find(AMOUNT_HINTS),
        "description": find(DESC_HINTS),
        "counterparty": find(COUNTERPARTY_HINTS),
        "afbij": find(AFBIJ_HINTS),
    }


_IBAN_RE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}")
_CCY_RE = re.compile(r"[A-Z]{3}")


def _looks_like_header(row: list) -> bool:
    """Een koprij bestaat uit tekstlabels — geen enkele cel mag een datum,
    bedrag, IBAN of valutacode zijn. We checken de hele rij, niet alleen de
    eerste kolommen: bankformaten zonder koprij beginnen vaak met een
    IBAN/valutacode, niet met de datum of het bedrag."""
    for cell in row:
        c = cell.strip()
        if not c:
            continue
        if parse_date_flexible(c) is not None:
            return False
        if re.fullmatch(r"[\d.,\-]+", c) and parse_amount(c) is not None:
            return False
        if _IBAN_RE.fullmatch(c) or _CCY_RE.fullmatch(c):
            return False
    return True


def read_preview(content: bytes, max_rows: int = 5) -> dict:
    """Leest de eerste regels van een geüploade CSV en levert header, een
    voorbeeld van de data, en een beste-poging kolomkoppeling — puur om aan
    de gebruiker te tonen; er wordt hier nog niets geïmporteerd."""
    text = decode_csv_bytes(content)
    delimiter = sniff_delimiter(text[:4096])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return {"header": [], "sample": [], "delimiter": delimiter, "has_header": False, "layout": detect_layout([], [])}

    first = rows[0]
    has_header = _looks_like_header(first)
    header = first if has_header else [f"Kolom {i + 1}" for i in range(len(first))]
    data_rows = rows[1:] if has_header else rows
    layout = detect_layout(first if has_header else [], data_rows[0] if data_rows else [])

    return {
        "header": header,
        "sample": data_rows[:max_rows],
        "delimiter": delimiter,
        "has_header": has_header,
        "layout": layout,
        "column_count": len(header),
    }


def compute_row_hash(row_date: date, amount: float, description: str, counterparty: str) -> str:
    key = f"{row_date.isoformat()}|{amount:.2f}|{(description or '').strip().lower()}|{(counterparty or '').strip().lower()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_rows(content: bytes, mapping: dict, delimiter: str, has_header: bool) -> list[ParsedRow]:
    """Parseert het volledige bestand met een door de gebruiker bevestigde
    kolomkoppeling. mapping-waarden zijn kolomindexen (int) of None."""
    text = decode_csv_bytes(content)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if has_header and rows:
        rows = rows[1:]

    def cell(row, idx):
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    out = []
    for row in rows:
        row_date = parse_date_flexible(cell(row, mapping.get("date")))
        raw_amount = parse_amount(cell(row, mapping.get("amount")))
        afbij = cell(row, mapping.get("afbij")).lower()
        amount = raw_amount
        if amount is not None and afbij in ("af", "-"):
            amount = -abs(amount)
        elif amount is not None and afbij == "bij":
            amount = abs(amount)
        out.append(ParsedRow(
            date=row_date,
            description=cell(row, mapping.get("description")),
            counterparty=cell(row, mapping.get("counterparty")),
            amount=amount,
            raw={"line": row},
        ))
    return out
