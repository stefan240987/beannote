#!/usr/bin/env python3
"""Integration test: Gemini Vision extraction + i18n (brand-agnostic)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABEL_IMAGE = ROOT / "Screenshot 2026-08-23 at 11.07.28.jpg"
BELLAROM_IMAGE = ROOT / "IMG_9354.jpg"

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
        _assert_true("prompt is brand-agnostic", "Bellarom" not in prompt)
        _assert_true("prompt has no Copenhagen lock", "Copenhagen Roaster" not in prompt)
        _assert_true("prompt has no Slow Roast SKU lock", "Slow Roast Espresso" not in prompt)
        _assert_true("prompt has no Schwarz CDN", "Schwarz" not in prompt and "Lidl" not in prompt)
        _assert_true("prompt asks for story map", '"story": language map' in prompt)
        _assert_true("prompt asks for flavor map", '"flavor_tags": language map' in prompt)
        _assert_true("prompt asks for brew map", '"brew_recommendation": language map' in prompt)
        _assert_true("prompt asks for roaster_url", '"roaster_url"' in prompt)
        _assert_true("prompt asks for acidity_score", '"acidity_score"' in prompt)
        _assert_true("prompt asks for body_score", '"body_score"' in prompt)
        _assert_true("prompt asks for roast_level_score", '"roast_level_score"' in prompt)
        _assert_true("prompt includes da key", '"da"' in prompt)
        _assert_true("prompt includes en key", '"en"' in prompt)
        _assert_true("da flavors in map prompt", "Mørk chokolade" in prompt)
        _assert_true("en flavors in map prompt", "Dark chocolate" in prompt)
        _assert_true("da brew in map prompt", "18g kaffe til 36g espresso" in prompt)
        _assert_true("en brew in map prompt", "18g coffee to 36g espresso" in prompt)
    _assert_true("da suitable_for", "Mælkedrikke" in da)
    _assert_true("en suitable_for", "Milk drinks" in en)
    print("OK  Gemini prompt is brand-agnostic and asks for JSON language maps")


def test_get_localized_fallback() -> None:
    from db import get_localized

    story = {"da": "Høstet i Yirgacheffe.", "en": "Harvested in Yirgacheffe."}
    _assert_eq("da story", get_localized(story, "da"), "Høstet i Yirgacheffe.")
    _assert_eq("en story", get_localized(story, "en"), "Harvested in Yirgacheffe.")
    _assert_eq("missing de falls back to en", get_localized({"en": "Hello"}, "de"), "Hello")
    _assert_eq("legacy string", get_localized("Plain story", "da"), "Plain story")
    tags = {"da": ["Karamel"], "en": ["Caramel"]}
    _assert_eq("da tags", get_localized(tags, "da"), ["Karamel"])
    _assert_eq("en tags", get_localized(tags, "en"), ["Caramel"])
    brew = {
        "da": {"brew_ratio": "18g kaffe til 36g espresso"},
        "en": {"brew_ratio": "18g coffee to 36g espresso"},
    }
    _assert_eq("en brew ratio", get_localized(brew, "en")["brew_ratio"], "18g coffee to 36g espresso")
    print("OK  get_localized falls back across JSON language maps")


def test_normalize_builds_language_maps() -> None:
    from db import get_localized
    from ocr import normalize_scan_fields

    raw = {
        "bean_name": "Slow Roast Espresso",
        "roaster": "Copenhagen Roaster",
        "origin": "Brasilien & Etiopien",
        "process": "Natural",
        "official_notes": "Noter af mørk chokolade, karamel, blåbær og citrus",
        "flavor_tags": {"da": ["Mørk chokolade", "Karamel"], "en": ["Dark chocolate", "Caramel"]},
        "story": {
            "da": "Høstet i 1.900 meters højde i Yirgacheffe-regionen.",
            "en": "Harvested at 1,900 meters in the Yirgacheffe region.",
        },
        "brew_recommendation": {
            "da": {
                "recommended_method": "Espresso",
                "grind_size": "Fin",
                "water_temp": "92-94°C",
                "brew_ratio": "18g kaffe til 36g espresso",
            },
            "en": {
                "recommended_method": "Espresso",
                "grind_size": "Fine",
                "water_temp": "92-94°C",
                "brew_ratio": "18g coffee to 36g espresso",
            },
        },
    }
    out = normalize_scan_fields(dict(raw), lang="da")
    _assert_true("story is map", isinstance(out.get("story"), dict))
    _assert_true("flavor_tags is map", isinstance(out.get("flavor_tags"), dict))
    _assert_true("brew is map", isinstance(out.get("brew_recommendation"), dict))
    _assert_true("story has da", bool(out["story"].get("da")))
    _assert_true("story has en", bool(out["story"].get("en")))
    _assert_true("da chocolate", "Mørk chokolade" in (get_localized(out["flavor_tags"], "da") or []))
    _assert_true("da caramel", "Karamel" in (get_localized(out["flavor_tags"], "da") or []))
    _assert_true("en chocolate", "Dark chocolate" in (get_localized(out["flavor_tags"], "en") or []))
    _assert_true("en caramel", "Caramel" in (get_localized(out["flavor_tags"], "en") or []))
    wrapped = normalize_scan_fields(
        {
            "bean_name": "Test Bean",
            "roaster": "Test Roaster",
            "story": "Harvested by smallholders on the hillside.",
            "flavor_tags": ["Caramel", "Citrus"],
        },
        lang="en",
    )
    _assert_eq("string story wraps under en", wrapped["story"].get("en"), "Harvested by smallholders on the hillside.")
    _assert_true("wrapped flavors da", "Karamel" in (wrapped["flavor_tags"].get("da") or []))
    _assert_true("wrapped flavors en", "Caramel" in (wrapped["flavor_tags"].get("en") or []))
    with_url = normalize_scan_fields(
        {
            "bean_name": "Kenya AA",
            "roaster": "La Cabra",
            "roaster_url": "Shop at https://lacabra.dk/",
        },
        lang="da",
    )
    _assert_eq("scan keeps sanitized roaster_url", with_url.get("roaster_url"), "https://lacabra.dk")
    print("OK  normalize_scan_fields stores story/flavor_tags/brew as language maps")


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


def test_roaster_url_and_retailer_i18n() -> None:
    from db import sanitize_roaster_url
    from ocr import parse_label
    from translations import t

    _assert_eq("https ok", sanitize_roaster_url("https://coffeecollective.dk"), "https://coffeecollective.dk")
    _assert_eq("www upgrade", sanitize_roaster_url("www.thebarn.de/coffee"), "https://www.thebarn.de/coffee")
    _assert_eq("extract from sentence", sanitize_roaster_url("Besøg os: https://aprilcoffee.dk/"), "https://aprilcoffee.dk")
    _assert_eq("reject javascript", sanitize_roaster_url("javascript:alert(1)"), "")
    _assert_eq("reject localhost", sanitize_roaster_url("https://localhost/shop"), "")
    _assert_eq("reject image url", sanitize_roaster_url("https://cdn.shopify.com/bag.jpg"), "")
    parsed = parse_label("COFFEE COLLECTIVE\nKenya AA\nwww.coffeecollective.dk")
    _assert_eq("tesseract website", parsed.get("roaster_url"), "https://www.coffeecollective.dk")
    _assert_eq("da find", t("da", "find_retailer"), "🛍️ Find forhandler")
    _assert_eq("en find", t("en", "find_retailer"), "🛍️ Find Retailer")
    _assert_eq("da visit", t("da", "visit_roaster"), "🌐 Besøg risteri")
    _assert_eq("en visit", t("en", "visit_roaster"), "🌐 Visit Roaster")
    print("OK  roaster URL sanitizer + retailer i18n")


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
    _assert_eq("da name kept as printed", da.get("name"), "Slow Roast Crema")
    _assert_eq("en name kept as printed", en.get("name"), "Slow Roast Crema")
    _assert_eq("da origin", da.get("origin"), "Brasilien & Etiopien")
    _assert_eq("en origin", en.get("origin"), "Brazil & Ethiopia")
    _assert_eq(
        "extract en",
        extract_flavor_tags(["Dark chocolate", "Caramel", "Blueberry"], lang="en"),
        ["Dark chocolate", "Caramel", "Blueberry"],
    )
    print("OK  refine_label_fields localizes origin without rewriting brand SKUs")


def test_bellarom_suitability_and_packshot() -> None:
    from image_search import curated_packshot_url
    from ocr import attach_official_bag_image, extract_suitable_for, refine_label_fields

    raw = {
        "roaster": "Bellarom",
        "bean_name": "BIO Organic COFFEE BEANS FULL-BODIED AROMA",
        "official_notes": "FOR MACHINES FOR FILTER IDEAL FOR LATTE MACCHIATO",
        "suitable_for": ["Filter", "Espresso", "Mælkedrikke"],
    }
    da = refine_label_fields(dict(raw), lang="da")
    en = refine_label_fields(dict(raw), lang="en")
    _assert_eq("printed roaster kept", da.get("roaster"), "Bellarom")
    _assert_eq("printed name kept", da.get("name"), "BIO Organic COFFEE BEANS FULL-BODIED AROMA")
    _assert_eq("da suitable_for", da.get("suitable_for"), ["Espresso", "Filter", "Mælkedrikke"])
    _assert_eq("en suitable_for", en.get("suitable_for"), ["Espresso", "Filter", "Milk drinks"])
    extracted = extract_suitable_for(
        "FOR MACHINES", "FOR FILTER", "IDEAL FOR LATTE MACCHIATO", lang="da"
    )
    _assert_eq("icon suitable_for", extracted, ["Espresso", "Filter", "Mælkedrikke"])
    _assert_true("catalog packshot", curated_packshot_url(raw["bean_name"], "Bellarom").startswith("https://"))
    attached = attach_official_bag_image({
        "name": raw["bean_name"],
        "roaster": "Bellarom",
        "product_image_urls": [],
    })
    attached_urls = attached.get("image_candidates") or []
    _assert_true("1-3 catalog candidates", 1 <= len(attached_urls) <= 3)
    _assert_true(
        "candidates https when present",
        all(str(item).startswith("https://") for item in attached_urls),
    )
    print("OK  generic suitability extraction with catalog studio packshots")


def _profile(parsed: dict, lang: str = "da") -> dict:
    from db import get_localized

    brew = get_localized(parsed.get("brew_recommendation") or {}, lang) or {}
    if not isinstance(brew, dict):
        brew = {}
    story = get_localized(parsed.get("story") or "", lang) or ""
    tags = get_localized(parsed.get("flavor_tags") or parsed.get("flavor_notes") or [], lang) or []
    return {
        "roaster": parsed.get("roaster"),
        "name": parsed.get("name") or parsed.get("bean_name"),
        "origin": parsed.get("origin"),
        "altitude": parsed.get("altitude"),
        "varietal": parsed.get("varietal"),
        "process": parsed.get("process"),
        "flavor_tags": tags if isinstance(tags, list) else [],
        "story": story if isinstance(story, str) else "",
        "brew_ratio": parsed.get("brew_ratio") or brew.get("brew_ratio") or "",
        "story_map": parsed.get("story") if isinstance(parsed.get("story"), dict) else {},
        "flavor_map": parsed.get("flavor_tags") if isinstance(parsed.get("flavor_tags"), dict) else {},
        "brew_map": parsed.get("brew_recommendation") if isinstance(parsed.get("brew_recommendation"), dict) else {},
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

    profile = _profile(parsed, lang)
    print(f"Gemini coffee profile lang={lang}")
    print(json.dumps({k: v for k, v in profile.items() if k not in {"story", "story_map", "flavor_map", "brew_map"}}, ensure_ascii=False, indent=2))
    print("story:", profile["story"])
    print("story_map keys:", sorted(profile["story_map"]))
    print("flavor_map keys:", sorted(profile["flavor_map"]))

    _assert_true(f"{lang} name present", bool(str(profile["name"] or "").strip()))
    _assert_true(f"{lang} roaster present", bool(str(profile["roaster"] or "").strip()))
    _assert_eq(f"{lang} origin", profile["origin"], expect["origin"])
    _assert_eq(f"{lang} process", profile["process"], expect["process"])
    _assert_true(f"{lang} flavor tags", isinstance(profile["flavor_tags"], list) and bool(profile["flavor_tags"]))
    _assert_true(f"{lang} story map da", bool(str(profile["story_map"].get("da") or "").strip()))
    _assert_true(f"{lang} story map en", bool(str(profile["story_map"].get("en") or "").strip()))
    _assert_true(f"{lang} flavor map da", bool(profile["flavor_map"].get("da")))
    _assert_true(f"{lang} flavor map en", bool(profile["flavor_map"].get("en")))
    _assert_true(f"{lang} brew map da", bool(profile["brew_map"].get("da")))
    _assert_true(f"{lang} brew map en", bool(profile["brew_map"].get("en")))

    story = profile["story"].strip()
    _assert_true(f"{lang} story present", bool(story))
    _assert_true(f"{lang} story language", bool(expect["story_needles"].search(story)))
    _assert_true(f"{lang} story not mixed", not expect["story_forbid"].search(story))

    ratio = (profile["brew_ratio"] or "").lower()
    _assert_true(
        f"{lang} brew_ratio localized ({profile['brew_ratio']!r})",
        any(needle in ratio for needle in expect["ratio_needles"]),
    )
    print(f"OK  lang={lang} label extracted with localized tags and story")


def test_bellarom_label_extraction() -> None:
    from ocr import encode_scan_jpeg, get_gemini_api_key, scan_label_gemini

    if not BELLAROM_IMAGE.is_file():
        raise FileNotFoundError(f"Missing label image: {BELLAROM_IMAGE.name}")
    if not get_gemini_api_key():
        raise RuntimeError("GEMINI_API_KEY missing — set it in .env")

    jpeg = encode_scan_jpeg(BELLAROM_IMAGE.read_bytes())
    parsed = scan_label_gemini(jpeg, lang="da")
    if not parsed:
        raise RuntimeError("Gemini Vision returned no profile for fixture bag")
    _assert_true("roaster present", bool(str(parsed.get("roaster") or "").strip()))
    _assert_true("name present", bool(str(parsed.get("name") or parsed.get("bean_name") or "").strip()))
    suitable = parsed.get("suitable_for") or []
    _assert_true("suitable_for from icons", isinstance(suitable, list) and len(suitable) >= 1)
    url = parsed.get("official_image_url") or ""
    if url:
        _assert_true("packshot https", str(url).startswith("https://"))
        _assert_true("packshot not snapshot", not str(url).startswith("images/"))
    print("OK  fixture bag extracted with suitability tags and no curated CDN lock")


def test_intensity_scores_and_support() -> None:
    from db import infer_intensity_scores, roast_level_to_score
    from main import LOCAL_SUPPORT_BUYMEACOFFEE, LOCAL_SUPPORT_MOBILEPAY, support_config
    from ocr import normalize_scan_fields
    from translations import brew_method_label, t

    _assert_eq("lys roast score", roast_level_to_score("Lys"), 1)
    _assert_eq("medium roast score", roast_level_to_score("Medium"), 3)
    _assert_eq("dark roast score", roast_level_to_score("Mørk"), 5)
    light = infer_intensity_scores(roast_level="Lys", origin="Ethiopia", process="Vasket")
    _assert_eq("light african acidity", light["acidity_score"], 5)
    _assert_eq("light roast score", light["roast_level_score"], 1)
    dark = infer_intensity_scores(roast_level="Mørk", name="Espresso Blend")
    _assert_eq("dark body", dark["body_score"], 5)
    _assert_eq("clamped high", infer_intensity_scores(acidity_score=9)["acidity_score"], 5)
    _assert_eq("clamped low", infer_intensity_scores(body_score=0)["body_score"], 1)
    scanned = normalize_scan_fields(
        {
            "bean_name": "Kenya AA",
            "roaster": "La Cabra",
            "origin": "Kenya",
            "process": "Washed",
            "roast_level": "Lys",
            "acidity_score": 5,
            "body_score": 2,
            "roast_level_score": 1,
        },
        lang="da",
    )
    _assert_eq("scan acidity_score", scanned.get("acidity_score"), 5)
    _assert_eq("scan body_score", scanned.get("body_score"), 2)
    _assert_eq("scan roast_level_score", scanned.get("roast_level_score"), 1)
    _assert_eq("da acidity bar", t("da", "intensity_acidity"), "🍋 Syre")
    _assert_eq("en acidity bar", t("en", "intensity_acidity"), "🍋 Acidity")
    _assert_eq("da support", t("da", "support_app"), "☕ Støt appen")
    _assert_eq("en french press", brew_method_label("French Press", "en"), "French Press")
    _assert_eq("da french press", brew_method_label("French Press", "da"), "Stempelkande")
    cfg = support_config()
    _assert_true("support keys", "support_enabled" in cfg and "mobilepay_url" in cfg and "buymeacoffee_url" in cfg)
    if cfg.get("support_test_mode"):
        _assert_eq("local mobilepay", cfg["mobilepay_url"], LOCAL_SUPPORT_MOBILEPAY)
        _assert_eq("local buymeacoffee", cfg["buymeacoffee_url"], LOCAL_SUPPORT_BUYMEACOFFEE)
        _assert_true("local support on", cfg["support_enabled"])
    print("OK  intensity scores, recipe i18n, and local support fallbacks")


def main() -> int:
    try:
        test_parse_gemini_json()
        test_prompt_locks_name_and_lang()
        test_get_localized_fallback()
        test_normalize_builds_language_maps()
        test_dynamic_tag_i18n()
        test_roaster_url_and_retailer_i18n()
        test_refine_and_flavor_i18n()
        test_bellarom_suitability_and_packshot()
        test_intensity_scores_and_support()
        test_label_extraction("da")
        test_label_extraction("en")
        test_bellarom_label_extraction()
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
