"""Parse coffee shop product pages with Gemini and cache the product photo."""

from __future__ import annotations

import re
import uuid
from typing import Any
from urllib.parse import urljoin

from db import get_catalog_dir, sanitize_roaster_url
from image_search import fetch_official_image_bytes, sanitize_image_url
from ocr import (
    MAX_PRODUCT_PAGE_CHARS,
    _gemini_generate_json,
    _with_scan_matches,
    encode_scan_jpeg,
    fetch_product_page,
    get_gemini_api_key,
    normalize_scan_fields,
)
from translations import normalize_lang

_STATIC_BEAN_PREFIX = "/static/img/beans/"
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"""([a-zA-Z_:][\w:.-]*)\s*=\s*(['"])(.*?)\2""", re.S)
_JSONLD_IMAGE_RE = re.compile(
    r'"image"\s*:\s*(?:\[\s*"([^"]+)"|"([^"]+)")',
    re.I,
)
_IMG_SRC_RE = re.compile(
    r'<img\b[^>]+(?:src|data-src|data-original)\s*=\s*[\'"]([^\'"]+)[\'"]',
    re.I,
)


def parse_bean_from_url(url: str, lang: str = "da") -> dict[str, Any]:
    """Fetch a public product page, extract a bean draft with Gemini, cache the photo."""
    page_url = sanitize_roaster_url(url)
    if not page_url:
        raise ValueError("invalid_url")
    if not get_gemini_api_key():
        raise RuntimeError("ocr_missing")

    html, page_text = fetch_product_page(page_url)
    if not html and not page_text:
        raise ValueError("from_url_fail")

    chosen = normalize_lang(lang)
    candidates = _page_image_candidates(html, page_url)
    prompt = _from_url_prompt(page_text or html, page_url, candidates, chosen)
    key = get_gemini_api_key()
    raw = _gemini_generate_json(key, prompt, 25_000, tools=None)
    if not isinstance(raw, dict) or not raw:
        raise RuntimeError("from_url_fail")

    mapped = _map_url_fields(raw, page_url, candidates)
    if not (mapped.get("name") or "").strip() or not (mapped.get("roaster") or "").strip():
        raise ValueError("required")

    parsed = normalize_scan_fields(mapped, lang=chosen)
    parsed["scan_source"] = "url"
    parsed["scan_enrichment"] = "url+gemini"
    parsed["roaster_url"] = parsed.get("roaster_url") or page_url
    parsed["product_page_url"] = page_url

    local_image = _cache_product_image(mapped.get("image_url") or "")
    if local_image:
        parsed["image_url"] = local_image
        parsed["official_image_url"] = local_image
        parsed["product_image_url"] = local_image
        parsed["snapshot_url"] = local_image
        parsed["preview"] = local_image
        parsed["image_candidates"] = [local_image]
    return _with_scan_matches(parsed)


def _from_url_prompt(page_text: str, page_url: str, images: list[str], lang: str) -> str:
    snippet = (page_text or "")[:MAX_PRODUCT_PAGE_CHARS]
    image_hint = "\n".join(f"- {item}" for item in images[:6]) or "- (none found in HTML)"
    return (
        "Extract coffee bean product metadata from THIS SHOP PAGE only. "
        "No outside knowledge, no other coffees, no invented tasting notes.\n"
        f"Page: {page_url}\n"
        "Return JSON only with these keys:\n"
        '- "name": coffee bean / product name\n'
        '- "roaster": roaster / brand name\n'
        '- "origin": origin country / region\n'
        '- "roast_level": roast degree (Lys, Medium-Lys, Medium, Medium-Mørk, Mørk, or the page wording)\n'
        '- "flavor_notes": array of flavor tags copied from the page\n'
        '- "suitable_for": array of brew suitability tags (Espresso, Filter, AeroPress, etc.)\n'
        '- "image_url": main product photo URL (prefer the bag/packshot)\n'
        '- "description": short roaster story / description from the page\n'
        f"Write name, origin, description, and flavor notes in {lang} when the page language allows.\n"
        "If a field is not on the page, use \"\" or [].\n\n"
        f"Candidate product images:\n{image_hint}\n\n"
        "PAGE TEXT:\n"
        f"{snippet}"
    )


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        parts = re.split(r"[,;/|]", value)
        return [part.strip() for part in parts if part.strip()]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out
    return []


def _map_url_fields(raw: dict[str, Any], page_url: str, candidates: list[str]) -> dict[str, Any]:
    name = str(raw.get("name") or raw.get("bean_name") or "").strip()
    description = str(raw.get("description") or raw.get("official_notes") or raw.get("story") or "").strip()
    image_url = _first_image_url(
        raw.get("image_url"),
        raw.get("product_image_url"),
        raw.get("product_image_urls"),
        candidates,
        page_url=page_url,
    )
    return {
        "name": name,
        "bean_name": name,
        "roaster": str(raw.get("roaster") or "").strip(),
        "origin": str(raw.get("origin") or "").strip(),
        "roast_level": str(raw.get("roast_level") or "").strip(),
        "flavor_notes": _as_string_list(raw.get("flavor_notes") or raw.get("flavor_tags")),
        "suitable_for": _as_string_list(raw.get("suitable_for")),
        "official_notes": description,
        "roaster_notes": description,
        "story": description,
        "image_url": image_url,
        "product_image_url": image_url,
        "roaster_url": sanitize_roaster_url(raw.get("roaster_url") or page_url) or page_url,
        "product_page_url": page_url,
    }


def _first_image_url(*sources: Any, page_url: str = "") -> str:
    pending: list[Any] = list(sources)
    seen: set[str] = set()
    while pending:
        source = pending.pop(0)
        if isinstance(source, (list, tuple, set)):
            pending[0:0] = list(source)
            continue
        raw = str(source or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        absolute = urljoin(page_url, raw) if page_url else raw
        clean = sanitize_image_url(absolute)
        if clean:
            return clean
    return ""


def _meta_attrs(tag: str) -> dict[str, str]:
    return {match.group(1).lower(): match.group(3) for match in _ATTR_RE.finditer(tag or "")}


def _page_image_candidates(html: str, page_url: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        absolute = urljoin(page_url, str(raw or "").strip())
        clean = sanitize_image_url(absolute)
        if clean and clean not in seen:
            seen.add(clean)
            found.append(clean)

    for tag in _META_TAG_RE.findall(html or ""):
        attrs = _meta_attrs(tag)
        key = attrs.get("property") or attrs.get("name") or ""
        if key.lower() in {
            "og:image",
            "og:image:url",
            "og:image:secure_url",
            "twitter:image",
            "twitter:image:src",
        }:
            add(attrs.get("content") or "")
    for match in _JSONLD_IMAGE_RE.finditer(html or ""):
        add(match.group(1) or match.group(2) or "")
    for match in _IMG_SRC_RE.finditer(html or ""):
        src = match.group(1) or ""
        if any(token in src.lower() for token in ("logo", "icon", "sprite", "pixel", "1x1")):
            continue
        add(src)
        if len(found) >= 8:
            break
    return found[:8]


def _cache_product_image(image_url: str) -> str:
    clean = sanitize_image_url(image_url, resolve_dns=True)
    if not clean:
        return ""
    raw = fetch_official_image_bytes(clean)
    if not raw:
        return ""
    try:
        jpeg = encode_scan_jpeg(raw)
    except Exception:
        return ""
    if not jpeg:
        return ""
    name = f"draft-{uuid.uuid4().hex}.jpg"
    dest = get_catalog_dir("beans") / name
    dest.write_bytes(jpeg)
    return f"{_STATIC_BEAN_PREFIX}{name}"
