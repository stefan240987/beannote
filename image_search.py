"""Autonomous high-res coffee product image search.

Primary source is DuckDuckGo Images (no API key, no extra packages).
Gemini Search Grounding stays a last-resort fallback in ocr.py.
No brand-specific studio backfill.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any
from urllib.parse import urlencode, urlparse

from PIL import Image

MAX_IMAGE_CANDIDATES = 3
SEARCH_TIMEOUT = 8.0
_MAX_OFFICIAL_IMAGE_BYTES = 8 * 1024 * 1024

_BLOCKED_IMAGE_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_IMAGE_CDN_HINTS = (
    "cdn",
    "shopify",
    "cloudinary",
    "imgix",
    "cloudfront",
    "googleusercontent",
    "wp.com",
    "wp-content",
    "squarespace",
    "bigcommerce",
    "schwarz",
    "openfoodfacts",
    "allegroimg",
    "prom.ua",
    "bing.net",
    "bing.com",
    "duckduckgo",
    "pinimg",
    "wikimedia",
    "staticflickr",
)
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,image/avif,image/webp,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
}
_STOP_TOKENS = {
    "coffee",
    "coffees",
    "beans",
    "bean",
    "bag",
    "bags",
    "kaffe",
    "kava",
    "kawa",
    "cafe",
    "café",
    "the",
    "and",
    "for",
    "with",
    "bio",
    "organic",
}
_DRINK_PENALTY = (
    "latte art",
    "in cup",
    "hotove",
    "hotové",
    "cupping",
    "pouring",
    "recipe",
)


def _host_is_public(host: str) -> bool:
    hostname = (host or "").strip().lower().rstrip(".")
    if not hostname or hostname in _BLOCKED_IMAGE_HOSTS or hostname.endswith(".local"):
        return False
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        raw_ip = info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def is_public_image_url(url: str, *, resolve_dns: bool = False) -> bool:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_IMAGE_HOSTS or host.endswith(".local"):
        return False
    if resolve_dns and not _host_is_public(host):
        return False
    path = (parsed.path or "").lower()
    blob = f"{host} {path} {parsed.query}".lower()
    if any(path.endswith(suffix) for suffix in _IMAGE_SUFFIXES):
        return True
    if any(suffix in blob for suffix in _IMAGE_SUFFIXES):
        return True
    if any(hint in blob for hint in _IMAGE_CDN_HINTS):
        return True
    if host.startswith("images.") or host.startswith("img.") or ".images." in host:
        return True
    return any(token in path for token in ("/image", "/images/", "/img/", "/media/", "/cdn/", "/uploads/", "/th"))


def sanitize_image_url(url: str, *, resolve_dns: bool = False) -> str:
    raw = (url or "").strip().strip("'").strip('"')
    if not raw or raw.lower() in {"none", "null", "undefined"}:
        return ""
    return raw if is_public_image_url(raw, resolve_dns=resolve_dns) else ""


def collect_image_urls(*sources: Any, resolve_dns: bool = False) -> list[str]:
    """Sanitize and de-dupe https product-image URLs from strings or lists."""
    seen: set[str] = set()
    out: list[str] = []
    pending: list[Any] = list(sources)
    while pending:
        source = pending.pop(0)
        if isinstance(source, (list, tuple, set)):
            pending[0:0] = list(source)
            continue
        if isinstance(source, dict):
            pending[0:0] = [
                source.get("image_urls"),
                source.get("urls"),
                source.get("product_image_urls"),
                source.get("image_url"),
                source.get("url"),
                source.get("image"),
                source.get("product_image_url"),
            ]
            continue
        clean = sanitize_image_url(str(source or ""), resolve_dns=resolve_dns)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= MAX_IMAGE_CANDIDATES:
            break
    return out


def fetch_official_image_bytes(url: str, timeout: float = 6.0) -> bytes | None:
    """Download a validated official product image. Returns None on any failure."""
    clean = sanitize_image_url(url, resolve_dns=True)
    if not clean:
        return None

    class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not is_public_image_url(newurl):
                return None
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    request = urllib.request.Request(
        clean,
        headers={
            **_BROWSER_HEADERS,
            "User-Agent": "BeanNote/3.3 (+https://beannote.local)",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(_PublicRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if content_type and not content_type.startswith("image/"):
                return None
            data = response.read(_MAX_OFFICIAL_IMAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if not data or len(data) > _MAX_OFFICIAL_IMAGE_BYTES:
        return None
    try:
        image = Image.open(BytesIO(data))
        image.verify()
    except Exception:
        return None
    return data


def curated_packshot_urls(name: str, roaster: str) -> list[str]:
    """Studio URLs come only from live search and model hints — no brand backfill."""
    del name, roaster
    return []


def curated_packshot_url(name: str, roaster: str) -> str:
    found = curated_packshot_urls(name, roaster)
    return found[0] if found else ""


def _http_get(
    url: str,
    timeout: float = SEARCH_TIMEOUT,
    headers: dict[str, str] | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> bytes:
    request = urllib.request.Request(url, headers=headers or _BROWSER_HEADERS)
    handle = opener or urllib.request.build_opener()
    with handle.open(request, timeout=timeout) as response:
        return response.read()


def _extract_vqd(html: str) -> str:
    for pattern in (
        r"vqd=['\"]([^'\"]+)['\"]",
        r"vqd=([0-9\-]+)",
        r'"vqd"\s*:\s*"([^"]+)"',
        r"vqd:\\['\"]([^'\"]+)['\"]",
    ):
        match = re.search(pattern, html or "")
        if match:
            return match.group(1).strip()
    return ""


def _hit_image_url(item: dict[str, Any]) -> str:
    for key in ("image", "image_url", "thumbnail", "url", "src"):
        raw = str(item.get(key) or "").strip()
        if raw.startswith("https://"):
            return raw
    return ""


def _query_tokens(name: str, roaster: str) -> list[str]:
    blob = f"{roaster} {name}".lower()
    tokens = []
    for raw in re.findall(r"[a-zæøåäöüß0-9]{3,}", blob):
        if raw in _STOP_TOKENS or raw in tokens:
            continue
        tokens.append(raw)
    return tokens


def _search_query(name: str, roaster: str) -> str:
    parts = [part for part in (roaster, name) if part]
    query = " ".join(parts).strip()
    if "coffee" not in query.lower() and "kaffe" not in query.lower():
        query = f"{query} coffee bag"
    else:
        query = f"{query} bag"
    return re.sub(r"\s+", " ", query).strip()


def _score_hit(item: dict[str, Any], tokens: list[str]) -> int:
    title = str(item.get("title") or "").lower()
    image = str(item.get("image") or item.get("url") or "").lower()
    source = str(item.get("url") or "").lower()
    blob = f"{title} {image} {source}"
    score = 0
    for token in tokens:
        if token in title:
            score += 4
        elif token in blob:
            score += 2
    try:
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0
    longest = max(width, height)
    if longest >= 1000:
        score += 4
    elif longest >= 700:
        score += 3
    elif longest >= 400:
        score += 1
    if any(hint in blob for hint in ("bag", "pack", "beans", "ziarn", "zern", "kaffe", "kava", "kawa")):
        score += 2
    if any(hint in blob for hint in _DRINK_PENALTY):
        score -= 4
    if any(hint in image for hint in (".jpg", ".jpeg", ".png", ".webp")):
        score += 1
    return score


def _search_queries(name: str, roaster: str) -> list[str]:
    """Brand-agnostic query variants for the extracted roaster + bean name."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    roaster = re.sub(r"\s+", " ", (roaster or "").strip())
    seen: set[str] = set()
    out: list[str] = []
    candidates = [
        _search_query(name, roaster),
        f"{roaster} {name}".strip(),
        f"{roaster} {name} packshot".strip(),
        f"{name} coffee beans".strip() if name else "",
        f"{roaster} coffee bag".strip() if roaster else "",
    ]
    for query in candidates:
        compact = re.sub(r"\s+", " ", query).strip()
        if len(compact) < 4 or compact.lower() in seen:
            continue
        seen.add(compact.lower())
        out.append(compact)
    return out


