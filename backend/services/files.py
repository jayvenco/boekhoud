from typing import Optional
import os
import shutil
from pathlib import Path
from fastapi import UploadFile

UPLOAD_ROOT = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def get_upload_path(type_: str, category_slug: str) -> Path:
    path = UPLOAD_ROOT / type_ / category_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_valid_upload(file: UploadFile) -> bool:
    """Check if an UploadFile actually has content."""
    if file is None:
        return False
    if not file.filename:
        return False
    if file.filename.strip() == "":
        return False
    return True


async def save_receipts(
    files: list,
    type_: str,
    category_slug: str,
    invoice_number: str,
) -> list:
    """Save one or more receipt files.
    Single file: invoice_number.ext
    Multiple files: invoice_number-a.ext, invoice_number-b.ext, etc.
    Returns list of dicts: [{"path": "...", "suffix": "a" or None}]
    """
    suffixes = "abcdefghijklmnopqrstuvwxyz"
    valid_files = [f for f in files if is_valid_upload(f)]
    results = []

    for i, file in enumerate(valid_files):
        try:
            ext = Path(file.filename).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue

            safe_inv = "".join(c for c in invoice_number if c.isalnum() or c in "-_.")
            # Only add suffix if multiple files
            suffix = suffixes[i] if len(valid_files) > 1 else None
            filename = f"{safe_inv}{'-' + suffix if suffix else ''}{ext}"

            dest_dir = get_upload_path(type_, category_slug)
            dest_path = dest_dir / filename

            file_bytes = await file.read()
            if not file_bytes:
                continue
            if len(file_bytes) > MAX_FILE_SIZE:
                continue

            with open(dest_path, "wb") as f:
                f.write(file_bytes)

            results.append({
                "path": str(dest_path.relative_to(UPLOAD_ROOT)),
                "suffix": suffix,
            })
        except Exception:
            continue

    return results


def delete_file(relative_path: str):
    try:
        full = UPLOAD_ROOT / relative_path
        if full.exists():
            full.unlink()
    except Exception:
        pass


async def move_tmp_to_category(
    tmp_filename: str,
    type_: str,
    category_slug: str,
    invoice_number: str
) -> Optional[str]:
    """Move an OCR tmp file to the correct category folder."""
    tmp_path = UPLOAD_ROOT / "tmp" / tmp_filename
    if not tmp_path.exists():
        return None

    ext = tmp_path.suffix.lower()
    safe_invoice = "".join(c for c in invoice_number if c.isalnum() or c in "-_.")
    filename = f"{safe_invoice}{ext}"
    dest_dir = get_upload_path(type_, category_slug)
    dest_path = dest_dir / filename

    shutil.move(str(tmp_path), str(dest_path))
    return str(dest_path.relative_to(UPLOAD_ROOT))
