"""Parse coffee shop product pages with Gemini and cache the product photo."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

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
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
    re.I,
)
_JSONLD_IMAGE_RE = re.compile(
    r'"image"\s*:\s*(?:\[\s*"([^"]+)"|"([^"]+)")',
    re.I,
)
_IMG_SRC_RE = re.compile(
    r'<img\b[^>]+(?:src|data-src|data-original)\s*=\s*[\'"]([^\'"]+)[\'"]',
    re.I,
)
_TITLE_RE = re.compile(r"<title[^>]*>([\s\S]*?)</title>", re.I)
_TRACKING_QUERY = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "utm_id",
    "gclid",
    "gbraid",
    "wbraid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "gad_source",
    "gad_campaignid",
    "msclkid",
    "twclid",
}


def parse_bean_from_url(url: str, lang: str = "da") -> dict[str, Any]:
    """Fetch a public product page, extract a bean draft with Gemini, cache the photo."""
    page_url = _public_product_url(url)
    if not page_url:
        raise ValueError("invalid_url")
    if not get_gemini_api_key():
        raise RuntimeError("ocr_missing")

    html, page_text = _load_product_page(page_url)
    if not html and not page_text:
        raise ValueError("from_url_fail")

    chosen = normalize_lang(lang)
    facts = _page_facts(html, page_url)
    candidates = _page_image_candidates(html, page_url, facts)
    brief = _page_brief(facts, page_text or html, page_url)
    raw: dict[str, Any] = {}
    try:
        raw = _gemini_generate_json(get_gemini_api_key(), _from_url_prompt(brief, page_url, candidates, chosen), 20_000, tools=None)
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    mapped = _merge_facts(_map_url_fields(raw, page_url, candidates), facts, page_url)
    if not (mapped.get("name") or "").strip() or not (mapped.get("roaster") or "").strip():
        raise ValueError("required")

    parsed = normalize_scan_fields(mapped, lang=chosen)
    parsed["scan_source"] = "url"
    parsed["scan_enrichment"] = "url+gemini" if raw else "url+jsonld"
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


def _public_product_url(url: str) -> str:
    clean = sanitize_roaster_url(url)
    if not clean:
        return ""
    parsed = urlparse(clean)
    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in _TRACKING_QUERY
    ]
    return urlunparse(("https", parsed.netloc.lower(), parsed.path or "", "", urlencode(kept), ""))


def _load_product_page(page_url: str) -> tuple[str, str]:
    html, page_text = fetch_product_page(page_url)
    if html or page_text:
        return html, page_text
    parsed = urlparse(page_url)
    if parsed.query:
        bare = urlunparse(("https", parsed.netloc, parsed.path or "", "", "", ""))
        return fetch_product_page(bare)
    return "", ""


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


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _map_url_fields(raw: dict[str, Any], page_url: str, candidates: list[str]) -> dict[str, Any]:
    name = _clean_text(raw.get("name") or raw.get("bean_name"))
    description = _clean_text(raw.get("description") or raw.get("official_notes") or raw.get("story"))
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
        "roaster": _clean_text(raw.get("roaster")),
        "origin": _clean_text(raw.get("origin")),
        "roast_level": _clean_text(raw.get("roast_level")),
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


def _merge_facts(mapped: dict[str, Any], facts: dict[str, Any], page_url: str) -> dict[str, Any]:
    out = dict(mapped)
    if not out.get("name"):
        out["name"] = facts.get("name") or ""
        out["bean_name"] = out["name"]
    if not out.get("roaster"):
        out["roaster"] = facts.get("roaster") or ""
    if not out.get("origin"):
        out["origin"] = facts.get("origin") or ""
    if not out.get("roast_level"):
        out["roast_level"] = facts.get("roast_level") or ""
    if not out.get("flavor_notes"):
        out["flavor_notes"] = list(facts.get("flavor_notes") or [])
    if not out.get("suitable_for"):
        out["suitable_for"] = list(facts.get("suitable_for") or [])
    if not out.get("official_notes"):
        story = facts.get("description") or ""
        out["official_notes"] = story
        out["roaster_notes"] = story
        out["story"] = story
    if not out.get("image_url"):
        image_url = _first_image_url(facts.get("image_url"), facts.get("images"), page_url=page_url)
        out["image_url"] = image_url
        out["product_image_url"] = image_url
    return out


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
        if absolute.startswith("http://"):
            absolute = "https://" + absolute[len("http://") :]
        clean = sanitize_image_url(absolute)
        if clean:
            return clean
    return ""


def _meta_attrs(tag: str) -> dict[str, str]:
    return {match.group(1).lower(): match.group(3) for match in _ATTR_RE.finditer(tag or "")}


def _jsonld_objects(html: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if "@graph" in node:
            walk(node.get("@graph"))
        found.append(node)

    for block in _JSONLD_RE.findall(html or ""):
        raw = (block or "").strip()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return found


def _jsonld_type(node: dict[str, Any]) -> str:
    raw = node.get("@type") or ""
    if isinstance(raw, list):
        return " ".join(str(item) for item in raw).lower()
    return str(raw).lower()


def _jsonld_name(value: Any) -> str:
    if isinstance(value, dict):
        return _clean_text(value.get("name") or value.get("legalName"))
    return _clean_text(value)


def _jsonld_images(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(value.get("url") or value.get("contentUrl") or "")]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_jsonld_images(item))
        return out
    return []


def _page_facts(html: str, page_url: str) -> dict[str, Any]:
    title = _clean_text(_TITLE_RE.search(html or "").group(1) if _TITLE_RE.search(html or "") else "")
    og: dict[str, str] = {}
    for tag in _META_TAG_RE.findall(html or ""):
        attrs = _meta_attrs(tag)
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content") or ""
        if key and content:
            og[key] = content

    product: dict[str, Any] = {}
    organization = ""
    for node in _jsonld_objects(html):
        kind = _jsonld_type(node)
        if "product" in kind and not product.get("name"):
            product = node
        if "organization" in kind or "brand" in kind:
            organization = organization or _jsonld_name(node)

    name = _jsonld_name(product.get("name")) or _clean_text(og.get("og:title")) or title
    brand = _jsonld_name(product.get("brand")) or organization
    description = _clean_text(product.get("description")) or _clean_text(og.get("og:description"))
    images = _jsonld_images(product.get("image"))
    og_image = og.get("og:image") or og.get("og:image:url") or og.get("twitter:image") or ""
    if og_image:
        images = [og_image, *images]
    origin, roast, flavors, suitable = _infer_from_copy(f"{name} {description}")
    return {
        "name": name,
        "roaster": brand,
        "description": description,
        "origin": origin,
        "roast_level": roast,
        "flavor_notes": flavors,
        "suitable_for": suitable,
        "image_url": _first_image_url(images, page_url=page_url),
        "images": images,
    }


def _infer_from_copy(blob: str) -> tuple[str, str, list[str], list[str]]:
    text = blob or ""
    lowered = text.lower()
    origin = ""
    for country in (
        "Brasilien",
        "Etiopien",
        "Colombia",
        "Kenya",
        "Indien",
        "Guatemala",
        "Peru",
        "Honduras",
        "Rwanda",
        "Tanzania",
        "Uganda",
        "Mexico",
        "Nicaragua",
        "Costa Rica",
        "El Salvador",
        "Indonesia",
        "Indonesien",
    ):
        if country.lower() in lowered:
            origin = country if not origin else f"{origin} & {country}"
    roast = ""
    if re.search(r"ristningsgrad|roast", lowered):
        if "mørk" in lowered or "dark" in lowered:
            roast = "Mørk"
        elif "lys" in lowered or "light" in lowered:
            roast = "Lys"
        elif "mellem" in lowered or "medium" in lowered:
            roast = "Medium"
        elif "espresso" in lowered:
            roast = "Espresso"
    flavors: list[str] = []
    notes_match = re.search(r"(?:noter|notes|smag)\s*[:–-]\s*([^\n\.]+)", text, re.I)
    if notes_match:
        flavors = _as_string_list(notes_match.group(1))
    suitable: list[str] = []
    if "espresso" in lowered:
        suitable.append("Espresso")
    if "filter" in lowered or "stempel" in lowered:
        suitable.append("Filter")
    if "fuldautomat" in lowered or "jura" in lowered or "delonghi" in lowered:
        suitable.append("Fuldautomatisk")
    return origin, roast, flavors, suitable


def _page_brief(facts: dict[str, Any], page_text: str, page_url: str) -> str:
    bits = [
        f"Title: {facts.get('name') or ''}",
        f"Roaster: {facts.get('roaster') or ''}",
        f"Page: {page_url}",
        f"Description: {facts.get('description') or ''}",
    ]
    visible = re.sub(r"\n{3,}", "\n\n", page_text or "")
    needle = (facts.get("name") or "")[:24]
    start = 0
    if needle:
        idx = visible.lower().find(needle.lower())
        if idx >= 0:
            start = max(0, idx - 200)
    body = visible[start : start + 6000]
    return "\n".join(bits) + "\n\nVISIBLE TEXT:\n" + body


def _page_image_candidates(html: str, page_url: str, facts: dict[str, Any] | None = None) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        absolute = urljoin(page_url, str(raw or "").strip())
        if absolute.startswith("http://"):
            absolute = "https://" + absolute[len("http://") :]
        clean = sanitize_image_url(absolute)
        if clean and clean not in seen:
            seen.add(clean)
            found.append(clean)

    for image in (facts or {}).get("images") or []:
        add(str(image or ""))
    if (facts or {}).get("image_url"):
        add(str(facts.get("image_url") or ""))
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
    raw_url = str(image_url or "").strip()
    if raw_url.startswith("http://"):
        raw_url = "https://" + raw_url[len("http://") :]
    clean = sanitize_image_url(raw_url, resolve_dns=True)
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
