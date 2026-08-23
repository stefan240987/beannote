"""Local Tesseract OCR + regex parser for coffee bag labels."""

from __future__ import annotations

import os
import re
import shutil
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from db import find_similar_beans

ORIGINS = [
    "Ethiopia", "Etiopien", "Colombia", "Kenya", "Brazil", "Brasilien",
    "Guatemala", "Costa Rica", "Rwanda", "Burundi", "Yemen", "Peru",
    "Honduras", "El Salvador", "Panama", "Indonesia", "India", "Mexico",
    "Tanzania", "Uganda", "Nicaragua", "Bolivia",
]

ORIGIN_CANON = {
    "etiopien": "Ethiopia",
    "ethiopia": "Ethiopia",
    "brasilien": "Brazil",
    "brazil": "Brazil",
}

PROCESS_MAP = {
    "Washed": ["washed", "vasket", "wet process", "fully washed", "wet"],
    "Natural": ["natural", "naturlig", "dry process", "tørret"],
    "Honey": ["honey", "honning", "pulped natural"],
    "Anaerobic": ["anaerobic", "anaerob", "carbonic"],
}

ROAST_MAP = {
    "Light": ["light", "lys", "nordic", "filter"],
    "Medium": ["medium", "mellem"],
    "Medium-Dark": ["medium-dark", "medium dark", "city+"],
    "Dark": ["dark", "mørk", "espresso roast", "french"],
}

KNOWN_ROASTERS = [
    "Prolog Coffee", "Prolog", "La Cabra", "The Coffee Collective",
    "Coffee Collective", "April Coffee", "April", "The Barn",
    "Coffee Mind", "Just Coffee", "Democratic Coffee", "Coffee Collective",
]


def configure_tesseract() -> str | None:
    """Prefer Homebrew paths on Mac local, then PATH / container install."""
    import pytesseract

    candidates = [
        os.getenv("TESSERACT_CMD", ""),
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
        shutil.which("tesseract") or "",
    ]
    for cmd in candidates:
        if cmd and Path(cmd).exists():
            pytesseract.pytesseract.tesseract_cmd = cmd
            return cmd
    return None


def extract_text(image_bytes: bytes) -> str:
    import pytesseract

    configure_tesseract()
    image = Image.open(BytesIO(image_bytes))
    if image.mode not in {"L", "RGB"}:
        image = image.convert("RGB")
    width, height = image.size
    shortest = min(width, height)
    if shortest and shortest < 900:
        scale = 900 / shortest
        image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    try:
        raw = pytesseract.image_to_string(image, lang="eng+dan")
    except Exception:
        raw = pytesseract.image_to_string(image, lang="eng")
    return _cleanup_ocr_text(raw)


def _cleanup_ocr_text(raw: str) -> str:
    text = raw.replace("\x0c", " ")
    text = re.sub(r"[|•·●]+", " ", text)
    text = re.sub(r"[^\w\s\-æøåäöüéèáíóúâê,/&'.]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_label(text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    blob = " ".join(lines)

    origin = _find_origin(blob)
    process = _find_mapped(blob, PROCESS_MAP)
    roast_level = _find_mapped(blob, ROAST_MAP)
    roaster = _find_roaster(lines, blob)
    name = _find_bean_name(lines, roaster, origin)

    return {
        "name": name,
        "roaster": roaster,
        "origin": origin,
        "process": process,
        "roast_level": roast_level,
        "roaster_notes": _guess_notes(blob),
        "raw_text": text,
    }


def scan_label(image_bytes: bytes) -> dict[str, Any]:
    raw = extract_text(image_bytes)
    parsed = parse_label(raw)
    similar = find_similar_beans(parsed["name"], parsed["roaster"]) if parsed["name"] else []
    parsed["similar"] = similar
    return parsed


def _find_origin(blob: str) -> str:
    for origin in ORIGINS:
        if re.search(rf"\b{re.escape(origin)}\b", blob, re.IGNORECASE):
            return ORIGIN_CANON.get(origin.lower(), origin)
    return ""


def _find_mapped(blob: str, mapping: dict[str, list[str]]) -> str:
    lowered = blob.lower()
    for label, aliases in mapping.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in aliases):
            return label
    return ""


def _find_roaster(lines: list[str], blob: str) -> str:
    for known in KNOWN_ROASTERS:
        if re.search(rf"\b{re.escape(known)}\b", blob, re.IGNORECASE):
            return "Prolog Coffee" if known.lower() == "prolog" else (
                "April Coffee" if known.lower() == "april" else (
                    "The Coffee Collective" if known.lower() in {"coffee collective"} else known
                )
            )
    match = re.search(r"(?:roaster|risteri|kafferisteri)\s*[:\-]\s*(.+)", blob, re.IGNORECASE)
    if match:
        return match.group(1).split(",")[0].strip()[:60]
    if lines:
        first = lines[0]
        if 2 <= len(first.split()) <= 5 and len(first) <= 40:
            return first
    return ""


def _find_bean_name(lines: list[str], roaster: str, origin: str) -> str:
    skip = {roaster.lower(), origin.lower(), "coffee", "kaffe", "specialty"}
    for line in lines:
        lowered = line.lower()
        if lowered in skip or lowered.startswith(("www", "http", "net wt", "250", "1 kg")):
            continue
        if re.search(r"\b(ethiopia|colombia|kenya|washed|natural|vasket)\b", lowered):
            if 2 <= len(line.split()) <= 6 and not _find_origin(line) == line:
                return line[:80]
            continue
        if 1 <= len(line.split()) <= 6 and 3 <= len(line) <= 60:
            return line[:80]
    return lines[1][:80] if len(lines) > 1 else (lines[0][:80] if lines else "")


def _guess_notes(blob: str) -> str:
    match = re.search(
        r"(?:notes?|smagsnoter|tasting)\s*[:\-]\s*(.+)",
        blob,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()[:180]
    flavors = re.findall(
        r"\b(jasmine|peach|bergamot|citrus|chocolate|caramel|berry|floral|cocoa|honey|apple)\b",
        blob,
        re.IGNORECASE,
    )
    return ", ".join(dict.fromkeys(f.lower() for f in flavors))
