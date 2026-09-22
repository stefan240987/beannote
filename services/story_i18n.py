"""Fill missing DA/EN coffee stories without scraping or overwriting unique copy."""

from __future__ import annotations

import os
import threading
import time
import traceback
from typing import Any

from db import (
    ENVIRONMENT,
    apply_story_translation_result,
    list_beans_needing_story_translation,
    replace_bean_story_map,
    story_translation_plan,
)
from translations import normalize_lang

_BACKFILL_SLOT = -9001
_started = False
_start_lock = threading.Lock()
_failed_ids: set[int] = set()


def _truthy(name: str, default: str = "") -> bool:
    return (os.getenv(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def story_backfill_enabled() -> bool:
    if _truthy("STORY_BACKFILL", "0"):
        return True
    if (os.getenv("STORY_BACKFILL") or "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return ENVIRONMENT == "production"


def translate_story_text(text: str, source_lang: str, target_lang: str) -> str:
    source = str(text or "").strip()
    src = normalize_lang(source_lang)
    dst = normalize_lang(target_lang)
    if not source or src == dst:
        return source
    from ocr import STORY_LANG, _gemini_generate_json, get_gemini_api_key
    from services.gemini import clean_story_text

    key = get_gemini_api_key()
    if not key:
        return ""
    src_name = STORY_LANG.get(src, src)
    dst_name = STORY_LANG.get(dst, dst)
    prompt = (
        f"Translate this coffee-bean story from {src_name} to {dst_name}. "
        "Keep the same facts, length, and tone. Do not add farms, flavors, places, "
        "or brewing advice that are not in the source. Return JSON only.\n"
        '{"text": ""}\n\n'
        f"SOURCE:\n{source}"
    )
    try:
        data = _gemini_generate_json(key, prompt, 12_000)
    except Exception as exc:
        print(f"story translate failed: {type(exc).__name__}: {exc}")
        return ""
    if not isinstance(data, dict):
        return ""
    translated = clean_story_text(data.get("text") or data.get("story") or "")
    if not translated or translated.strip().lower() == source.lower():
        return ""
    return translated


def complete_story_map(story: Any, lang: str | None = None) -> dict[str, str]:
    from db import coerce_story_map

    current = coerce_story_map(story, lang)
    plan = story_translation_plan(current or story)
    if not plan:
        return current
    translated = translate_story_text(
        plan["source_text"],
        plan["source_lang"],
        plan["target_lang"],
    )
    if not translated:
        return current
    return apply_story_translation_result(current, plan, translated)


def backfill_one_story() -> bool:
    from ocr import get_gemini_api_key
    from jobs import acquire_gemini_slot, release_gemini_slot

    if not get_gemini_api_key():
        return False
    rows = [
        row
        for row in list_beans_needing_story_translation(24)
        if int(row["id"]) not in _failed_ids
    ]
    if not rows:
        return False
    row = rows[0]
    plan = row.get("plan") or story_translation_plan(row.get("story"))
    if not plan:
        _failed_ids.add(int(row["id"]))
        return True
    if not acquire_gemini_slot(_BACKFILL_SLOT, timeout_sec=8.0):
        return True
    try:
        translated = translate_story_text(
            plan["source_text"],
            plan["source_lang"],
            plan["target_lang"],
        )
        if not translated:
            _failed_ids.add(int(row["id"]))
            return True
        next_map = apply_story_translation_result(row.get("story"), plan, translated)
        if next_map:
            replace_bean_story_map(int(row["id"]), next_map)
            print(
                f"story backfill bean {row['id']} "
                f"{plan['source_lang']}->{plan['target_lang']}"
            )
        else:
            _failed_ids.add(int(row["id"]))
    except Exception as exc:
        _failed_ids.add(int(row["id"]))
        print(f"story backfill bean {row.get('id')}: {exc}")
        traceback.print_exc()
    finally:
        release_gemini_slot(_BACKFILL_SLOT)
    return True


def run_story_backfill_loop() -> None:
    idle = 45.0
    busy = 1.6
    while True:
        try:
            worked = backfill_one_story()
            time.sleep(busy if worked else idle)
        except Exception as exc:
            print(f"story backfill loop: {exc}")
            traceback.print_exc()
            time.sleep(8.0)


def start_story_backfill() -> None:
    """Daemon that fills missing story languages after Unraid boot. No-op locally."""
    global _started
    if not story_backfill_enabled():
        return
    try:
        from ocr import get_gemini_api_key

        if not get_gemini_api_key():
            return
    except Exception:
        return
    with _start_lock:
        if _started:
            return
        _started = True
    thread = threading.Thread(
        target=run_story_backfill_loop,
        name="beannote-story-i18n",
        daemon=True,
    )
    thread.start()
    print("story backfill started")


__all__ = [
    "backfill_one_story",
    "complete_story_map",
    "start_story_backfill",
    "story_backfill_enabled",
    "translate_story_text",
]
