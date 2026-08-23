#!/usr/bin/env python3
"""Integration test: Gemini Vision extraction of the Copenhagen Roaster Crema label."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABEL_IMAGE = ROOT / "Screenshot 2026-08-23 at 11.07.28.jpg"

EXPECTED = {
    "roaster": "Copenhagen Roaster",
    "name": "Slow Roast Crema",
    "origin": "Brasilien & Etiopien",
    "altitude": "800 - 2100 M.",
    "varietal": "Catuai & Heirloom",
    "process": "Natural",
    "flavor_tags": ["Mørk chokolade", "Karamel", "Blåbær", "Citrus"],
}


def _assert_eq(label: str, got, expected) -> None:
    if got != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {got!r}")


def test_parse_gemini_json() -> None:
    from ocr import _parse_gemini_json

    payload = {"roaster": "Copenhagen Roaster", "bean_name": "Slow Roast Crema"}
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


def test_label_extraction() -> None:
    from ocr import encode_scan_jpeg, get_gemini_api_key, scan_label_gemini

    if not LABEL_IMAGE.is_file():
        raise FileNotFoundError(f"Missing label image: {LABEL_IMAGE.name}")
    if not get_gemini_api_key():
        raise RuntimeError("GEMINI_API_KEY missing — set it in .env")

    jpeg = encode_scan_jpeg(LABEL_IMAGE.read_bytes())
    parsed = scan_label_gemini(jpeg, lang="da")
    if not parsed:
        raise RuntimeError("Gemini Vision returned no profile")
    if parsed.get("scan_source") != "gemini":
        raise RuntimeError(f"Unexpected scan_source: {parsed.get('scan_source')!r}")

    profile = {
        "roaster": parsed.get("roaster"),
        "name": parsed.get("name") or parsed.get("bean_name"),
        "origin": parsed.get("origin"),
        "altitude": parsed.get("altitude"),
        "varietal": parsed.get("varietal"),
        "process": parsed.get("process"),
        "flavor_tags": parsed.get("flavor_tags") or parsed.get("flavor_notes") or [],
    }
    print("Gemini coffee profile")
    print(json.dumps(profile, ensure_ascii=False, indent=2))

    for key, expected in EXPECTED.items():
        _assert_eq(key, profile[key], expected)
    print("OK  Copenhagen Roaster Slow Roast Crema extracted at 100%")


def main() -> int:
    try:
        test_parse_gemini_json()
        test_label_extraction()
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
