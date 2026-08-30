"""Bean catalog routes: list, detail, create, update, favorite, enrich."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from db import (
    apply_bean_enrichment,
    get_bean,
    get_catalog_dir,
    get_flavor_profile,
    get_images_dir,
    insert_bean,
    list_beans,
    list_pending_image_beans,
    set_bean_professional_image,
    resolve_catalog_image,
    resolve_image_path,
    toggle_favorite,
    update_bean,
    update_bean_image,
)
from deps import _auth_error, current_user, require_admin
from image_search import fetch_official_image_bytes
from jobs import enqueue_job, public_job
from ocr import assert_upload_size, compare_flavor_notes, encode_scan_jpeg, enrich_bean_from_web
from routes.users import annotate_community_recipes
from schemas import BeanImageIn, BeanIn
from translations import SUPPORTED_LANGUAGES

router = APIRouter(prefix="/api/beans", tags=["beans"])
admin_router = APIRouter(prefix="/api/admin/beans", tags=["admin-beans"])

_STATIC_BEAN_PREFIX = "/static/img/beans/"


@router.get("")
def beans(
    search: str = "",
    origin: str = "",
    roast_level: str = "",
    min_rating: float = 0.0,
    favorites: bool = False,
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    return list_beans(
        search=search,
        origin=origin,
        roast_level=roast_level,
        min_rating=min_rating,
        user_id=user["id"],
        favorites_only=favorites,
    )


@router.get("/{bean_id}")
def bean_detail(bean_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    profile = get_flavor_profile(bean_id, user_id=user["id"])
    if not profile:
        raise HTTPException(status_code=404, detail="not_found")
    notes = compare_flavor_notes(
        (profile["bean"] or {}).get("roaster_notes") or "",
        ((profile.get("user") or {}) or {}).get("notes") or "",
        (profile["bean"] or {}).get("flavor_tags") or {},
    )
    profile["notes"] = notes
    matched = annotate_community_recipes(profile.get("community_history") or [], user)
    profile["community_history"] = matched["recipes"]
    profile["community_gear_fallback"] = matched["fallback"]
    return profile


@router.post("")
def create_bean(payload: BeanIn, _user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        result = insert_bean(
            name=payload.name,
            roaster=payload.roaster,
            origin=payload.origin,
            process=payload.process,
            roast_level=payload.roast_level,
            roaster_notes=payload.roaster_notes,
            flavor_tags=payload.flavor_tags,
            suitable_for=payload.suitable_for,
            skip_fuzzy=payload.skip_fuzzy,
            image_url=payload.image_url,
            roaster_url=payload.roaster_url,
            story=payload.story,
            recommended_method=payload.recommended_method,
            grind_size=payload.grind_size,
            water_temp=payload.water_temp,
            brew_ratio=payload.brew_ratio,
            brew_recommendation=payload.brew_recommendation,
            roast_date=payload.roast_date,
            altitude=payload.altitude,
            varietal=payload.varietal,
            latitude=payload.latitude,
            longitude=payload.longitude,
            region_full=payload.region_full,
            acidity_score=payload.acidity_score,
            body_score=payload.body_score,
            roast_level_score=payload.roast_level_score,
            roaster_acidity=payload.roaster_acidity,
            roaster_body=payload.roaster_body,
            roaster_roast_level=payload.roaster_roast_level,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.put("/{bean_id}")
def replace_bean(
    bean_id: int,
    payload: BeanIn,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    if not get_bean(bean_id):
        raise HTTPException(status_code=404, detail="not_found")
    try:
        bean = update_bean(
            bean_id,
            name=payload.name,
            roaster=payload.roaster,
            origin=payload.origin,
            process=payload.process,
            roast_level=payload.roast_level,
            roaster_notes=payload.roaster_notes,
            flavor_tags=payload.flavor_tags,
            suitable_for=payload.suitable_for,
            story=payload.story,
            image_url=payload.image_url,
            roaster_url=payload.roaster_url,
            recommended_method=payload.recommended_method,
            grind_size=payload.grind_size,
            water_temp=payload.water_temp,
            brew_ratio=payload.brew_ratio,
            brew_recommendation=payload.brew_recommendation,
            roast_date=payload.roast_date,
            altitude=payload.altitude,
            varietal=payload.varietal,
            latitude=payload.latitude,
            longitude=payload.longitude,
            region_full=payload.region_full,
            acidity_score=payload.acidity_score,
            body_score=payload.body_score,
            roast_level_score=payload.roast_level_score,
            roaster_acidity=payload.roaster_acidity,
            roaster_body=payload.roaster_body,
            roaster_roast_level=payload.roaster_roast_level,
        )
    except ValueError as exc:
        raise _auth_error(str(exc)) from exc
    if not bean:
        raise HTTPException(status_code=404, detail="not_found")
    return {"status": "updated", "bean": bean}


@router.post("/{bean_id}/favorite")
def favorite_bean(bean_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not get_bean(bean_id):
        raise HTTPException(status_code=404, detail="not_found")
    return {"is_favorite": toggle_favorite(user["id"], bean_id), "bean_id": bean_id}


def process_enrich_job(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Grounded web lookup for an archive bean. Runs in a job worker."""
    bean_id = int(payload.get("bean_id") or 0)
    lang = str(payload.get("lang") or "da")
    bean = get_bean(bean_id, user_id=user_id)
    if not bean:
        raise RuntimeError("not_found")
    result = enrich_bean_from_web(bean.get("name") or "", bean.get("roaster") or "", lang=lang)
    updated = apply_bean_enrichment(bean_id, result, lang=lang)
    if not updated:
        raise RuntimeError("not_found")
    profile = get_flavor_profile(bean_id, user_id=user_id)
    return {"status": "enriched", "bean": updated, "profile": profile}


