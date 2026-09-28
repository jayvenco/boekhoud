import re
from typing import Optional


def parse_amount(value) -> Optional[float]:
    """Zet een bedrag uit AI/OCR of een invoerveld om naar een float.

    Begrijpt Nederlandse ("1.234,56"), Engelse ("1,234.56") en simpele ("1234.56",
    "1234,56") notaties, met of zonder valutateken/spaties. Geeft None terug als er
    geen geldig bedrag in zit, in plaats van te crashen."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).replace(" ", " ").strip()
    text = re.sub(r"(?i)eur(o)?", "", text).replace("€", "").replace(" ", "")
    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = text.strip("-+()")
    if not re.fullmatch(r"[\d.,]+", text or ""):
        return None

    last_comma, last_dot = text.rfind(","), text.rfind(".")
    if last_comma != -1 and last_dot != -1:
        # Beide aanwezig: het laatste teken is het decimaalteken.
        decimal, thousands = ("," , ".") if last_comma > last_dot else (".", ",")
        text = text.replace(thousands, "").replace(decimal, ".")
    elif last_comma != -1 or last_dot != -1:
        sep = "," if last_comma != -1 else "."
        parts = text.split(sep)
        # "1.234" / "1,234,567": alleen groepen van 3 cijfers na het eerste deel
        # → duizendtalscheiding. Anders (bijv. "12,5" of "12.50") → decimaalteken.
        if len(parts) > 2 or (len(parts[-1]) == 3 and len(parts[0]) <= 3 and parts[0] != "0"):
            text = "".join(parts)
        else:
            text = ".".join(parts)

    try:
        amount = float(text)
    except ValueError:
        return None
    return -amount if negative else amount
