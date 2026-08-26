"""Gear routes: catalog lookup, photo upload, saved setup."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

import db as db_mod
from db import GEAR_CATALOG, normalize_gear_item, save_bean_image, update_user_gear
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

_orig_canonical_gear_kind = db_mod.canonical_gear_kind
_GEAR_SLOTS = {"espresso_machine", "grinder", "brewer", "scale_kettle"}
_KIND_ALIASES = {
    "machine": "espresso_machine",
    "espresso": "espresso_machine",
    "espresso_machine": "espresso_machine",
    "espresso-machine": "espresso_machine",
    "grinder": "grinder",
    "mill": "grinder",
    "brewer": "brewer",
    "brew": "brewer",
    "filter": "brewer",
    "scale_kettle": "scale_kettle",
    "scale": "scale_kettle",
    "kettle": "scale_kettle",
    "scales": "scale_kettle",
    "scale-kettle": "scale_kettle",
}


def _canonical_gear_kind(value: str) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in _KIND_ALIASES:
        return _KIND_ALIASES[raw]
    mapped = _orig_canonical_gear_kind(value)
    return mapped if mapped != "other" or raw not in _GEAR_SLOTS else raw


db_mod.canonical_gear_kind = _canonical_gear_kind


def item_gear_kind(item: dict[str, Any]) -> str:
    return _canonical_gear_kind(
        str(item.get("type") or item.get("kind") or item.get("gear_type") or "")
    )


def restore_catalog_images(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for raw in GEAR_CATALOG:
        gid = str(raw.get("id") or "").strip()
        key = str(raw.get("name") or raw.get("model_name") or "").strip().lower()
        if gid:
            by_id[gid] = raw
        if key:
            by_name[key] = raw
    restored: list[dict[str, Any]] = []
    for card in cards:
        item = dict(card)
        gid = str(item.get("id") or "").strip()
        key = str(item.get("name") or item.get("model_name") or "").strip().lower()
        raw = by_id.get(gid) or by_name.get(key)
        current = local_gear_image_url(str(item.get("image_url") or ""))
        if current:
            item["image_url"] = current
        elif raw:
            item["image_url"] = local_gear_image_url(str(raw.get("image_url") or ""))
        if raw:
            item["id"] = str(raw.get("id") or item.get("id") or "")
            item["kind"] = raw.get("kind") or raw.get("type") or item.get("kind")
            item["type"] = raw.get("type") or item.get("type") or item.get("kind")
        restored.append(item)
    return restored


def filter_gear_by_kind(cards: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Drop catalog/Gemini hits that do not match the selected setup tab."""
    slot = _canonical_gear_kind(kind) if kind else ""
    if slot not in _GEAR_SLOTS:
        return [] if kind else list(cards)
    return [item for item in cards if item_gear_kind(item) == slot]


def search_catalog(query: str, kind: str) -> list[dict[str, Any]]:
    """Score catalog models and keep only the active tab type."""
    q = db_mod._fold_gear_text(query)
    if len(q) < 2:
        return []
    slot = _canonical_gear_kind(kind) if kind else ""
    ranked: list[tuple[float, dict[str, Any]]] = []
    for raw in GEAR_CATALOG:
        if slot and item_gear_kind(raw) != slot:
            continue
        name = db_mod._fold_gear_text(str(raw.get("name") or ""))
        brand = db_mod._fold_gear_text(str(raw.get("brand") or ""))
        aliases = [
            db_mod._fold_gear_text(str(alias))
            for alias in (raw.get("aliases") or ())
            if str(alias).strip()
        ]
        hay = " ".join([brand, name, *aliases])
        score = 0.0
        if q == name or q in aliases:
            score = 4.0
        elif name.startswith(q) or any(alias.startswith(q) for alias in aliases):
            score = 3.4
        elif q in name or any(q in alias for alias in aliases):
            score = 3.0
        elif q == brand or brand.startswith(q) or q in brand:
            score = 2.6
        elif q in hay:
            score = 2.0
        else:
            tokens = [part for part in (name, brand, *aliases, hay) if part]
            if difflib.get_close_matches(q, tokens, n=1, cutoff=0.78):
                score = 1.4
        if score <= 0:
            continue
        ranked.append((score, raw))
    ranked.sort(key=lambda row: (-row[0], str(row[1].get("name") or "")))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _score, raw in ranked:
        card = normalize_gear_item(raw)
        if not card:
            continue
        card["kind"] = item_gear_kind(raw)
        card["type"] = raw.get("type") or card["kind"]
        key = card["id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(card)
        if len(out) >= 8:
            break
    return restore_catalog_images(out)


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
    slot = payload.kind
    local = filter_gear_by_kind(search_catalog(payload.query, kind=slot), slot)
    if local:
        return {"gear_candidates": local, "specs": local[0]}
    try:
        candidates = lookup_gear_catalog(payload.query, kind=slot, lang=chosen)
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
    restored = filter_gear_by_kind(restore_catalog_images(candidates), slot)
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
