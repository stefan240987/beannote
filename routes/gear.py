"""Gear routes: catalog lookup, photo upload, saved setup."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from db import save_bean_image, search_local_gear, update_user_gear
from deps import current_user
from ocr import encode_scan_jpeg, lookup_gear_catalog
from schemas import GearIn, GearLookupIn
from translations import SUPPORTED_LANGUAGES

router = APIRouter(prefix="/api/gear", tags=["gear"])


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
    local = search_local_gear(payload.query, kind=payload.kind)
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
    return {"gear_candidates": candidates, "specs": candidates[0] if candidates else None}


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
    return {"user": updated}
