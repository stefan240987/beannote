#!/usr/bin/env python3
"""Download catalog product photos into static/img/gear/{id}.jpg."""

from __future__ import annotations

import json
import ssl
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "gear_catalog.json"
DEST_DIR = ROOT / "static" / "img" / "gear"
PLACEHOLDER = DEST_DIR / "placeholder.svg"
MAX_EDGE = 800
CREAM = (250, 246, 240)
USER_AGENT = "BeanNote/1.0 (local gear image cache; https://beannote.app)"
SSL_CTX = ssl.create_default_context()

PLACEHOLDER_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 160" role="img" aria-label="Gear">
  <rect width="160" height="160" rx="28" fill="#f3ebe3"/>
  <rect x="38" y="86" width="84" height="10" rx="5" fill="#b85c38"/>
  <rect x="52" y="52" width="56" height="34" rx="8" fill="none" stroke="#b85c38" stroke-width="6"/>
  <circle cx="80" cy="69" r="8" fill="#3c2a21"/>
  <path d="M104 44c14 2 22 14 22 26" fill="none" stroke="#3c2a21" stroke-width="6" stroke-linecap="round"/>
</svg>
"""


def ensure_placeholder() -> None:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    if not PLACEHOLDER.exists() or PLACEHOLDER.stat().st_size < 40:
        PLACEHOLDER.write_text(PLACEHOLDER_SVG, encoding="utf-8")


def load_catalog() -> list[dict]:
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("gear_catalog.json must be a JSON array")
    return [item for item in raw if isinstance(item, dict)]


def source_url(item: dict) -> str:
    for key in ("source_url", "remote_url", "product_image_url"):
        url = str(item.get(key) or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return url
    url = str(item.get("image_url") or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def wikimedia_original(url: str) -> str:
    if "upload.wikimedia.org" not in url:
        return ""
    sized = url.replace("/640px-", "/500px-").replace("/800px-", "/500px-")
    if "/thumb/" not in url:
        return sized if sized != url else ""
    base, rest = url.split("/thumb/", 1)
    parts = rest.split("/")
    if len(parts) < 2:
        return sized
    original = f"{base}/{'/'.join(parts[:-1])}"
    return sized if sized != url else original


def fetch_json(url: str) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    req = Request(url, headers=headers)
    with urlopen(req, timeout=25, context=SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_bytes(url: str) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if "upload.wikimedia.org" in url or "wikimedia.org" in url:
        headers["Referer"] = "https://commons.wikimedia.org/"
    elif "breville.com" in url:
        headers["Referer"] = "https://www.breville.com/"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=12, context=SSL_CTX) as resp:
        data = resp.read()
        content_type = str(resp.headers.get("Content-Type") or "").lower()
    if len(data) < 512:
        raise ValueError("image too small")
    if "html" in content_type or data[:32].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise ValueError("html response")
    return data


def commons_image_url(query: str) -> str:
    params = urlencode({
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "8",
        "prop": "imageinfo",
        "iiprop": "url|mime|size",
        "iiurlwidth": "800",
        "origin": "*",
    })
    raw = fetch_json(f"https://commons.wikimedia.org/w/api.php?{params}")
    payload = raw
    pages = (payload.get("query") or {}).get("pages") or {}
    for page in pages.values():
        title = str(page.get("title") or "").lower()
        if any(skip in title for skip in ("logo", "icon", "flag", ".svg", "map")):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        mime = str(info.get("mime") or "")
        if not mime.startswith("image/") or mime.endswith("svg+xml"):
            continue
        url = str(info.get("thumburl") or info.get("url") or "")
        if url.startswith("http"):
            return url
    return ""


def candidate_urls(item: dict) -> list[str]:
    urls: list[str] = []
    primary = source_url(item)
    if primary:
        urls.append(primary)
        original = wikimedia_original(primary)
        if original:
            urls.append(original)
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def save_jpeg(raw: bytes, dest: Path) -> None:
    img = Image.open(BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, CREAM)
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.jpg")
    img.save(tmp, "JPEG", quality=86, optimize=True)
    tmp.replace(dest)


def download_item(item: dict, dest: Path) -> None:
    errors: list[str] = []
    for url in candidate_urls(item):
        try:
            save_jpeg(fetch_bytes(url), dest)
            return
        except (HTTPError, URLError, OSError, ValueError) as exc:
            errors.append(f"{url} ({exc})")
            if "429" in str(exc):
                time.sleep(2)
    query = " ".join(part for part in (item.get("brand"), item.get("name")) if part).strip()
    if query:
        time.sleep(0.35)
        found = commons_image_url(query)
        if found:
            save_jpeg(fetch_bytes(found), dest)
            return
    raise ValueError("; ".join(errors[:2]) or "no source")


def main() -> int:
    ensure_placeholder()
    catalog = load_catalog()
    saved = skipped = failed = 0
    for item in catalog:
        gid = str(item.get("id") or "").strip()
        if not gid or "/" in gid or ".." in gid:
            print(f"skip invalid id: {gid!r}")
            failed += 1
            continue
        dest = DEST_DIR / f"{gid}.jpg"
        if dest.exists() and dest.stat().st_size > 512:
            skipped += 1
            continue
        try:
            download_item(item, dest)
            print(f"saved {dest.relative_to(ROOT)}")
            saved += 1
        except (HTTPError, URLError, OSError, ValueError) as exc:
            print(f"fail {gid}: {exc}")
            failed += 1
            if dest.exists() and dest.stat().st_size < 512:
                dest.unlink(missing_ok=True)
    print(f"done saved={saved} skipped={skipped} failed={failed} placeholder={PLACEHOLDER.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