@router.post("/{bean_id}/enrich")
def enrich_bean(
    bean_id: int,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
) -> JSONResponse:
    bean = get_bean(bean_id, user_id=user["id"])
    if not bean:
        raise HTTPException(status_code=404, detail="not_found")
    chosen = (request.query_params.get("lang") or "da").lower().strip()
    if chosen not in SUPPORTED_LANGUAGES:
        chosen = "da"
    try:
        job = enqueue_job(
            "enrich",
            int(user["id"]),
            {"bean_id": bean_id, "lang": chosen},
        )
    except RuntimeError as exc:
        detail = str(exc)
        if detail == "enrich_rate_limited":
            raise HTTPException(status_code=429, detail=detail) from exc
        if detail == "scan_queue_full":
            raise HTTPException(status_code=503, detail=detail) from exc
        if detail == "ocr_missing":
            raise HTTPException(status_code=503, detail=detail) from exc
        raise HTTPException(status_code=422, detail="enrich_fail") from exc
    return JSONResponse(status_code=202, content=public_job(job))


def save_professional_bean_image(image_bytes: bytes, bean_id: int) -> str:
    dest_dir = get_catalog_dir("beans")
    dest = dest_dir / f"{int(bean_id)}.jpg"
    dest.write_bytes(image_bytes)
    return f"{_STATIC_BEAN_PREFIX}{int(bean_id)}.jpg"


def bust_bean_image_url(url: str) -> str:
    raw = str(url or "").strip()
    path = raw.split("?", 1)[0]
    if not path.startswith(_STATIC_BEAN_PREFIX):
        return raw
    dest = resolve_catalog_image("beans", path[len(_STATIC_BEAN_PREFIX) :])
    try:
        return f"{path}?v={int(dest.stat().st_mtime)}" if dest else path
    except OSError:
        return path


def _read_local_image(url: str) -> bytes:
    raw = str(url or "").strip().split("?", 1)[0]
    if raw.startswith(_STATIC_BEAN_PREFIX) and ".." not in raw:
        dest = resolve_catalog_image("beans", Path(raw[len(_STATIC_BEAN_PREFIX) :]).name)
        return dest.read_bytes() if dest and dest.is_file() else b""
    if raw.startswith("/media/"):
        raw = f"images/{Path(raw).name}"
    resolved = resolve_image_path(raw)
    if resolved and resolved.is_file():
        return resolved.read_bytes()
    candidate = get_images_dir() / Path(raw).name
    return candidate.read_bytes() if candidate.is_file() else b""


async def _read_image_payload(request: Request) -> tuple[bytes, str]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_json") from exc
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="invalid_json")
        payload = BeanImageIn.model_validate(raw)
        return b"", (payload.image_url or "").strip()
    form = await request.form()
    upload = form.get("file") or form.get("image") or form.get("photo")
    image_url = str(form.get("image_url") or "").strip()
    raw_bytes = b""
    if isinstance(upload, StarletteUploadFile):
        raw_bytes = await upload.read()
    return raw_bytes, image_url


def _mark_professional(bean: dict[str, Any] | None, image_url: str) -> dict[str, Any]:
    data = dict(bean or {})
    data["image_url"] = bust_bean_image_url(image_url)
    data["is_professional_image"] = True
    data["image_source"] = "professional"
    return data


@router.post("/{bean_id}/image")
async def replace_bean_image(
    bean_id: int,
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del _admin
    if not get_bean(bean_id):
        raise HTTPException(status_code=404, detail="not_found")
    raw_bytes, image_url = await _read_image_payload(request)
    jpeg = b""
    source = raw_bytes
    if not source and image_url:
        source = _read_local_image(image_url)
        if not source:
            fetched = await asyncio.to_thread(fetch_official_image_bytes, image_url)
            source = fetched or b""
    if not source:
        raise HTTPException(status_code=400, detail="image_replace_fail")
    try:
        assert_upload_size(source)
        jpeg = await asyncio.to_thread(encode_scan_jpeg, source)
    except ValueError as exc:
        if str(exc) == "upload_too_large":
            raise HTTPException(status_code=413, detail="upload_too_large") from exc
        raise HTTPException(status_code=422, detail="image_replace_fail") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="image_replace_fail") from exc
    stored = await asyncio.to_thread(save_professional_bean_image, jpeg, bean_id)
    update_bean_image(bean_id, stored, professional=True)
    bean = _mark_professional(get_bean(bean_id), stored)
    return {"status": "updated", "bean": bean, "image_url": bean["image_url"], "is_professional_image": True}


@router.post("/{bean_id}/image/approve")
def approve_bean_image(
    bean_id: int,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del _admin
    bean = set_bean_professional_image(bean_id, True)
    if not bean:
        raise HTTPException(status_code=404, detail="not_found")
    marked = _mark_professional(bean, bean.get("image_url") or "")
    return {"status": "approved", "bean": marked, "image_url": marked.get("image_url") or "", "is_professional_image": True}


@admin_router.get("/pending-images")
def pending_bean_images(_admin: dict[str, Any] = Depends(require_admin)) -> list[dict[str, Any]]:
    del _admin
    return list_pending_image_beans()


_combined = APIRouter()
_combined.include_router(router)
_combined.include_router(admin_router)
router = _combined