def search_duckduckgo_images(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Return raw DuckDuckGo image hits for a product query. Empty on any failure."""
    query = re.sub(r"\s+", " ", (query or "").strip())
    if not query:
        return []
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    html_headers = {**_BROWSER_HEADERS, "Accept": "text/html,application/xhtml+xml"}
    try:
        html = _http_get(
            "https://duckduckgo.com/?" + urlencode({"q": query, "iax": "images", "ia": "images"}),
            headers=html_headers,
            opener=opener,
        ).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, UnicodeError):
        return []
    vqd = _extract_vqd(html)
    if not vqd:
        return []
    json_headers = {**_BROWSER_HEADERS, "Referer": "https://duckduckgo.com/", "Accept": "application/json"}
    for locale in ("us-en", "wt-wt"):
        try:
            raw = _http_get(
                "https://duckduckgo.com/i.js?"
                + urlencode({"l": locale, "o": "json", "q": query, "vqd": vqd, "f": ",,,", "p": "1"}),
                headers=json_headers,
                opener=opener,
            )
            data = json.loads(raw.decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError):
            continue
        results = data.get("results") if isinstance(data, dict) else None
        if isinstance(results, list) and results:
            return [item for item in results if isinstance(item, dict)][: max(limit, 0)]
    return []


def search_bing_images(query: str, limit: int = 12) -> list[dict[str, Any]]:
    """Fallback image search when DuckDuckGo returns nothing. Brand-agnostic."""
    query = re.sub(r"\s+", " ", (query or "").strip())
    if not query:
        return []
    try:
        html = _http_get(
            "https://www.bing.com/images/async?"
            + urlencode({"q": query, "first": "0", "count": str(max(limit, 8)), "mmasync": "1"}),
            headers={**_BROWSER_HEADERS, "Accept": "text/html,application/xhtml+xml", "Referer": "https://www.bing.com/"},
        ).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, UnicodeError):
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for pattern in (
        r"murl&quot;(https://[^&<>\"]+)&quot;",
        r'"murl"\s*:\s*"(https://[^"]+)"',
        r"murl&quot;:?(https://[^&<>\"]+)",
        r"src=['\"](https://[^'\"]+\.(?:jpg|jpeg|png|webp)[^'\"]*)['\"]",
    ):
        for match in re.findall(pattern, html, flags=re.I):
            url = str(match).replace("\\u0026", "&").replace("\\/", "/")
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                break
        if len(urls) >= limit:
            break
    return [{"image": url, "title": query, "url": url} for url in urls]


def find_live_product_images(name: str, roaster: str, limit: int = MAX_IMAGE_CANDIDATES) -> list[str]:
    """Autonomous web image search scored for the scanned coffee. Never brand-locked."""
    tokens = _query_tokens(name, roaster)
    hits: list[dict[str, Any]] = []
    seen_raw: set[str] = set()
    for query in _search_queries(name, roaster):
        batch = search_duckduckgo_images(query, limit=18)
        if len(batch) < 4:
            batch = batch + search_bing_images(query, limit=12)
        for item in batch:
            key = _hit_image_url(item)
            if not key or key in seen_raw:
                continue
            seen_raw.add(key)
            hits.append(item)
        if len(hits) >= 12:
            break
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for item in hits:
        url = sanitize_image_url(_hit_image_url(item), resolve_dns=False)
        if not url or url in seen:
            continue
        seen.add(url)
        ranked.append((_score_hit(item, tokens), url))
    ranked.sort(key=lambda row: row[0], reverse=True)
    out: list[str] = []
    for score, url in ranked:
        if score < 2:
            continue
        if url in out:
            continue
        out.append(url)
        if len(out) >= limit:
            return out
    for _score, url in ranked:
        if url in out:
            continue
        out.append(url)
        if len(out) >= limit:
            break
    return out


def find_product_images(
    name: str,
    roaster: str,
    hint_urls: str | list[str] | None = None,
) -> list[str]:
    """Return up to 3 high-res product URLs from live web search plus model hints."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    roaster = re.sub(r"\s+", " ", (roaster or "").strip())
    hints = collect_image_urls(hint_urls, resolve_dns=False)
    live: list[str] = []
    if name or roaster:
        try:
            live = find_live_product_images(name, roaster)
        except Exception:
            live = []
    return collect_image_urls(live, hints, resolve_dns=False)[:MAX_IMAGE_CANDIDATES]
