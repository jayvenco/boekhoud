import os
import shutil
from pathlib import Path
from fastapi import UploadFile

UPLOAD_ROOT = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def get_upload_path(type_: str, category_slug: str) -> Path:
    """type_: 'inkomsten' or 'uitgaven'"""
    path = UPLOAD_ROOT / type_ / category_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


async def save_receipt(
    file: UploadFile,
    type_: str,
    category_slug: str,
    invoice_number: str
) -> str:
    """Save uploaded file and return relative path."""
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Bestandstype {ext} niet toegestaan. Gebruik PDF, JPG of PNG.")

    safe_invoice = "".join(c for c in invoice_number if c.isalnum() or c in "-_.")
    filename = f"{safe_invoice}{ext}"
    dest_dir = get_upload_path(type_, category_slug)
    dest_path = dest_dir / filename

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise ValueError("Bestand is te groot (max 10MB).")

    with open(dest_path, "wb") as f:
        f.write(content)

    # Return relative path for storage
    return str(dest_path.relative_to(UPLOAD_ROOT.parent.parent if UPLOAD_ROOT.name == "uploads" else UPLOAD_ROOT.parent))


def delete_file(path: str):
    try:
        full = Path("/app") / path if not path.startswith("/") else Path(path)
        if full.exists():
            full.unlink()
    except Exception:
        pass
