import os
import json
import re
from pathlib import Path
import pytesseract
from PIL import Image
import pdfplumber
import httpx

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


EXTRACTION_PROMPT = """Je bent een assistent die bonnen en facturen analyseert.
Analyseer de volgende tekst van een bon/factuur en extraheer de gegevens.

Tekst:
{text}

Retourneer ALLEEN een geldig JSON object, geen uitleg, geen markdown, geen backticks:
{{
  "invoice_number": "factuurnummer als string of null",
  "date": "datum in formaat DD.MM.YYYY of null",
  "amount": getal zonder valutasymbool of null,
  "description": "korte omschrijving of null",
  "category_suggestion": "één van: behandelingen, praktijkinrichting, vaste_lasten, abonnementen, materiaal, materieel, marketing, reiskosten, of null"
}}"""


def clean_json(text: str) -> str:
    """Strip markdown code blocks and whitespace from AI response."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


async def analyze_with_openai(text: str) -> dict:
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
                        {"role": "user", "content": EXTRACTION_PROMPT.format(text=text[:3000])}
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


async def analyze_with_ollama(text: str) -> dict:
    if not OLLAMA_BASE_URL:
        return {}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": "llama3.2",
                    "messages": [
                        {"role": "user", "content": EXTRACTION_PROMPT.format(text=text[:3000])}
                    ],
                    "stream": False,
                }
            )
            data = resp.json()
            content = data["message"]["content"]
            return json.loads(clean_json(content))
    except Exception as e:
        return {"_ai_error": str(e)}


async def process_receipt(file_path: str) -> dict:
    """Extract text and analyze with AI. Returns structured data."""
    text = extract_text(file_path)
    if not text:
        return {"error": "Kon geen tekst extraheren uit het bestand."}

    result = {}
    if OPENAI_API_KEY:
        result = await analyze_with_openai(text)
    elif OLLAMA_BASE_URL:
        result = await analyze_with_ollama(text)

    # Ensure amount is a float or null
    if "amount" in result and result["amount"] is not None:
        try:
            result["amount"] = float(str(result["amount"]).replace(",", ".").replace("€", "").strip())
        except Exception:
            result["amount"] = None

    result["_raw_text"] = text[:500]
    return result
