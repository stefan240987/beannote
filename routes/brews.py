"""Brew log routes: ratings, tasting journal, export."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from db import export_ratings, get_bean, get_flavor_profile, insert_rating, list_user_journal
from deps import current_user
from schemas import RatingIn

router = APIRouter(tags=["brews"])


@router.post("/api/ratings")
def create_rating(payload: RatingIn, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not get_bean(payload.bean_id):
        raise HTTPException(status_code=404, detail="not_found")
    rating = insert_rating(
        bean_id=payload.bean_id,
        brew_method=payload.brew_method,
        rating=payload.rating,
        acidity=payload.acidity,
        sweetness=payload.sweetness,
        body=payload.body,
        aftertaste=payload.aftertaste,
        notes=payload.tasting_notes_user or payload.notes,
        user_id=user["id"],
        grind_setting=payload.grind_setting,
        coffee_grams=payload.coffee_grams,
        water_grams=payload.water_grams,
        brew_time=payload.brew_time,
        espresso_machine=payload.espresso_machine,
        grinder=payload.grinder,
    )
    return {"rating": rating, "profile": get_flavor_profile(payload.bean_id, user_id=user["id"])}


@router.get("/api/journal")
def tasting_journal(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"entries": list_user_journal(user["id"])}


@router.get("/api/export")
def export_log(fmt: str = "csv", _user: dict[str, Any] = Depends(current_user)) -> Response:
    filename, mime, payload = export_ratings(fmt)
    return Response(
        content=payload,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
