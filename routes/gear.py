"""Gear routes: catalog lookup, photo upload, saved setup."""

from __future__ import annotations

import asyncio
import difflib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

import db as db_mod
from db import GEAR_CATALOG, connect, normalize_gear_item, save_bean_image, update_user_gear
from deps import current_user, require_admin
from ocr import encode_scan_jpeg, lookup_gear_catalog
from schemas import GearCreateIn, GearIn, GearLookupIn
from translations import SUPPORTED_LANGUAGES

router = APIRouter(prefix="/api/gear", tags=["gear"])

_STATIC_GEAR_PREFIX = "/static/img/gear/"
_GEAR_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
_GEAR_IMG_DIR = Path(__file__).resolve().parent.parent / "static" / "img" / "gear"


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


def _ensure_gear_table(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gear (
            id TEXT PRIMARY KEY,
            brand TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            kind TEXT NOT NULL,
            highlights TEXT NOT NULL DEFAULT '[]',
            specs TEXT NOT NULL DEFAULT '{}',
            image_url TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_gear_brand_name ON gear (brand, name)"
    )


def _row_to_catalog(row: Any) -> dict[str, Any]:
    data = dict(row)
    highlights = data.get("highlights")
    specs = data.get("specs")
    if isinstance(highlights, str):
        try:
            highlights = json.loads(highlights)
        except json.JSONDecodeError:
            highlights = []
    if isinstance(specs, str):
        try:
            specs = json.loads(specs)
        except json.JSONDecodeError:
            specs = {}
    return {
        "id": str(data.get("id") or ""),
        "brand": str(data.get("brand") or ""),
        "name": str(data.get("name") or ""),
        "type": str(data.get("type") or data.get("kind") or ""),
        "kind": str(data.get("kind") or data.get("type") or ""),
        "aliases": [],
        "highlights": highlights if isinstance(highlights, list) else [],
        "specs": specs if isinstance(specs, dict) else {},
        "image_url": str(data.get("image_url") or ""),
    }


def _load_db_gear() -> list[dict[str, Any]]:
    try:
        with connect() as conn:
            _ensure_gear_table(conn)
            rows = conn.execute(
                "SELECT id, brand, name, type, kind, highlights, specs, image_url FROM gear"
            ).fetchall()
    except Exception as exc:
        print(f"gear catalog load failed: {exc}")
        return []
    return [_row_to_catalog(row) for row in rows]


def _all_catalog() -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in (*_load_db_gear(), *GEAR_CATALOG):
        gid = str(raw.get("id") or "").strip()
        key = gid or str(raw.get("name") or "").strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(raw)
    return out


def _safe_gear_slug(brand: str, name: str) -> str:
    base = db_mod._gear_slug(f"{brand} {name}".strip() or name or "gear") or "gear"
    return re.sub(r"[^a-z0-9-]+", "", base).strip("-") or "gear"


def _unique_gear_slug(
    brand: str,
    name: str,
    existing_id: str = "",
    taken: set[str] | None = None,
) -> str:
    slug = _safe_gear_slug(brand, name)
    if existing_id:
        return existing_id
    used = set(taken or ())
    candidate = slug
    n = 2
    while candidate in used or (_GEAR_IMG_DIR / f"{candidate}.jpg").exists():
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def _find_catalog_item(gear_id: str) -> dict[str, Any] | None:
    gid = str(gear_id or "").strip()
    if not gid:
        return None
    folded = db_mod._fold_gear_text(gid)
    for raw in _all_catalog():
        if str(raw.get("id") or "").strip() == gid:
            return raw
    if not folded:
        return None
    for raw in _all_catalog():
        if db_mod._fold_gear_text(str(raw.get("name") or "")) == folded:
            return raw
    return None


def _catalog_photo_slug(item: dict[str, Any]) -> str:
    existing = local_gear_image_url(str(item.get("image_url") or ""))
    if existing:
        return Path(existing).stem
    gid = re.sub(r"[^a-z0-9-]+", "", str(item.get("id") or "").lower()).strip("-")
    if gid:
        return gid
    return _safe_gear_slug(str(item.get("brand") or ""), str(item.get("name") or item.get("model_name") or ""))


def _upsert_gear_row(item: dict[str, Any], image_url: str) -> dict[str, Any]:
    gid = str(item.get("id") or "").strip() or _catalog_photo_slug(item)
    name = str(item.get("name") or item.get("model_name") or "").strip()
    brand = str(item.get("brand") or "").strip()
    slot = item_gear_kind(item) or "other"
    highlights = item.get("highlights") if isinstance(item.get("highlights"), list) else []
    specs = item.get("specs") if isinstance(item.get("specs"), dict) else {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as conn:
        _ensure_gear_table(conn)
        twin = conn.execute(
            "SELECT id FROM gear WHERE id = ? OR (lower(name) = lower(?) AND lower(brand) = lower(?))",
            (gid, name, brand),
        ).fetchone()
        if twin:
            gid = str(twin["id"])
        conn.execute(
            """
            INSERT INTO gear (id, brand, name, type, kind, highlights, specs, image_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                brand = excluded.brand,
                name = excluded.name,
                type = excluded.type,
                kind = excluded.kind,
                highlights = excluded.highlights,
                specs = excluded.specs,
                image_url = excluded.image_url
            """,
            (
                gid,
                brand,
                name,
                slot,
                slot,
                json.dumps(highlights, ensure_ascii=False),
                json.dumps(specs, ensure_ascii=False),
                image_url,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM gear WHERE id = ?", (gid,)).fetchone()
    card = normalize_gear_item(_row_to_catalog(row))
    if not card:
        raise HTTPException(status_code=422, detail="gear_admin_fail")
    card["kind"] = slot
    card["type"] = slot
    card["image_url"] = local_gear_image_url(str(card.get("image_url") or image_url)) or image_url
    return card


def save_gear_catalog_image(image_bytes: bytes, slug: str) -> str:
    safe = re.sub(r"[^a-z0-9-]+", "", (slug or "").lower()).strip("-") or "gear"
    if ".." in safe or "/" in safe:
        raise ValueError("invalid_slug")
    _GEAR_IMG_DIR.mkdir(parents=True, exist_ok=True)
    dest = _GEAR_IMG_DIR / f"{safe}.jpg"
    dest.write_bytes(image_bytes)
    return f"{_STATIC_GEAR_PREFIX}{safe}.jpg"


def is_user_owned_gear_image(url: str, item: dict[str, Any] | None = None) -> bool:
    """User uploads live under images/ — never replace those with catalog photos."""
    raw = str(url or "").strip()
    if raw.startswith("images/") and ".." not in raw and "/" not in raw[7:]:
        return True
    if raw.startswith("/media/") and ".." not in raw:
        return True
    specs = item.get("specs") if isinstance(item, dict) else None
    if isinstance(specs, dict) and specs.get("custom") and raw and not local_gear_image_url(raw):
        return True
    return False


def restore_catalog_images(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for raw in _all_catalog():
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
        user_url = str(item.get("image_url") or "").strip()
        if is_user_owned_gear_image(user_url, item):
            item["image_url"] = _orig_sanitize_gear_image_url(user_url) or user_url
        else:
            current = local_gear_image_url(user_url)
            catalog_img = local_gear_image_url(str(raw.get("image_url") or "")) if raw else ""
            item["image_url"] = current or catalog_img
        if raw:
            item["id"] = str(raw.get("id") or item.get("id") or "")
            item["kind"] = raw.get("kind") or raw.get("type") or item.get("kind")
            item["type"] = raw.get("type") or item.get("type") or item.get("kind")
        restored.append(item)
    return restored


_orig_normalize_gear_specs = db_mod.normalize_gear_specs


def _normalize_gear_specs_with_catalog(raw: Any) -> list[dict[str, Any]]:
    return restore_catalog_images(_orig_normalize_gear_specs(raw))


db_mod.normalize_gear_specs = _normalize_gear_specs_with_catalog


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
    for raw in _all_catalog():
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
        jpeg = await asyncio.to_thread(encode_scan_jpeg, raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="gear_photo_required") from exc
    image_url = await asyncio.to_thread(save_bean_image, jpeg, "gear.jpg")
    return {"image_url": image_url}


@router.post("/{gear_id}/photo")
async def admin_catalog_photo(
    gear_id: str,
    file: UploadFile = File(...),
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del _admin
    item = _find_catalog_item(gear_id)
    if not item:
        raise HTTPException(status_code=404, detail="not_found")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_image")
    slug = _catalog_photo_slug(item)
    try:
        jpeg = await asyncio.to_thread(encode_scan_jpeg, raw)
        image_url = await asyncio.to_thread(save_gear_catalog_image, jpeg, slug)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="gear_photo_required") from exc
    card = _upsert_gear_row({**item, "image_url": image_url}, image_url)
    return {"gear": card, "item": card, "image_url": card.get("image_url") or image_url}


async def _read_create_payload(request: Request) -> tuple[GearCreateIn, bytes, str]:
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_json") from exc
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="invalid_json")
        return GearCreateIn.model_validate(raw), b"", ""
    form = await request.form()
    payload = GearCreateIn.model_validate(
        {
            "brand": form.get("brand") or "",
            "name": form.get("name") or "",
            "type": form.get("type") or "",
            "kind": form.get("kind") or "",
            "highlights": form.get("highlights") or [],
            "specs": form.get("specs") or {},
        }
    )
    upload = form.get("file") or form.get("image") or form.get("photo")
    raw_bytes = b""
    filename = ""
    if isinstance(upload, StarletteUploadFile):
        raw_bytes = await upload.read()
        filename = str(upload.filename or "")
    return payload, raw_bytes, filename


@router.post("")
async def create_gear(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    del _admin
    try:
        payload, raw_bytes, filename = await _read_create_payload(request)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="gear_admin_fail") from exc
    slot = _canonical_gear_kind(payload.type or payload.kind)
    if slot not in _GEAR_SLOTS:
        raise HTTPException(status_code=422, detail="gear_type_invalid")
    kind = _canonical_gear_kind(payload.kind) if payload.kind else slot
    if kind not in _GEAR_SLOTS:
        kind = slot
    name = payload.name.strip()
    brand = payload.brand.strip()
    if not name:
        raise HTTPException(status_code=400, detail="gear_name_required")
    image_url = ""
    with connect() as conn:
        _ensure_gear_table(conn)
        existing = conn.execute(
            "SELECT * FROM gear WHERE lower(name) = lower(?) AND lower(brand) = lower(?)",
            (name, brand),
        ).fetchone()
        existing_id = str(existing["id"]) if existing else ""
        taken = {str(row["id"]) for row in conn.execute("SELECT id FROM gear").fetchall()}
        taken.update(str(item.get("id") or "") for item in GEAR_CATALOG)
        slug = _unique_gear_slug(brand, name, existing_id, taken)
        if raw_bytes:
            try:
                jpeg = await asyncio.to_thread(encode_scan_jpeg, raw_bytes)
                image_url = await asyncio.to_thread(save_gear_catalog_image, jpeg, slug)
            except Exception as exc:
                raise HTTPException(status_code=422, detail="gear_photo_required") from exc
        elif existing:
            image_url = str(existing["image_url"] or "")
        highlights = list(payload.highlights or [])
        specs = dict(payload.specs or {})
        if existing and not highlights:
            try:
                parsed = json.loads(existing["highlights"] or "[]")
                highlights = parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                highlights = []
        if existing and not specs:
            try:
                parsed = json.loads(existing["specs"] or "{}")
                specs = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                specs = {}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            """
            INSERT INTO gear (id, brand, name, type, kind, highlights, specs, image_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                brand = excluded.brand,
                name = excluded.name,
                type = excluded.type,
                kind = excluded.kind,
                highlights = excluded.highlights,
                specs = excluded.specs,
                image_url = excluded.image_url
            """,
            (
                slug,
                brand,
                name,
                slot,
                kind,
                json.dumps(highlights if isinstance(highlights, list) else [], ensure_ascii=False),
                json.dumps(specs if isinstance(specs, dict) else {}, ensure_ascii=False),
                image_url,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM gear WHERE id = ?", (slug,)).fetchone()
    del filename
    card = normalize_gear_item(_row_to_catalog(row))
    if not card:
        raise HTTPException(status_code=422, detail="gear_admin_fail")
    card["kind"] = kind
    card["type"] = slot
    card["image_url"] = local_gear_image_url(str(card.get("image_url") or image_url)) or image_url
    return {"gear": card, "item": card}


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
