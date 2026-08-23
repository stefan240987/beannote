#!/usr/bin/env python3
"""Verify autonomous high-res product image search for BeanNote."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUERY = "Bellarom Bio Organic Coffee Beans Full-Bodied Aroma"
ROASTER = "Bellarom"
NAME = "Bio Organic Coffee Beans Full-Bodied Aroma"


def _assert_true(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)


def _assert_eq(label: str, got, expected) -> None:
    if got != expected:
        raise AssertionError(f"{label}: {got!r} != {expected!r}")


def test_url_guardrails() -> None:
    from image_search import collect_image_urls, is_public_image_url, sanitize_image_url

    _assert_true("reject http", not is_public_image_url("http://cdn.shopify.com/bag.jpg"))
    _assert_true("reject localhost", not is_public_image_url("https://localhost/bag.jpg"))
    _assert_eq("empty sanitizer", sanitize_image_url("null"), "")
    _assert_true(
        "accept jpg",
        bool(sanitize_image_url("https://cdn.shopify.com/bag.jpg", resolve_dns=False)),
    )
    urls = collect_image_urls(
        ["https://cdn.shopify.com/a.jpg", "https://cdn.shopify.com/a.jpg", "not-a-url"],
        {"image": "https://images.prom.ua/bag.png"},
        resolve_dns=False,
    )
    _assert_eq("deduped collect", len(urls), 2)
    print("OK  URL guardrails reject private/http and de-dupe candidates")


def test_curated_bellarom() -> None:
    from image_search import BELLAROM_BIO_PACKSHOT, curated_packshot_urls

    found = curated_packshot_urls(NAME, ROASTER)
    _assert_true("curated 1-3", 1 <= len(found) <= 3)
    _assert_eq("bio packshot first", found[0], BELLAROM_BIO_PACKSHOT)
    _assert_true("all https", all(item.startswith("https://") for item in found))
    print("OK  curated Bellarom studio packshots available as backfill")


def test_duckduckgo_bellarom() -> None:
    from image_search import search_duckduckgo_images, sanitize_image_url

    hits = search_duckduckgo_images(QUERY)
    if not hits:
        hits = search_duckduckgo_images(f"{QUERY} coffee bag")
    _assert_true("DDG returned hits", bool(hits))
    images = []
    for item in hits:
        url = sanitize_image_url(str(item.get("image") or ""), resolve_dns=False)
        if url:
            images.append(url)
    _assert_true("DDG has https images", len(images) >= 3)
    blob = " ".join(
        f"{item.get('title') or ''} {item.get('image') or ''}".lower() for item in hits[:12]
    )
    _assert_true("DDG matches Bellarom", "bellarom" in blob)
    print(f"OK  DuckDuckGo Images returned {len(images)} Bellarom product URLs")
    for url in images[:3]:
        print(f"     {url}")


def test_find_product_images() -> None:
    from image_search import find_live_product_images, find_product_images

    live = find_live_product_images(NAME, ROASTER)
    _assert_true("live 1-3", 1 <= len(live) <= 3)
    _assert_true("live https", all(item.startswith("https://") for item in live))
    candidates = find_product_images(NAME, ROASTER)
    _assert_true("merged 1-3", 1 <= len(candidates) <= 3)
    _assert_true("merged https", all(item.startswith("https://") for item in candidates))
    _assert_eq("payload key length", len(candidates), len(dict.fromkeys(candidates)))
    print("OK  find_product_images returned up to 3 high-res candidates")
    for url in candidates:
        print(f"     {url}")


def main() -> int:
    try:
        test_url_guardrails()
        test_curated_bellarom()
        test_duckduckgo_bellarom()
        test_find_product_images()
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    print("OK  autonomous high-res image search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
