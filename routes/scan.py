"""Scan route: Gemini Vision OCR plus official packshot candidates."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from db import save_bean_image
from deps import current_user
from ocr import (
    encode_scan_jpeg,
    fetch_official_image_bytes,
    pad_image_candidates,
    scan_available,
    scan_label,
)
from translations import SUPPORTED_LANGUAGES

router = APIRouter(tags=["scan"])


@router.post("/api/scan")
async def scan(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form(""),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    del user
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_image")
    if not scan_available():
        raise HTTPException(status_code=503, detail="ocr_missing")
    chosen = (lang or request.query_params.get("lang") or "da").lower().strip()
    if chosen not in SUPPORTED_LANGUAGES:
        chosen = "da"
    try:
        jpeg = encode_scan_jpeg(raw)
        parsed = scan_label(jpeg, lang=chosen)
    except HTTPException:
        raise
    except Exception as exc:
        print(f"scan failed: {exc}")
        raise HTTPException(status_code=422, detail="ocr_fail") from exc
    snapshot_url = save_bean_image(jpeg, filename="scan.jpg")
    raw_candidates = [str(item).strip() for item in (parsed.get("image_candidates") or []) if str(item or "").strip()]
    unique: list[str] = []
    seen_raw: set[str] = set()
    for raw_url in raw_candidates:
        url = str(raw_url or "").strip()
        if not url or url in seen_raw:
            continue
        seen_raw.add(url)
        unique.append(url)
        if len(unique) >= 3:
            break

    def _store_candidate(url: str) -> str:
        official_bytes = fetch_official_image_bytes(url)
        return save_bean_image(official_bytes, filename="official.jpg") if official_bytes else url

    stored_urls = list(unique)
    if unique:
        with ThreadPoolExecutor(max_workers=min(3, len(unique))) as pool:
            stored_urls = list(pool.map(_store_candidate, unique))
    resolved: list[str] = []
    seen: set[str] = set()
    for stored in stored_urls:
        if not stored or stored in seen:
            continue
        seen.add(stored)
        resolved.append(stored)
    parsed["image_candidates"] = pad_image_candidates(resolved, snapshot_url)
    parsed["official_image_url"] = next((url for url in parsed["image_candidates"] if url != snapshot_url), "")
    parsed["product_image_url"] = parsed["official_image_url"]
    parsed["image_url"] = snapshot_url
    parsed["snapshot_url"] = snapshot_url
    parsed["preview"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    return parsed
