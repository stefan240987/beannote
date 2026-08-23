#!/usr/bin/env python3
"""Integration test: Gemini Vision extraction + i18n for the Copenhagen Roaster bag."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABEL_IMAGE = ROOT / "Screenshot 2026-08-23 at 11.07.28.jpg"
BELLAROM_IMAGE = ROOT / "IMG_9354.jpg"
BELLAROM_NAME = "Bio Organic Coffee Beans Full-Bodied Aroma"

EXPECTED_CORE = {
    "roaster": "Copenhagen Roaster",
    "name": "Slow Roast Espresso",
    "altitude": "800 - 2100 M.",
    "varietal": "Catuai & Heirloom",
}

EXPECTED_BY_LANG = {
    "da": {
        "origin": "Brasilien & Etiopien",
        "process": "Natural",
        "flavor_tags": ["Mørk chokolade", "Karamel", "Blåbær", "Citrus", "Hasselnød"],
        "story_needles": re.compile(r"[æøåÆØÅ]|kaffe|højde|bønn|smag|rist", re.I),
        "story_forbid": re.compile(r"\b(harvested|smallholders|cherries|farmers)\b"),
        "ratio_needles": ("kaffe til", "kaffe pr."),
    },
    "en": {
        "origin": "Brazil & Ethiopia",
        "process": "Natural",
        "flavor_tags": ["Dark chocolate", "Caramel", "Blueberry", "Citrus", "Hazelnut"],
        "story_needles": re.compile(
            r"\b(coffee|harvest|beans?|flavor|farmers?|region|altitudes?|cherries|blend)\b",
            re.I,
        ),
        "story_forbid": re.compile(r"[æøåÆØÅ]"),
        "ratio_needles": ("coffee to", "coffee per"),
    },
}


def _assert_eq(label: str, got, expected) -> None:
    if got != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {got!r}")


def _assert_true(label: str, ok: bool) -> None:
    if not ok:
        raise AssertionError(label)


def test_parse_gemini_json() -> None:
    from ocr import _parse_gemini_json

    payload = {"roaster": "Copenhagen Roaster", "bean_name": "Slow Roast Espresso"}
    body = json.dumps(payload)
    _assert_eq("plain JSON", _parse_gemini_json(body), payload)
    _assert_eq("fenced json", _parse_gemini_json(f"```json\n{body}\n```"), payload)
    _assert_eq("fenced bare", _parse_gemini_json(f"```\n{body}\n```"), payload)
    _assert_eq(
        "preamble fences",
        _parse_gemini_json(f"Here is the label:\n```json\n{body}\n```\n"),
        payload,
    )
    print("OK  markdown JSON fences are stripped before json.loads()")


def test_prompt_locks_name_and_lang() -> None:
    from ocr import _gemini_prompt

    da = _gemini_prompt("da")
    en = _gemini_prompt("en")
    for prompt in (da, en):
        _assert_true("prompt names Slow Roast Espresso", "Slow Roast Espresso" in prompt)
        _assert_true("prompt forbids Slow Roast Crema", "Never return \"Slow Roast Crema\"" in prompt)
    _assert_true("da flavors", "Mørk chokolade" in da)
    _assert_true("en flavors", "Dark chocolate" in en)
    _assert_true("da brew", "18g kaffe til 36g espresso" in da)
    _assert_true("en brew", "18g coffee to 36g espresso" in en)
    _assert_true("da suitable_for", "Mælkedrikke" in da)
    _assert_true("en suitable_for", "Milk drinks" in en)
    print("OK  Gemini prompt locks bean name and localizes flavor/brew copy")


def test_dynamic_tag_i18n() -> None:
    from ocr import flavor_i18n_table, is_public_image_url, localize_flavor, sanitize_image_url

    table = flavor_i18n_table()
    _assert_eq("da→en chocolate", table["Mørk chokolade"]["en"], "Dark chocolate")
    _assert_eq("en→da chocolate", table["Dark chocolate"]["da"], "Mørk chokolade")
    _assert_eq("title-case chocolate", table["Dark Chocolate"]["da"], "Mørk chokolade")
    _assert_eq("karamel en", localize_flavor("Karamel", "en"), "Caramel")
    _assert_eq("caramel da", localize_flavor("Caramel", "da"), "Karamel")
    _assert_eq("blueberry da", localize_flavor("Blueberry", "da"), "Blåbær")
    _assert_eq("blåbær en", localize_flavor("Blåbær", "en"), "Blueberry")
    _assert_true("reject http image", not is_public_image_url("http://cdn.shopify.com/bag.jpg"))
    _assert_true("reject localhost", not is_public_image_url("https://localhost/bag.jpg"))
    _assert_eq("empty sanitizer", sanitize_image_url("null"), "")
    print("OK  dynamic flavor i18n maps DA↔EN and rejects unsafe image URLs")


def test_refine_and_flavor_i18n() -> None:
    from ocr import extract_flavor_tags, refine_label_fields

    raw = {
        "bean_name": "Slow Roast Crema",
        "roaster": "Copenhagen Roaster",
        "origin": "Brasilien & Etiopien",
        "official_notes": "Noter af mørk chokolade, karamel, blåbær og citrus",
        "flavor_tags": ["Mørk chokolade", "Karamel", "Crema"],
        "process": "Natural",
        "story": "",
    }
    da = refine_label_fields(dict(raw), lang="da")
    en = refine_label_fields(dict(raw), lang="en")
    _assert_eq("da name", da.get("name"), "Slow Roast Espresso")
    _assert_eq("en name", en.get("name"), "Slow Roast Espresso")
    _assert_true("never Crema da", "Crema" not in (da.get("name") or ""))
    _assert_true("never Crema en", "Crema" not in (en.get("name") or ""))
    _assert_eq("da flavors", da.get("flavor_tags"), EXPECTED_BY_LANG["da"]["flavor_tags"])
    _assert_eq("en flavors", en.get("flavor_tags"), EXPECTED_BY_LANG["en"]["flavor_tags"])
    _assert_eq("da origin", da.get("origin"), "Brasilien & Etiopien")
    _assert_eq("en origin", en.get("origin"), "Brazil & Ethiopia")
    _assert_eq(
        "extract en",
        extract_flavor_tags(["Dark chocolate", "Caramel", "Blueberry"], lang="en"),
        ["Dark chocolate", "Caramel", "Blueberry"],
    )
    print("OK  refine_label_fields + flavor tags localize and rename Crema → Espresso")


def test_bellarom_suitability_and_packshot() -> None:
    from ocr import (
        BELLAROM_BIO_PACKSHOT,
        attach_official_bag_image,
        curated_packshot_url,
        extract_suitable_for,
        find_official_bag_image,
        find_official_bag_images,
        refine_label_fields,
    )

    raw = {
        "roaster": "Bellarom",
        "bean_name": "BIO Organic COFFEE BEANS FULL-BODIED AROMA",
        "official_notes": "FOR MACHINES FOR FILTER IDEAL FOR LATTE MACCHIATO",
        "suitable_for": ["Filter", "Espresso", "Mælkedrikke"],
    }
    da = refine_label_fields(dict(raw), lang="da")
    en = refine_label_fields(dict(raw), lang="en")
    _assert_eq("bellarom roaster", da.get("roaster"), "Bellarom")
    _assert_eq("bellarom name", da.get("name"), BELLAROM_NAME)
    _assert_eq("da suitable_for", da.get("suitable_for"), ["Filter", "Espresso", "Mælkedrikke"])
    _assert_eq("en suitable_for", en.get("suitable_for"), ["Filter", "Espresso", "Milk drinks"])
    extracted = extract_suitable_for(
        "FOR MACHINES", "FOR FILTER", "IDEAL FOR LATTE MACCHIATO", lang="da"
    )
    _assert_eq("icon suitable_for", extracted, ["Espresso", "Filter", "Mælkedrikke"])
    curated = curated_packshot_url(BELLAROM_NAME, "Bellarom")
    _assert_eq("curated studio packshot", curated, BELLAROM_BIO_PACKSHOT)
    url = find_official_bag_image(BELLAROM_NAME, "Bellarom")
    _assert_true("https packshot", str(url).startswith("https://"))
    _assert_true("not camera snapshot", not str(url).startswith("images/"))
    candidates = find_official_bag_images("Bellarom Bio Organic", "Bellarom")
    _assert_true("up to 3 candidates", 1 <= len(candidates) <= 3)
    _assert_true("all candidates https", all(item.startswith("https://") for item in candidates))
    attached = attach_official_bag_image({
        "name": BELLAROM_NAME,
        "roaster": "Bellarom",
    })
    attached_urls = attached.get("image_candidates") or []
    _assert_true("attached 1-3", 1 <= len(attached_urls) <= 3)
    _assert_true("attached https", all(item.startswith("https://") for item in attached_urls))
    _assert_eq("attached official first", attached_urls[0], BELLAROM_BIO_PACKSHOT)
    print("OK  Bellarom suitability tags and studio packshot fallback")


def _profile(parsed: dict) -> dict:
    brew = parsed.get("brew_recommendation") or {}
    return {
        "roaster": parsed.get("roaster"),
        "name": parsed.get("name") or parsed.get("bean_name"),
        "origin": parsed.get("origin"),
        "altitude": parsed.get("altitude"),
        "varietal": parsed.get("varietal"),
        "process": parsed.get("process"),
        "flavor_tags": parsed.get("flavor_tags") or parsed.get("flavor_notes") or [],
        "story": parsed.get("story") or "",
        "brew_ratio": parsed.get("brew_ratio") or brew.get("brew_ratio") or "",
    }


def test_label_extraction(lang: str) -> None:
    from ocr import encode_scan_jpeg, get_gemini_api_key, scan_label_gemini

    if not LABEL_IMAGE.is_file():
        raise FileNotFoundError(f"Missing label image: {LABEL_IMAGE.name}")
    if not get_gemini_api_key():
        raise RuntimeError("GEMINI_API_KEY missing — set it in .env")

    expect = EXPECTED_BY_LANG[lang]
    jpeg = encode_scan_jpeg(LABEL_IMAGE.read_bytes())
    parsed = scan_label_gemini(jpeg, lang=lang)
    if not parsed:
        raise RuntimeError(f"Gemini Vision returned no profile for lang={lang}")
    if parsed.get("scan_source") != "gemini":
        raise RuntimeError(f"Unexpected scan_source: {parsed.get('scan_source')!r}")

    profile = _profile(parsed)
    print(f"Gemini coffee profile lang={lang}")
    print(json.dumps({k: v for k, v in profile.items() if k != "story"}, ensure_ascii=False, indent=2))
    print("story:", profile["story"])

    for key, expected in EXPECTED_CORE.items():
        _assert_eq(f"{lang} {key}", profile[key], expected)
    _assert_true(f"{lang} not Crema", profile["name"] != "Slow Roast Crema")
    _assert_eq(f"{lang} origin", profile["origin"], expect["origin"])
    _assert_eq(f"{lang} process", profile["process"], expect["process"])
    _assert_eq(f"{lang} flavor_tags", profile["flavor_tags"], expect["flavor_tags"])

    story = profile["story"].strip()
    _assert_true(f"{lang} story present", bool(story))
    _assert_true(f"{lang} story language", bool(expect["story_needles"].search(story)))
    _assert_true(f"{lang} story not mixed", not expect["story_forbid"].search(story))

    ratio = (profile["brew_ratio"] or "").lower()
    _assert_true(
        f"{lang} brew_ratio localized ({profile['brew_ratio']!r})",
        any(needle in ratio for needle in expect["ratio_needles"]),
    )
    print(f"OK  lang={lang} Slow Roast Espresso extracted with localized tags and story")


def test_bellarom_label_extraction() -> None:
    from ocr import encode_scan_jpeg, find_official_bag_image, get_gemini_api_key, scan_label_gemini
    from ocr import BELLAROM_BIO_PACKSHOT

    if not BELLAROM_IMAGE.is_file():
        raise FileNotFoundError(f"Missing label image: {BELLAROM_IMAGE.name}")
    if not get_gemini_api_key():
        raise RuntimeError("GEMINI_API_KEY missing — set it in .env")

    jpeg = encode_scan_jpeg(BELLAROM_IMAGE.read_bytes())
    parsed = scan_label_gemini(jpeg, lang="da")
    if not parsed:
        raise RuntimeError("Gemini Vision returned no profile for Bellarom")
    _assert_eq("bellarom roaster", parsed.get("roaster"), "Bellarom")
    _assert_eq("bellarom name", parsed.get("name"), BELLAROM_NAME)
    _assert_eq(
        "bellarom suitable_for",
        parsed.get("suitable_for"),
        ["Filter", "Espresso", "Mælkedrikke"],
    )
    url = parsed.get("official_image_url") or find_official_bag_image(
        parsed.get("name") or "", parsed.get("roaster") or ""
    )
    if not url:
        url = find_official_bag_image(BELLAROM_NAME, "Bellarom")
    _assert_true("packshot https", str(url).startswith("https://"))
    _assert_true("packshot not snapshot", not str(url).startswith("images/"))
    _assert_true(
        "packshot studio",
        url == BELLAROM_BIO_PACKSHOT or "assets.schwarz" in str(url) or "cdn" in str(url),
    )
    print("OK  IMG_9354 Bellarom extracted with suitability tags and studio image fallback")


def main() -> int:
    try:
        test_parse_gemini_json()
        test_prompt_locks_name_and_lang()
        test_dynamic_tag_i18n()
        test_refine_and_flavor_i18n()
        test_bellarom_suitability_and_packshot()
        test_label_extraction("da")
        test_label_extraction("en")
        test_bellarom_label_extraction()
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
