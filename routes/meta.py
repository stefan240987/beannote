"""Meta routes: health, config, i18n packs."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request

from db import ENVIRONMENT, VERSION, distinct_values, get_db_path, is_local_dev, should_auto_flush
from deps import BREW_METHODS, UI_LANGS, _oauth_configured, optional_user, support_config
from jobs import queue_stats
from ocr import (
    flavor_i18n_table,
    flavor_notes_for,
    processes_for,
    roast_levels_for,
    scan_available,
    suitable_for_catalog,
    suitable_for_i18n_table,
)
from translations import FALLBACK_LANG, STRINGS, SUPPORTED_LANGUAGES, brew_method_i18n_table, normalize_lang

router = APIRouter(tags=["meta"])


@router.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": VERSION,
        "environment": ENVIRONMENT,
        "auto_flush": should_auto_flush(),
        "scan": scan_available(),
        "db": str(get_db_path()),
        "jobs": queue_stats(),
    }


@router.get("/api/config")
def config(request: Request, user: Optional[dict[str, Any]] = Depends(optional_user)) -> dict[str, Any]:
    lang = normalize_lang(request.query_params.get("lang"))
    local = is_local_dev()
    return {
        "version": VERSION,
        "lang": lang,
        "langs": UI_LANGS,
        "supported_languages": list(SUPPORTED_LANGUAGES),
        "fallback_lang": FALLBACK_LANG,
        "strings": STRINGS.get(lang) or STRINGS[FALLBACK_LANG],
        "i18n": {code: STRINGS[code] for code in SUPPORTED_LANGUAGES if code in STRINGS},
        "user": user,
        "environment": ENVIRONMENT,
        "local_dev": local,
        "providers": {
            "google": local or _oauth_configured("google"),
            "apple": local or _oauth_configured("apple"),
        },
        "brew_methods": BREW_METHODS,
        "processes": processes_for(lang),
        "roast_levels": roast_levels_for(lang),
        "flavor_notes": flavor_notes_for(lang),
        "flavor_i18n": flavor_i18n_table(),
        "suitable_for": suitable_for_catalog(lang),
        "suitable_i18n": suitable_for_i18n_table(),
        "brew_method_i18n": brew_method_i18n_table(),
        "origins": distinct_values("origin"),
        "roasts": distinct_values("roast_level"),
        **support_config(),
    }


@router.get("/api/i18n")
def i18n_pack() -> dict[str, dict[str, str]]:
    return {code: STRINGS[code] for code in SUPPORTED_LANGUAGES if code in STRINGS}


@router.get("/api/i18n/{lang}")
def i18n(lang: str) -> dict[str, str]:
    code = normalize_lang(lang)
    return STRINGS.get(code) or STRINGS[FALLBACK_LANG]
