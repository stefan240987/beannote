"""Bean catalog routes: list, detail, create, update, favorite, enrich."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from db import (
    apply_bean_enrichment,
    get_bean,
    get_flavor_profile,
    insert_bean,
    list_beans,
    toggle_favorite,
    update_bean,
)
from deps import _auth_error, current_user, require_admin
from jobs import enqueue_job, public_job
from ocr import compare_flavor_notes, enrich_bean_from_web
from schemas import BeanIn
from translations import SUPPORTED_LANGUAGES

router = APIRouter(prefix="/api/beans", tags=["beans"])


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
