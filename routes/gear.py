"""Gear routes: catalog lookup, photo upload, saved setup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

import db as db_mod
from db import GEAR_CATALOG, save_bean_image, search_local_gear, update_user_gear
from deps import current_user
from ocr import encode_scan_jpeg, lookup_gear_catalog
from schemas import GearIn, GearLookupIn
from translations import SUPPORTED_LANGUAGES

router = APIRouter(prefix="/api/gear", tags=["gear"])

_STATIC_GEAR_PREFIX = "/static/img/gear/"
_GEAR_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}


def local_gear_image_url(url: str) -> str:
    """Keep curated /static/img/gear/ photos that the generic URL sanitizer would drop."""
    raw = str(url or "").strip()
    if not raw.startswith(_STATIC_GEAR_PREFIX) or ".." in raw:
        return ""
    name = raw[len(_STATIC_GEAR_PREFIX) :]
    if not name or "/" in name or name.startswith("."):
        return ""
    if Path(name).suffix.lower() not in _GEAR_IMAGE_SUFFIXES:
        return ""
    return raw


_orig_sanitize_gear_image_url = db_mod.sanitize_gear_image_url


def _sanitize_gear_image_url(url: str) -> str:
    return local_gear_image_url(url) or _orig_sanitize_gear_image_url(url)


db_mod.sanitize_gear_image_url = _sanitize_gear_image_url


def restore_catalog_images(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog_urls: dict[str, str] = {}
    for raw in GEAR_CATALOG:
        url = local_gear_image_url(str(raw.get("image_url") or ""))
        key = str(raw.get("name") or raw.get("model_name") or "").strip().lower()
        if url and key:
            catalog_urls[key] = url
    restored: list[dict[str, Any]] = []
    for card in cards:
        item = dict(card)
        key = str(item.get("name") or item.get("model_name") or "").strip().lower()
        current = local_gear_image_url(str(item.get("image_url") or ""))
        if current:
            item["image_url"] = current
        elif key in catalog_urls:
            item["image_url"] = catalog_urls[key]
        restored.append(item)
    return restored


@router.post("/lookup")
def gear_lookup(
    payload: GearLookupIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    del user
    chosen = (payload.lang or request.query_params.get("lang") or "da").lower().strip()
    if chosen not in SUPPORTED_LANGUAGES:
        chosen = "da"
    local = restore_catalog_images(search_local_gear(payload.query, kind=payload.kind))
    if local:
        return {"gear_candidates": local, "specs": local[0]}
    try:
        candidates = lookup_gear_catalog(payload.query, kind=payload.kind, lang=chosen)
    except ValueError as exc:
        code = str(exc)
        if code == "gear_query_required":
            raise HTTPException(status_code=400, detail=code) from exc
        raise HTTPException(status_code=422, detail="gear_lookup_fail") from exc
    except RuntimeError as exc:
        if str(exc) == "ocr_missing":
            raise HTTPException(status_code=503, detail="ocr_missing") from exc
        raise HTTPException(status_code=422, detail="gear_lookup_fail") from exc
    except Exception as exc:
        print(f"gear lookup failed: {exc}")
        raise HTTPException(status_code=422, detail="gear_lookup_fail") from exc
    restored = restore_catalog_images(candidates)
    return {"gear_candidates": restored, "specs": restored[0] if restored else None}


@router.post("/photo")
async def gear_photo(
    file: UploadFile = File(...),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    del user
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_image")
    try:
        jpeg = encode_scan_jpeg(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="gear_photo_required") from exc
    return {"image_url": save_bean_image(jpeg, filename="gear.jpg")}


@router.put("")
def save_gear(payload: GearIn, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        updated = update_user_gear(
            user["id"],
            espresso_machine=payload.espresso_machine,
            grinder=payload.grinder,
            brewer_types=payload.brewer_types,
            gear_specs=payload.gear_specs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    specs = updated.get("gear_specs")
    if isinstance(specs, list):
        updated["gear_specs"] = restore_catalog_images(specs)
    return {"user": updated}
