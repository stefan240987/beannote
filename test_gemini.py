#!/usr/bin/env python3
"""Integration test: Gemini Vision extraction + i18n for the Copenhagen Roaster bag."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABEL_IMAGE = ROOT / "Screenshot 2026-08-23 at 11.07.28.jpg"

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
            r"\b(coffee|harvest|beans?|flavor|farmers?|region|altitude|cherries)\b",
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
    print("OK  Gemini prompt locks bean name and localizes flavor/brew copy")


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


def main() -> int:
    try:
        test_parse_gemini_json()
        test_prompt_locks_name_and_lang()
        test_refine_and_flavor_i18n()
        test_label_extraction("da")
        test_label_extraction("en")
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
