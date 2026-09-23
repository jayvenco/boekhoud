import os
import json
import logging
import re
from pathlib import Path
import pytesseract
from PIL import Image
import pdfplumber
import httpx

from backend.services.ai_providers import get_provider
from backend.services.crypto import decrypt
from backend.services.amounts import parse_amount

logger = logging.getLogger("boekhoud.ocr")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "")


def extract_text_from_image(path: str) -> str:
    try:
        img = Image.open(path)
        text = pytesseract.image_to_string(img, lang="nld+eng")
        return text.strip()
    except Exception:
        return ""


def extract_text_from_pdf(path: str) -> str:
    try:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        return text.strip()
    except Exception:
        return ""


def extract_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(path)
    elif ext in [".jpg", ".jpeg", ".png"]:
        return extract_text_from_image(path)
    return ""


EXTRACTION_PROMPT = """Je bent een assistent die {doc_kind} analyseert voor een Nederlandse boekhouding.
Analyseer de volgende tekst en extraheer de gegevens.

Regels:
- amount: het TOTAALBEDRAG INCLUSIEF btw dat betaald is of betaald moet worden ("totaal", "te betalen", "totaalbedrag"). Niet een subtotaal, btw-bedrag of bedrag exclusief btw, tenzij dat het enige bedrag is. Geef een getal met punt als decimaalteken, zonder valutasymbool of duizendtalscheiding (bijv. 1234.56).
- date: de FACTUURDATUM of bondatum, niet de vervaldatum of betaaltermijn. Formaat DD-MM-YYYY.
- invoice_number: het factuur-/bonnummer zoals de {counterparty} dat heeft afgegeven (niet het klantnummer of IBAN).
- description: kort (max. 8 woorden): wat is er gekocht/geleverd en door wie.
- category_suggestion: kies de best passende uit deze lijst, of null als niets past: {category_list}
- Verzin niets: als een veld niet duidelijk in de tekst staat, gebruik je null.

Tekst:
{text}

Retourneer ALLEEN een geldig JSON object, geen uitleg, geen markdown, geen backticks:
{{
  "invoice_number": "string of null",
  "date": "DD-MM-YYYY of null",
  "amount": getal of null,
  "description": "string of null",
  "category_suggestion": "één van de opgegeven categorieën of null"
}}"""

DEFAULT_EXPENSE_CATEGORIES = [
    "praktijkinrichting", "vaste_lasten", "abonnementen", "materiaal", "materieel",
    "marketing", "reiskosten", "apparatuur", "huisvestingskosten", "overige",
]
DEFAULT_INCOME_CATEGORIES = ["behandelingen", "debiteuren"]

MAX_PROMPT_CHARS = 3500


def _trim_text(text: str, limit: int = MAX_PROMPT_CHARS) -> str:
    """Bij lange documenten staan totaalbedragen meestal onderaan. Bewaar daarom
    het begin (afzender, nummer, datum) én het einde (totalen) in plaats van
    alleen de eerste N tekens."""
    if len(text) <= limit:
        return text
    head = int(limit * 0.55)
    tail = limit - head
    return f"{text[:head]}\n[...]\n{text[-tail:]}"


def build_prompt(text: str, transaction_type: str = "uitgave", category_slugs=None) -> str:
    is_income = transaction_type == "inkomst"
    slugs = list(category_slugs) if category_slugs else (
        DEFAULT_INCOME_CATEGORIES if is_income else DEFAULT_EXPENSE_CATEGORIES
    )
    return EXTRACTION_PROMPT.format(
        doc_kind="betaalbewijzen en verkoopfacturen" if is_income else "bonnen en inkoopfacturen",
        counterparty="klant" if is_income else "leverancier",
        category_list=", ".join(slugs),
        text=_trim_text(text),
    )


def clean_json(text: str) -> str:
    """Strip markdown code blocks and whitespace from AI response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


async def analyze_with_openai(prompt: str) -> dict:
    if not OPENAI_API_KEY:
        return {}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 500,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                }
            )
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(clean_json(content))
    except Exception as e:
        return {"_ai_error": str(e)}


async def analyze_with_ollama(prompt: str) -> dict:
    if not OLLAMA_BASE_URL:
        return {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": "llama3.2",
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "stream": False,
                }
            )
            data = resp.json()
            content = data["message"]["content"]
            return json.loads(clean_json(content))
    except Exception as e:
        return {"_ai_error": str(e)}


async def analyze_with_configured_provider(db, prompt: str) -> dict:
    """Gebruikt de in Instellingen gekozen AI-provider/model (database-config)."""
    from sqlalchemy import select
    from backend.models.models import AISettings

    result = await db.execute(select(AISettings))
    settings = result.scalar_one_or_none()
    if not settings or not settings.provider:
        return {}

    provider = get_provider(settings.provider)
    if not provider:
        return {}

    encrypted_key = (
        settings.openai_api_key_encrypted if settings.provider == "openai"
        else settings.anthropic_api_key_encrypted
    )
    api_key = decrypt(encrypted_key) if encrypted_key else ""
    if not api_key:
        return {}

    model = settings.model or provider.default_model()
    try:
        content = await provider.complete(api_key, model, prompt)
        return json.loads(clean_json(content))
    except Exception as e:
        detail = str(e)[:200]
        logger.error(f"AI-aanvraag mislukt voor provider {settings.provider}: {type(e).__name__}: {detail}")
        return {"_ai_error": f"AI-aanvraag mislukt ({type(e).__name__}): {detail}"}


async def process_receipt(file_path: str, db=None, transaction_type: str = "uitgave",
                          category_slugs=None) -> dict:
    """Extract text and analyze with AI. Returns structured data."""
    text = extract_text(file_path)
    if not text:
        return {"error": "Kon geen tekst extraheren uit het bestand."}

    prompt = build_prompt(text, transaction_type, category_slugs)
    result = {}
    if db is not None:
        result = await analyze_with_configured_provider(db, prompt)
    if not result and OPENAI_API_KEY:
        result = await analyze_with_openai(prompt)
    elif not result and OLLAMA_BASE_URL:
        result = await analyze_with_ollama(prompt)

    # Bedrag naar float (begrijpt ook "1.234,56"); ongeldig → None
    if "amount" in result:
        result["amount"] = parse_amount(result["amount"])

    # Alleen een categorie uit de toegestane lijst accepteren — een verzonnen of
    # verkeerd-type categorie levert anders een stille misser in de selectie op.
    allowed = {c.lower() for c in category_slugs} if category_slugs else None
    suggestion = result.get("category_suggestion")
    if suggestion:
        suggestion = str(suggestion).strip().lower()
        result["category_suggestion"] = suggestion if (allowed is None or suggestion in allowed) else None

    result["_raw_text"] = text[:500]
    return result
