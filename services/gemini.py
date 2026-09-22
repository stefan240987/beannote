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
    STORY_LANG,
    _gemini_generate_json,
    _with_scan_matches,
    encode_scan_jpeg,
    fetch_product_page,
    get_gemini_api_key,
    normalize_scan_fields,
)
from translations import SUPPORTED_LANGUAGES, normalize_lang

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
# Scripts outside the active UI languages. A translated field that is still
# mostly written in one of these was echoed from the shop page.
_NON_APP_SCRIPT = re.compile(
    "["
    "\u0400-\u04FF"
    "\u0500-\u052F"
    "\u0590-\u05FF"
    "\u0600-\u06FF"
    "\u0900-\u097F"
    "\u0E00-\u0E7F"
    "\u1100-\u11FF"
    "\u3040-\u30FF"
    "\u31F0-\u31FF"
    "\u3400-\u9FFF"
    "\uAC00-\uD7AF"
    "\uFF66-\uFF9D"
    "]"
)
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
    raw: dict[str, Any] = {}
    brief = _page_brief(facts, page_text or html, page_url)
    try:
        raw = _gemini_generate_json(
            get_gemini_api_key(),
            _from_url_prompt(brief, page_url, candidates, chosen),
            20_000,
            tools=None,
        )
    except Exception as exc:
        print(f"from-url gemini skipped: {type(exc).__name__}: {exc}")
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    mapped = _merge_facts(
        _map_url_fields(raw, page_url, candidates, chosen),
        facts,
        page_url,
        keep_untranslated=not bool(raw),
    )
    mapped = _lock_printed_identity(mapped, facts)
    if not (mapped.get("name") or "").strip() or not (mapped.get("roaster") or "").strip():
        raise ValueError("required")

    parsed = normalize_scan_fields(mapped, lang=chosen)
    parsed["story"] = clean_story_field(parsed.get("story"))
    active_story = _story_for_lang(parsed.get("story"), chosen)
    if active_story:
        parsed["official_notes"] = active_story
        parsed["roaster_notes"] = active_story
    else:
        parsed["official_notes"] = clean_story_text(parsed.get("official_notes"))
        parsed["roaster_notes"] = clean_story_text(parsed.get("roaster_notes"))
    parsed["scan_source"] = "url"
    parsed["scan_enrichment"] = "url+gemini" if raw else "url+jsonld"
    parsed["roaster_url"] = parsed.get("roaster_url") or page_url
    parsed["product_page_url"] = page_url

    try:
        local_image = _cache_product_image(mapped.get("image_url") or "")
    except Exception as exc:
        print(f"from-url image cache skipped: {type(exc).__name__}: {exc}")
        local_image = ""
    if local_image:
        parsed["image_url"] = local_image
        parsed["official_image_url"] = local_image
        parsed["product_image_url"] = local_image
        parsed["snapshot_url"] = local_image
        parsed["preview"] = local_image
        parsed["image_candidates"] = [local_image]
    try:
        return _with_scan_matches(parsed)
    except Exception as exc:
        print(f"from-url match attach skipped: {type(exc).__name__}: {exc}")
        parsed.setdefault("similar", [])
        return parsed


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
    chosen = normalize_lang(lang)
    lang_keys = ", ".join(f'"{code}"' for code in SUPPORTED_LANGUAGES)
    lang_names = " and ".join(STORY_LANG.get(code, code) for code in SUPPORTED_LANGUAGES)
    active = STORY_LANG.get(chosen, "Danish")
    return (
        "Extract coffee bean product metadata from THIS SHOP PAGE only. "
        "The page may be written in any language. "
        "No outside knowledge, no other coffees, no invented tasting notes.\n"
        f"Page: {page_url}\n"
        "Return JSON only with these keys:\n"
        '- "name": product name exactly as printed. Do not translate it.\n'
        '- "roaster": brand name exactly as printed. Do not translate it.\n'
        f'- "origin": origin countries or regions in {active}. '
        "Dedicated key only — never inside the story.\n"
        '- "altitude": farm altitude / MASL if stated. Dedicated key only.\n'
        '- "process": one catalog token if the page states a method, else "". '
        "Use Vasket, Natural, Honey, Anaerob, Washed, or Anaerobic.\n"
        '- "roast_level": one catalog token if the page states a roast degree, else "". '
        "Use Lys, Medium-Lys, Medium, Medium-Mørk, Mørk, Light, Medium-Light, "
        "Medium-Dark, or Dark.\n"
        '- "brew_ratio": brew ratio only if the page states one (e.g. 1:2, 1:16).\n'
        f'- "flavor_notes": object with keys {lang_keys}. Each value is an array of '
        f"tasting notes translated into that language. Same notes, same order, in {lang_names}. "
        "Do not leave the shop's original wording in these arrays.\n"
        f'- "suitable_for": array of brew methods the page actually names '
        f"(Espresso, Filter, AeroPress), written in {active}.\n"
        '- "image_url": main product photo URL (prefer the bag/packshot)\n'
        f'- "story": object with keys {lang_keys}. Each value is 2–3 sentences '
        f"(around 30-40 words) in that language on taste and roaster story. "
        f"Write every key in {lang_names} even when the page is another language. "
        "Same facts in every language. "
        "FORBIDDEN in story: the shop's original script, raw copy-paste, product specifications, "
        "weight options (e.g. 500g, 1kg), machine listings, brew-ratio specs, "
        "holdbarhed/shelf life, varianter/variants, or any Produktspecifikationer dump. "
        "Put every technical parameter in its dedicated JSON key instead.\n"
        "If a field is not on the page, use \"\" , [] , or an object with empty strings.\n\n"
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


_STORY_MAX_CHARS = 350
_STORY_SECTION_HEAD = re.compile(
    r"(?im)(?:^|(?<=[.!?]\s))"
    r"(?:"
    r"produktspecifikation(?:er)?|"
    r"product\s*specifications?|"
    r"produktspezifikation(?:en)?|"
    r"sp[eé]cifications?(?:\s+produit)?|"
    r"especificaciones|"
    r"holdbarhed|shelf\s*life|best\s*before|haltbarkeit|durabilit[eé]|caducidad|"
    r"varianter|variants|varianten|variantes|"
    r"tekniske\s*data|technical\s*(?:data|specs?|details)|"
    r"brygge(?:anvisning|forhold)|brew\s*(?:ratio|specs?|instructions?)|"
    r"compatible\s*machines?|maskiner|"
    r"v[æe]gt(?:e|options?)?|weight\s*options?"
    r")\b"
)
_STORY_BOILERPLATE_TOKEN = re.compile(
    r"(?i)\b(?:"
    r"produktspecifikation(?:er)?|product\s*specifications?|"
    r"holdbarhed|shelf\s*life|varianter|variants"
    r")\b[:\s]*"
)
_STORY_WEIGHT_OPTIONS = re.compile(
    r"(?i)(?:\s*(?:f[aå]s i|available in|sizes?)[:\s]*)?"
    r"(?:\b\d+(?:[.,]\d+)?\s*(?:g|kg|gr|gram|grams)\b(?:\s*[,;/&+|og and]+\s*)?)+"
)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_untranslated(text: str) -> bool:
    """True when a da/en field is still mostly the shop page's original script."""
    letters = [ch for ch in str(text or "") if ch.isalpha()]
    if not letters:
        return False
    foreign = sum(1 for ch in letters if _NON_APP_SCRIPT.match(ch))
    if not foreign:
        return False
    ratio = foreign / len(letters)
    if len(letters) <= 16:
        return ratio >= 0.5
    return foreign >= 8 and ratio >= 0.4


def _story_for_lang(story: Any, lang: str) -> str:
    if not isinstance(story, dict):
        text = clean_story_text(story)
        return "" if _is_untranslated(text) else text
    chosen = normalize_lang(lang)
    for code in (chosen, *SUPPORTED_LANGUAGES):
        text = clean_story_text(story.get(code))
        if text and not _is_untranslated(text):
            return text
    return ""


def _supported_text_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for code in SUPPORTED_LANGUAGES:
        text = clean_story_text(value.get(code))
        if text and not _is_untranslated(text):
            out[code] = text
    return out


def _supported_list_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for code in SUPPORTED_LANGUAGES:
        tags = [tag for tag in _as_string_list(value.get(code)) if tag and not _is_untranslated(tag)]
        if tags:
            out[code] = tags
    return out


def _copy_for_lang(value: Any, lang: str) -> str:
    if isinstance(value, dict):
        return _story_for_lang(value, lang)
    text = _clean_text(value)
    if _is_untranslated(text):
        return ""
    return text


def _flavor_field(value: Any) -> dict[str, list[str]] | list[str]:
    mapped = _supported_list_map(value)
    if mapped:
        return mapped
    return [tag for tag in _as_string_list(value) if not _is_untranslated(tag)]


def clean_story_text(value: Any) -> str:
    """Strip shop boilerplate and cap a story/description at 350 characters."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    heading = _STORY_SECTION_HEAD.search(raw)
    if heading:
        raw = raw[: heading.start()].rstrip()
    text = _clean_text(raw)
    if not text:
        return ""
    text = _STORY_BOILERPLATE_TOKEN.sub("", text)
    text = _STORY_WEIGHT_OPTIONS.sub(" ", text)
    text = re.sub(r"(?i)\b(?:f[aå]s i|available in)\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?:[.!?]\s*){2,}", ". ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"^[\s\-:;,.|]+", "", text).rstrip(" -:;|")
    if len(text) <= _STORY_MAX_CHARS:
        return text
    window = text[:_STORY_MAX_CHARS].rstrip()
    sentence = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if sentence >= 80:
        return window[: sentence + 1].strip()
    space = window.rfind(" ")
    clipped = window[:space] if space >= 40 else window
    return clipped.rstrip(" ,;:-") + "…"


def clean_story_field(value: Any) -> Any:
    """Clean a plain story string or a language map of story strings."""
    if isinstance(value, dict):
        cleaned: dict[str, str] = {}
        for key, item in value.items():
            text = clean_story_text(item)
            if text:
                cleaned[str(key)] = text
        return cleaned
    return clean_story_text(value)


def _map_url_fields(
    raw: dict[str, Any],
    page_url: str,
    candidates: list[str],
    lang: str = "da",
) -> dict[str, Any]:
    name = _clean_text(raw.get("name") or raw.get("bean_name"))
    story_map = _supported_text_map(raw.get("story")) or _supported_text_map(raw.get("description"))
    description = ""
    if not story_map:
        description = clean_story_text(
            _copy_for_lang(raw.get("description") or raw.get("official_notes") or raw.get("story"), lang)
        )
        if _is_untranslated(description):
            description = ""
    notes = _story_for_lang(story_map, lang) or description
    flavors = _flavor_field(raw.get("flavor_notes") or raw.get("flavor_tags"))
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
        "origin": _copy_for_lang(raw.get("origin"), lang),
        "altitude": "" if _is_untranslated(_clean_text(raw.get("altitude"))) else _clean_text(raw.get("altitude")),
        "process": "" if _is_untranslated(_clean_text(raw.get("process"))) else _clean_text(raw.get("process")),
        "roast_level": "" if _is_untranslated(_clean_text(raw.get("roast_level"))) else _clean_text(raw.get("roast_level")),
        "brew_ratio": _clean_text(raw.get("brew_ratio")),
        "flavor_notes": flavors,
        "flavor_tags": flavors if isinstance(flavors, dict) else {},
        "suitable_for": [tag for tag in _as_string_list(raw.get("suitable_for")) if not _is_untranslated(tag)],
        "official_notes": notes,
        "roaster_notes": notes,
        "story": story_map or description,
        "image_url": image_url,
        "product_image_url": image_url,
        "roaster_url": sanitize_roaster_url(raw.get("roaster_url") or page_url) or page_url,
        "product_page_url": page_url,
    }


def _lock_printed_identity(mapped: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Keep the shop's product title and brand. Those are the dedupe key."""
    out = dict(mapped)
    name = _clean_text(facts.get("name"))
    roaster = _clean_text(facts.get("roaster"))
    if name:
        out["name"] = name
        out["bean_name"] = name
    if roaster:
        out["roaster"] = roaster
    return out


def _page_copy(text: str, *, keep_untranslated: bool) -> str:
    clean = clean_story_text(text)
    if not clean:
        return ""
    if not keep_untranslated and _is_untranslated(clean):
        return ""
    return clean


def _merge_facts(
    mapped: dict[str, Any],
    facts: dict[str, Any],
    page_url: str,
    *,
    keep_untranslated: bool = True,
) -> dict[str, Any]:
    out = dict(mapped)
    if not out.get("name"):
        out["name"] = facts.get("name") or ""
        out["bean_name"] = out["name"]
    if not out.get("roaster"):
        out["roaster"] = facts.get("roaster") or ""
    if not out.get("origin"):
        origin = _clean_text(facts.get("origin"))
        if origin and (keep_untranslated or not _is_untranslated(origin)):
            out["origin"] = origin
    if not out.get("roast_level"):
        roast = _clean_text(facts.get("roast_level"))
        if roast and (keep_untranslated or not _is_untranslated(roast)):
            out["roast_level"] = roast
    if not out.get("flavor_notes"):
        flavors = [
            tag
            for tag in list(facts.get("flavor_notes") or [])
            if keep_untranslated or not _is_untranslated(str(tag))
        ]
        if flavors:
            out["flavor_notes"] = flavors
    if not out.get("suitable_for"):
        out["suitable_for"] = list(facts.get("suitable_for") or [])
    if not out.get("process"):
        process = _clean_text(facts.get("process"))
        if process and (keep_untranslated or not _is_untranslated(process)):
            out["process"] = process
    if not out.get("altitude"):
        out["altitude"] = facts.get("altitude") or ""
    if not out.get("brew_ratio"):
        out["brew_ratio"] = facts.get("brew_ratio") or ""
    if not out.get("official_notes"):
        story = _page_copy(str(facts.get("description") or ""), keep_untranslated=keep_untranslated)
        if story:
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
    raw_description = _clean_text(product.get("description")) or _clean_text(og.get("og:description"))
    images = _jsonld_images(product.get("image"))
    og_image = og.get("og:image") or og.get("og:image:url") or og.get("twitter:image") or ""
    if og_image:
        images = [og_image, *images]
    origin, roast, flavors, suitable = _infer_from_copy(f"{name} {raw_description}")
    return {
        "name": name,
        "roaster": brand,
        "description": clean_story_text(raw_description),
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
