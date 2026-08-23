#!/usr/bin/env python3
"""Verify autonomous high-res product image search for BeanNote."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ROASTER = "Copenhagen Roaster"
NAME = "Slow Roast Espresso"


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


def test_catalog_lookup() -> None:
    from image_search import (
        ROASTER_PACKSHOT_CATALOG,
        curated_packshot_url,
        curated_packshot_urls,
        find_product_images,
    )

    _assert_true("catalog has copenhagen", "copenhagen roaster" in ROASTER_PACKSHOT_CATALOG)
    _assert_true("catalog has bellarom", "bellarom" in ROASTER_PACKSHOT_CATALOG)
    _assert_true("catalog has dinluksus", "dinluksus" in ROASTER_PACKSHOT_CATALOG)
    _assert_true("catalog has dinluxus alias", "dinluxus" in ROASTER_PACKSHOT_CATALOG)

    found = curated_packshot_urls(NAME, ROASTER)
    _assert_true("copenhagen catalog 1-3", 1 <= len(found) <= 3)
    _assert_true("copenhagen https", all(item.startswith("https://") for item in found))
    _assert_true("copenhagen single", curated_packshot_url(NAME, ROASTER).startswith("https://"))

    bellarom = curated_packshot_urls("BIO Organic COFFEE BEANS", "Bellarom")
    _assert_eq("bellarom 3 studio urls", len(bellarom), 3)

    din = curated_packshot_urls("Espresso Crema", "Dinluksus")
    alias = curated_packshot_urls("Espresso Crema", "Dinluxus")
    _assert_true("dinluksus catalog", 1 <= len(din) <= 3)
    _assert_eq("dinluxus alias matches", din, alias)

    hint = "https://cdn.shopify.com/s/files/hint-bag.jpg"
    merged = find_product_images(NAME, ROASTER, hint)
    _assert_eq("vision hint is first", merged[0], hint)
    _assert_true("hint plus catalog", 2 <= len(merged) <= 3)
    print("OK  ROASTER_PACKSHOT_CATALOG matches Copenhagen/Bellarom/Dinluksus")


def test_find_product_images() -> None:
    from image_search import find_product_images

    candidates = find_product_images(NAME, ROASTER)
    _assert_true("merged 1-3", 1 <= len(candidates) <= 3)
    _assert_true("merged https", all(item.startswith("https://") for item in candidates))
    _assert_eq("payload key length", len(candidates), len(dict.fromkeys(candidates)))
    print("OK  find_product_images returned catalog candidates without scrapers")
    for url in candidates:
        print(f"     {url}")


def test_copenhagen_roaster_candidates() -> None:
    from image_search import find_product_images

    found = find_product_images("Slow Roast Espresso", "Copenhagen Roaster")
    _assert_true("copenhagen 1-3", 1 <= len(found) <= 3)
    _assert_true("copenhagen https", all(item.startswith("https://") for item in found))
    _assert_true("copenhagen salling or catalog host", any("sallinggroup" in url for url in found))
    print("OK  find_product_images('Slow Roast Espresso', 'Copenhagen Roaster')")
    for url in found:
        print(f"     {url}")


def main() -> int:
    try:
        test_url_guardrails()
        test_catalog_lookup()
        test_find_product_images()
        test_copenhagen_roaster_candidates()
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    print("OK  autonomous high-res image search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
