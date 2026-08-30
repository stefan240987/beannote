"""Scan route: enqueue Gemini Vision OCR so the event loop stays free."""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps

import db
import ocr
from deps import current_user
from jobs import enqueue_job, public_job, store_scan_upload
from translations import SUPPORTED_LANGUAGES

router = APIRouter(tags=["scan"])


def _jpeg_buffer(raw: bytes) -> bytes:
    """Normalize the multipart upload to JPEG for OCR and storage."""
    try:
        return ocr.encode_scan_jpeg(raw)
    except Exception:
        image = Image.open(BytesIO(raw))
        image.load()
        try:
            image = ImageOps.exif_transpose(image) or image
        except Exception:
            pass
        rgb = image.convert("RGB") if image.mode != "RGB" else image
        buffer = BytesIO()
        rgb.save(buffer, format="JPEG", quality=88)
        return buffer.getvalue()


def process_scan_jpeg(jpeg: bytes, lang: str) -> dict[str, Any]:
    """Run OCR and official-page lookup. Called from a job worker, never the event loop."""
    parsed = ocr.scan_label(jpeg, lang=lang)
    if parsed.get("scan_action") == "rate" and (parsed.get("scan_match") or {}).get("id"):
        return parsed
    snapshot_url = db.save_bean_image(jpeg, filename="scan.jpg")
    raw_candidates = [
        str(item).strip()
        for item in (parsed.get("image_candidates") or [])
        if str(item or "").strip()
    ]
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
        try:
            official_bytes = ocr.fetch_official_image_bytes(url)
            return db.save_bean_image(official_bytes, filename="official.jpg") if official_bytes else url
        except Exception:
            return url

    stored_urls = list(unique)
    if unique:
        try:
            with ThreadPoolExecutor(max_workers=min(3, len(unique))) as pool:
                stored_urls = list(pool.map(_store_candidate, unique))
        except Exception as exc:
            print(f"scan image candidates failed: {exc}")
            stored_urls = list(unique)
    resolved: list[str] = []
    seen: set[str] = set()
    for stored in stored_urls:
        if not stored or stored in seen:
            continue
        seen.add(stored)
        resolved.append(stored)
    parsed["image_candidates"] = ocr.pad_image_candidates(resolved, snapshot_url)
    parsed["official_image_url"] = next((url for url in parsed["image_candidates"] if url != snapshot_url), "")
    parsed["product_image_url"] = parsed["official_image_url"]
    parsed["image_url"] = snapshot_url
    parsed["snapshot_url"] = snapshot_url
    parsed["preview"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    return parsed


@router.post("/api/scan")
async def scan(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form(""),
    user: dict[str, Any] = Depends(current_user),
) -> JSONResponse:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_image")
    try:
        ocr.assert_upload_size(raw)
    except ValueError as exc:
        if str(exc) == "upload_too_large":
            raise HTTPException(status_code=413, detail="upload_too_large") from exc
        raise
    chosen = (lang or request.query_params.get("lang") or "da").lower().strip()
    if chosen not in SUPPORTED_LANGUAGES:
        chosen = "da"
    if not ocr.scan_available():
        raise HTTPException(status_code=503, detail="ocr_missing")
    try:
        jpeg = await asyncio.to_thread(_jpeg_buffer, raw)
        image_path = await asyncio.to_thread(store_scan_upload, jpeg)
        job = enqueue_job("scan", int(user["id"]), {"image_path": image_path, "lang": chosen})
    except RuntimeError as exc:
        detail = str(exc)
        if detail == "scan_rate_limited":
            raise HTTPException(status_code=429, detail=detail) from exc
        if detail == "scan_queue_full":
            raise HTTPException(status_code=503, detail=detail) from exc
        raise HTTPException(status_code=422, detail="ocr_fail") from exc
    except Exception as exc:
        print(f"scan enqueue failed: {exc}")
        raise HTTPException(status_code=422, detail="ocr_fail") from exc
    return JSONResponse(status_code=202, content=public_job(job))
