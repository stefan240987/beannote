"""Coffee bag scanner: optical Gemini Vision, grounded official-page lookup, Tesseract fallback."""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from db import (
    clamp_intensity_score,
    classify_matches,
    find_similar_beans,
    get_localized,
    infer_intensity_scores,
    normalize_gear_catalog,
    resolve_origin_geo,
    sanitize_roaster_url,
    scan_destination,
)
from translations import FALLBACK_LANG, SUPPORTED_LANGUAGES, normalize_lang
from image_search import (
    MAX_IMAGE_CANDIDATES,
    collect_image_urls,
    curated_packshot_urls,
    fetch_official_image_bytes,
    is_public_image_url,
    sanitize_image_url,
)

STORY_LANG = {
    "da": "Danish",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
}

ORIGINS = [
    "Ethiopia", "Etiopien", "Colombia", "Kenya", "Brazil", "Brasilien",
    "Guatemala", "Costa Rica", "Rwanda", "Burundi", "Yemen", "Jemen",
    "Peru", "Honduras", "El Salvador", "Panama", "Indonesia", "Indonesien",
    "India", "Indien", "Mexico", "Tanzania", "Uganda", "Nicaragua", "Bolivia",
]

ORIGIN_CANON = {
    "etiopien": "Ethiopia",
    "ethiopia": "Ethiopia",
    "brasilien": "Brazil",
    "brazil": "Brazil",
    "jemen": "Yemen",
    "yemen": "Yemen",
    "indonesien": "Indonesia",
    "indonesia": "Indonesia",
    "indien": "India",
    "india": "India",
}

PROCESSES = ["Vasket", "Natural", "Anaerob", "Honey"]
ROAST_LEVELS = ["Lys", "Medium-Lys", "Medium", "Medium-Mørk", "Mørk"]

PROCESS_MAP = {
    "Vasket": ["washed", "vasket", "wet process", "fully washed"],
    "Natural": ["natural", "naturlig", "dry process", "tørret"],
    "Honey": ["honey", "honning", "pulped natural"],
    "Anaerob": ["anaerobic", "anaerob", "carbonic"],
}

# Longer roast aliases first so "medium-dark" wins over "medium" / "dark".
ROAST_MAP = {
    "Medium-Lys": ["medium-light", "medium lys", "medium-lys", "mellemlys", "light-medium"],
    "Medium-Mørk": ["medium-dark", "medium-mørk", "medium mørk", "mellemmørk", "medium dark"],
    "Medium": ["mellemristet", "mellemristede", "medium"],
    "Lys": ["lysristet", "lysristede", "light", "lys"],
    "Mørk": ["mørkristet", "mørkriste", "mørkpistet", "morkristet", "dark", "mørk"],
}

# Official current Flash IDs. gemini-1.5-flash / 2.0-flash are retired on v1beta.
GEMINI_STABLE_MODEL = "gemini-3.6-flash"
GEMINI_MODELS = (
    GEMINI_STABLE_MODEL,
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-flash-latest",
)
ENV_PLACEHOLDER = "GEMINI_API_KEY=\n"

KNOWN_ROASTERS = [
    "Copenhagen Roaster",
    "The Coffee Collective",
    "Coffee Collective",
    "Prolog Coffee",
    "April Coffee",
    "La Cabra",
    "The Barn",
    "Coffee Mind",
    "Just Coffee",
    "Democratic Coffee",
    "Original Coffee",
    "Andersen & Maillard",
]

ROASTER_MARKERS = re.compile(
    r"\b(roaster|coffee|mikroristeri|kafferisteri|risteri|brew|collective|est\.?)\b",
    re.IGNORECASE,
)

FIELD_LABELS = re.compile(
    r"^(oprindelse|origin|forarbejdning|process|proces|ristningsgrad|"
    r"roast(?:\s*level)?|ristning|variety|varietal|sort|højde|altitude|"
    r"vægt|net\s*wt|noter|smagsnoter|tasting\s*notes?|smag(?:\s*af)?|"
    r"brew\s*method|bryg(?:gemetode)?|region|gård|farm|producer|"
    r"høst|harvest|højde over havet)$",
    re.IGNORECASE,
)

NOISE_LINE = re.compile(
    r"^(www\.|https?://|\d+\s*(g|kg|ml|%|gram)|net\s*wt|best\s*before|"
    r"holdbar|e\s*\d{3,}|est\.?\s*\d{0,4}|©|scan|batch|"
    r"ler\s+oil|sedato|a\s+posen|posen)$",
    re.IGNORECASE,
)

GRAPHIC_NOISE = re.compile(
    r"^(ler\s+oil|est\.?|sedato|a\s+posen|posen)$",
    re.IGNORECASE,
)

# Strong coffee titles — checked in the upper/middle block first.
# Cup-quality words (crema, body, aroma) are not product titles.
NAME_PRIORITY = [
    (r"slow\s+roast", "Slow Roast"),
    (r"yirgacheffe", "Yirgacheffe"),
    (r"geisha|gesha", "Geisha"),
    (r"espresso", "Espresso"),
]
NAME_SKIP_TITLES = {"crema"}

ORIGIN_FUZZY_CUTOFF = 0.80

PROCESS_LINE = re.compile(
    r"(?i)^\s*(natural|naturlig|washed|vasket|wet\s+process|fully\s+washed|"
    r"honey|pulped\s+natural|anaerobic|anaerob|carbonic|dry\s+process)\s*$",
)

NOTES_LEAD = re.compile(
    r"(?:noter\s+af|smag\s+af|tasting\s+notes?|smagsnoter|notes?)\s*[:.\-]?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

BREW_METHODS_REC = [
    "Espresso",
    "V60 / Pour-over",
    "Stempelkande (French Press)",
    "Filter",
]

GRIND_SIZES = ["Fin", "Medium-fin", "Medium", "Grov"]

METHOD_ALIASES = {
    "Espresso": ["espresso", "ristretto"],
    "V60 / Pour-over": ["v60", "pour-over", "pour over", "pourover", "kalita", "chemex"],
    "Stempelkande (French Press)": ["french press", "stempelkande", "plunger"],
    "Filter": ["filter", "batch brew", "drip", "dryp"],
}

GRIND_ALIASES = {
    "Medium-fin": ["medium-fin", "medium fine", "medium-fine", "mellemfin"],
    "Fin": ["fin", "fine"],
    "Medium": ["medium", "mellem"],
    "Grov": ["grov", "coarse", "groft"],
}

FLAVOR_NOTES = [
    "Mørk chokolade",
    "Chokolade",
    "Karamel",
    "Blåbær",
    "Citrus",
    "Nødder",
    "Nøddet",
    "Honning",
    "Jasmin",
    "Fersken",
    "Bergamot",
    "Kakao",
    "Æble",
    "Vanilje",
    "Tørret frugt",
    "Blomstret",
    "Vinøs",
    "Hasselnød",
    "Solbær",
    "Jordbær",
    "Grapefrugt",
    "Tropisk",
]

FLAVOR_ALIASES: dict[str, list[str]] = {
    "Mørk chokolade": ["mørk chokolade", "dark chocolate", "mork chokolade"],
    "Chokolade": ["chokolade", "chocolate"],
    "Karamel": ["karamel", "caramel", "karameliseret"],
    "Blåbær": ["blåbær", "blaabaer", "blueberry", "blueberries"],
    "Citrus": ["citrus", "citron", "lemon", "lime"],
    "Nødder": ["nødder", "nuts"],
    "Nøddet": ["nøddet", "nutty"],
    "Honning": ["honning", "honey"],
    "Jasmin": ["jasmin", "jasmine"],
    "Fersken": ["fersken", "peach"],
    "Bergamot": ["bergamot", "bergamotte"],
    "Kakao": ["kakao", "cocoa"],
    "Æble": ["æble", "apple"],
    "Vanilje": ["vanilje", "vanilla"],
    "Tørret frugt": ["tørret frugt", "dried fruit", "tørrede frugter"],
    "Blomstret": ["blomstret", "floral", "florals"],
    "Vinøs": ["vinøs", "wine", "vin"],
    "Hasselnød": ["hasselnød", "hazelnut", "hassel"],
    "Solbær": ["solbær", "blackcurrant"],
    "Jordbær": ["jordbær", "strawberry"],
    "Grapefrugt": ["grapefrugt", "grapefruit"],
    "Tropisk": ["tropisk", "tropical"],
}

FLAVOR_LOCALES: dict[str, dict[str, str]] = {
    "Mørk chokolade": {"da": "Mørk chokolade", "en": "Dark chocolate"},
    "Chokolade": {"da": "Chokolade", "en": "Chocolate"},
    "Karamel": {"da": "Karamel", "en": "Caramel"},
    "Blåbær": {"da": "Blåbær", "en": "Blueberry"},
    "Citrus": {"da": "Citrus", "en": "Citrus"},
    "Nødder": {"da": "Nødder", "en": "Nuts"},
    "Nøddet": {"da": "Nøddet", "en": "Nutty"},
    "Honning": {"da": "Honning", "en": "Honey"},
    "Jasmin": {"da": "Jasmin", "en": "Jasmine"},
    "Fersken": {"da": "Fersken", "en": "Peach"},
    "Bergamot": {"da": "Bergamot", "en": "Bergamot"},
    "Kakao": {"da": "Kakao", "en": "Cocoa"},
    "Æble": {"da": "Æble", "en": "Apple"},
    "Vanilje": {"da": "Vanilje", "en": "Vanilla"},
    "Tørret frugt": {"da": "Tørret frugt", "en": "Dried fruit"},
    "Blomstret": {"da": "Blomstret", "en": "Floral"},
    "Vinøs": {"da": "Vinøs", "en": "Winey"},
    "Hasselnød": {"da": "Hasselnød", "en": "Hazelnut"},
    "Solbær": {"da": "Solbær", "en": "Blackcurrant"},
    "Jordbær": {"da": "Jordbær", "en": "Strawberry"},
    "Grapefrugt": {"da": "Grapefrugt", "en": "Grapefruit"},
    "Tropisk": {"da": "Tropisk", "en": "Tropical"},
}

PROCESS_LOCALES = {
    "Vasket": {"da": "Vasket", "en": "Washed"},
    "Natural": {"da": "Natural", "en": "Natural"},
    "Anaerob": {"da": "Anaerob", "en": "Anaerobic"},
    "Honey": {"da": "Honey", "en": "Honey"},
}

ROAST_LOCALES = {
    "Lys": {"da": "Lys", "en": "Light"},
    "Medium-Lys": {"da": "Medium-Lys", "en": "Medium-Light"},
    "Medium": {"da": "Medium", "en": "Medium"},
    "Medium-Mørk": {"da": "Medium-Mørk", "en": "Medium-Dark"},
    "Mørk": {"da": "Mørk", "en": "Dark"},
}

ORIGIN_PRINT_LOCALES = {
    "Brasilien": {"da": "Brasilien", "en": "Brazil"},
    "Brazil": {"da": "Brasilien", "en": "Brazil"},
    "Etiopien": {"da": "Etiopien", "en": "Ethiopia"},
    "Ethiopia": {"da": "Etiopien", "en": "Ethiopia"},
}

METHOD_LOCALES = {
    "Espresso": {"da": "Espresso", "en": "Espresso"},
    "V60 / Pour-over": {"da": "V60 / Pour-over", "en": "V60 / Pour-over"},
    "Stempelkande (French Press)": {"da": "Stempelkande (French Press)", "en": "French Press"},
    "Filter": {"da": "Filter", "en": "Filter"},
}

GRIND_LOCALES = {
    "Fin": {"da": "Fin", "en": "Fine"},
    "Medium-fin": {"da": "Medium-fin", "en": "Medium-fine"},
    "Medium": {"da": "Medium", "en": "Medium"},
    "Grov": {"da": "Grov", "en": "Coarse"},
}

BREW_RATIO_COPY = {
    "espresso": {
        "da": "1:2 (18g kaffe til 36g espresso)",
        "en": "1:2 (18g coffee to 36g espresso)",
    },
    "pour_over": {
        "da": "1:16 (60g kaffe pr. 1 liter vand)",
        "en": "1:16 (60g coffee per 1 liter water)",
    },
    "press": {
        "da": "1:15 (67g kaffe pr. 1 liter vand)",
        "en": "1:15 (67g coffee per 1 liter water)",
    },
    "filter": {
        "da": "1:16 (60g kaffe pr. 1 liter vand)",
        "en": "1:16 (60g coffee per 1 liter water)",
    },
}

SUITABLE_FOR = ["Espresso", "Filter", "Mælkedrikke", "Stempelkande"]
SUITABLE_LOCALES: dict[str, dict[str, str]] = {
    "Espresso": {"da": "Espresso", "en": "Espresso"},
    "Filter": {"da": "Filter", "en": "Filter"},
    "Mælkedrikke": {"da": "Mælkedrikke", "en": "Milk drinks"},
    "Stempelkande": {"da": "Stempelkande", "en": "French Press"},
}
SUITABLE_ALIASES: dict[str, list[str]] = {
    "Espresso": [
        "espresso",
        "machines",
        "machine",
        "maskine",
        "for machines",
        "kaffemaskine",
        "portafilter",
    ],
    "Filter": ["filter", "pour-over", "pour over", "v60", "drip", "for filter", "filterkaffe"],
    "Mælkedrikke": [
        "mælkedrikke",
        "mælkedrik",
        "milk",
        "latte",
        "macchiato",
        "latte macchiato",
        "milk drinks",
        "ideal for latte",
    ],
    "Stempelkande": ["stempelkande", "french press", "press", "plunger"],
}
_NEXT_FIELD = (
    r"oprindelse|origin|forarbejdning|process|proces|ristningsgrad|"
    r"roast|ristning|noter|smag|tasting|variety|varietal"
)


def _ui_lang(lang: str | None) -> str:
    code = (lang or "da").lower().strip()
    if code in STORY_LANG:
        return normalize_lang(code)
    return normalize_lang(lang)


def _copy_lang(lang: str | None) -> str:
    """Languages with full extraction copy (flavor tags, process, brew)."""
    return normalize_lang(lang)


def flavor_notes_for(lang: str = "da") -> list[str]:
    code = _copy_lang(lang)
    return [FLAVOR_LOCALES[name][code] for name in FLAVOR_NOTES]


def processes_for(lang: str = "da") -> list[str]:
    code = _copy_lang(lang)
    return [PROCESS_LOCALES[name][code] for name in PROCESSES]


def roast_levels_for(lang: str = "da") -> list[str]:
    code = _copy_lang(lang)
    return [ROAST_LOCALES[name][code] for name in ROAST_LEVELS]


def localize_mapped(value: str, locales: dict[str, dict[str, str]], lang: str) -> str:
    raw = re.sub(r"\s+", " ", (value or "").strip())
    if not raw:
        return ""
    code = _copy_lang(lang)
    lowered = raw.lower()
    for canon, names in locales.items():
        variants = {canon.lower(), *(str(name).lower() for name in names.values())}
        if lowered in variants:
            return names.get(code, names.get("da", canon))
    return raw


def localize_flavor(tag: str, lang: str = "da") -> str:
    canon = _canonical_flavor(tag) or tag
    names = FLAVOR_LOCALES.get(canon)
    if not names:
        return tag
    return names.get(_copy_lang(lang), names["da"])


def flavor_i18n_table() -> dict[str, dict[str, str]]:
    """Bidirectional flavor lookup so saved language tags can switch instantly."""
    table: dict[str, dict[str, str]] = {}
    for canon, names in FLAVOR_LOCALES.items():
        entry = {code: names.get(code, names.get("da", canon)) for code in SUPPORTED_LANGUAGES}
        entry.update(names)
        keys = {canon, *names.values(), *FLAVOR_ALIASES.get(canon, [])}
        for key in keys:
            compact = re.sub(r"\s+", " ", str(key or "").strip())
            if not compact:
                continue
            table[compact] = entry
            table[compact.lower()] = entry
            table[compact.title()] = entry
    return table


def localize_flavor_tags(tags: list[str] | None, lang: str = "da") -> list[str]:
    return [localize_flavor(tag, lang) for tag in (tags or []) if str(tag).strip()]


def _flatten_i18n_sources(*sources: Any) -> list[Any]:
    out: list[Any] = []
    for source in sources:
        if not source:
            continue
        if isinstance(source, dict):
            for value in source.values():
                if isinstance(value, (list, tuple)):
                    out.extend(value)
                elif value:
                    out.append(value)
            continue
        out.append(source)
    return out


def flavor_tags_lang_map(*sources: Any) -> dict[str, list[str]]:
    """Canonicalize flavor pills, then emit a language → tags dictionary."""
    canons = extract_flavor_canons(*sources)
    if not canons:
        return {}
    return {code: [localize_flavor(tag, code) for tag in canons] for code in SUPPORTED_LANGUAGES}


def brew_recommendation_lang_map(raw: Any) -> dict[str, dict[str, str]]:
    """Expand a flat or partial brew object into a language map."""
    if not raw:
        return {}
    if isinstance(raw, dict) and any(isinstance(item, dict) for item in raw.values()):
        seed = next((item for item in raw.values() if isinstance(item, dict)), {})
        method = str(seed.get("recommended_method") or "")
        grind = str(seed.get("grind_size") or "")
        temp = str(seed.get("water_temp") or "")
        ratio = str(seed.get("brew_ratio") or "")
    elif isinstance(raw, dict):
        method = str(raw.get("recommended_method") or "")
        grind = str(raw.get("grind_size") or "")
        temp = str(raw.get("water_temp") or "")
        ratio = str(raw.get("brew_ratio") or "")
    else:
        return {}
    method = _canon_listed(method, BREW_METHODS_REC, METHOD_ALIASES) or method
    grind = _canon_listed(grind, GRIND_SIZES, GRIND_ALIASES) or grind
    out: dict[str, dict[str, str]] = {}
    for code in SUPPORTED_LANGUAGES:
        out[code] = {
            "recommended_method": localize_mapped(method, METHOD_LOCALES, code) if method else "",
            "grind_size": localize_mapped(grind, GRIND_LOCALES, code) if grind else "",
            "water_temp": temp,
            "brew_ratio": _localize_brew_ratio(ratio, code) if ratio else "",
        }
    if isinstance(raw, dict):
        for code, item in raw.items():
            if isinstance(item, dict) and any(str(item.get(key) or "").strip() for key in item):
                key = str(code).lower().strip()
                out[key] = {
                    "recommended_method": str(item.get("recommended_method") or out.get(key, {}).get("recommended_method") or ""),
                    "grind_size": str(item.get("grind_size") or out.get(key, {}).get("grind_size") or ""),
                    "water_temp": str(item.get("water_temp") or temp),
                    "brew_ratio": str(item.get("brew_ratio") or out.get(key, {}).get("brew_ratio") or ""),
                }
    return {code: brew for code, brew in out.items() if any(brew.values())}


def _canonical_suitable(token: str) -> str:
    lowered = re.sub(r"\s+", " ", (token or "").strip()).lower()
    if not lowered:
        return ""
    for canon, names in SUITABLE_LOCALES.items():
        if lowered in {canon.lower(), *(str(name).lower() for name in names.values())}:
            return canon
    for canon, aliases in SUITABLE_ALIASES.items():
        if any(lowered == alias or alias in lowered for alias in aliases):
            return canon
    return ""


def suitable_for_i18n_table() -> dict[str, dict[str, str]]:
    table: dict[str, dict[str, str]] = {}
    for canon, names in SUITABLE_LOCALES.items():
        entry = {code: names.get(code, names.get("da", canon)) for code in SUPPORTED_LANGUAGES}
        entry.update(names)
        keys = {canon, *names.values(), *SUITABLE_ALIASES.get(canon, [])}
        for key in keys:
            compact = re.sub(r"\s+", " ", str(key or "").strip())
            if not compact:
                continue
            table[compact] = entry
            table[compact.lower()] = entry
    return table


def localize_suitable(tag: str, lang: str = "da") -> str:
    canon = _canonical_suitable(tag)
    names = SUITABLE_LOCALES.get(canon or "")
    if not names:
        return (tag or "").strip()
    return names.get(_copy_lang(lang), names["da"])


def extract_suitable_for(*sources: str | list[str] | None, lang: str = "da") -> list[str]:
    hits: list[str] = []
    blobs: list[str] = []
    for source in sources:
        if not source:
            continue
        if isinstance(source, (list, tuple)):
            for item in source:
                text = str(item).strip()
                if not text:
                    continue
                canon = _canonical_suitable(text)
                if canon:
                    hits.append(canon)
                else:
                    blobs.append(text)
            continue
        blobs.append(str(source))
    blob = " ".join(blobs).lower()
    if blob:
        for canon, aliases in SUITABLE_ALIASES.items():
            if any(re.search(rf"\b{re.escape(alias)}\b", blob) for alias in aliases):
                hits.append(canon)
    seen: set[str] = set()
    ordered: list[str] = []
    for canon in SUITABLE_FOR:
        if canon in hits and canon not in seen:
            seen.add(canon)
            ordered.append(canon)
    return [localize_suitable(tag, lang) for tag in ordered]


def suitable_for_catalog(lang: str = "da") -> list[str]:
    return [localize_suitable(name, lang) for name in SUITABLE_FOR]


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _parse_env_file(env_path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not env_path.is_file():
        return parsed
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            parsed[key] = value
    return parsed


def load_local_env() -> None:
    """Load root .env into os.environ without overwriting existing vars."""
    for key, value in _parse_env_file(_project_root() / ".env").items():
        if key and key not in os.environ:
            os.environ[key] = value


def _persist_gemini_key(env_path: Path, key: str) -> None:
    """Write or fill GEMINI_API_KEY in .env. Never clears a non-empty value."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    written = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith("GEMINI_API_KEY="):
            _, _, current = line.partition("=")
            if current.strip().strip("'").strip('"'):
                out.append(line)
            else:
                out.append(f"GEMINI_API_KEY={key}")
            written = True
        else:
            out.append(line)
    if not written:
        if out and out[-1].strip():
            out.append("")
        out.append(f"GEMINI_API_KEY={key}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _write_streamlit_secrets(key: str) -> Path | None:
    """Mirror a non-empty key into gitignored Streamlit secrets."""
    if not key:
        return None
    secrets_dir = _project_root() / ".streamlit"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secrets_path = secrets_dir / "secrets.toml"
    if secrets_path.is_file():
        existing = _parse_env_file(secrets_path).get("GEMINI_API_KEY", "")
        if not existing:
            match = re.search(
                r'(?m)^GEMINI_API_KEY\s*=\s*["\']?([^"\'\n]*)["\']?',
                secrets_path.read_text(encoding="utf-8"),
            )
            existing = (match.group(1) if match else "").strip()
        if existing:
            return secrets_path
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    secrets_path.write_text(f'GEMINI_API_KEY = "{escaped}"\n', encoding="utf-8")
    return secrets_path


def ensure_local_env() -> Path:
    """Create or repair local .env so GEMINI_API_KEY is always declared."""
    env_path = _project_root() / ".env"
    if not env_path.exists():
        env_path.write_text(ENV_PLACEHOLDER, encoding="utf-8")
    elif "GEMINI_API_KEY" not in env_path.read_text(encoding="utf-8"):
        prefix = "" if env_path.read_text(encoding="utf-8").endswith("\n") else "\n"
        with env_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{ENV_PLACEHOLDER}")
    load_local_env()
    file_key = _parse_env_file(env_path).get("GEMINI_API_KEY", "").strip()
    env_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    key = env_key or file_key
    if env_key and not file_key:
        _persist_gemini_key(env_path, env_key)
        key = env_key
    if key:
        os.environ["GEMINI_API_KEY"] = key
        _write_streamlit_secrets(key)
    return env_path


def get_gemini_api_key() -> str:
    ensure_local_env()
    load_local_env()
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        secrets_path = _project_root() / ".streamlit" / "secrets.toml"
        key = _parse_env_file(secrets_path).get("GEMINI_API_KEY", "").strip()
        if not key and secrets_path.is_file():
            match = re.search(
                r'(?m)^GEMINI_API_KEY\s*=\s*["\']?([^"\'\n]*)["\']?',
                secrets_path.read_text(encoding="utf-8"),
            )
            key = (match.group(1) if match else "").strip()
    if key:
        os.environ["GEMINI_API_KEY"] = key
    return key


def gemini_available() -> bool:
    return bool(get_gemini_api_key())


def scan_available() -> bool:
    return gemini_available() or bool(configure_tesseract())


def _canon_choice(value: str, mapping: dict[str, list[str]], options: list[str]) -> str:
    raw = re.sub(r"\s+", " ", (value or "").strip())
    if not raw:
        return ""
    for option in options:
        if option.lower() == raw.lower():
            return option
    hit = _map_aliases(raw, mapping)
    return hit if hit in options else ""


def _canon_process(value: str) -> str:
    return _canon_choice(value, PROCESS_MAP, PROCESSES)


def _canon_roast(value: str) -> str:
    return _canon_choice(value, ROAST_MAP, ROAST_LEVELS)


def _extract_roaster_url(parsed: dict[str, Any]) -> str:
    """Prefer Gemini's roaster homepage, then any URL printed on the bag."""
    for key in ("roaster_url", "website", "official_website", "homepage", "url"):
        found = sanitize_roaster_url(parsed.get(key))
        if found:
            return found
    for key in ("raw_text", "official_notes", "roaster_notes"):
        found = sanitize_roaster_url(parsed.get(key))
        if found:
            return found
    return ""


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def normalize_scan_fields(parsed: dict[str, Any], lang: str = "da") -> dict[str, Any]:
    """Map Gemini/Tesseract fields onto the add-bean widget keys."""
    notes = (parsed.get("official_notes") or parsed.get("roaster_notes") or "").strip()
    name = (parsed.get("bean_name") or parsed.get("name") or "").strip()
    flavor_map = flavor_tags_lang_map(
        parsed.get("flavor_tags"),
        parsed.get("flavor_notes"),
        notes,
    )
    flavors = get_localized(flavor_map, lang) or []
    out = dict(parsed)
    out["lang"] = _copy_lang(lang)
    out["name"] = name
    out["roaster"] = (parsed.get("roaster") or "").strip()
    out["origin"] = (parsed.get("origin") or "").strip()
    out["process"] = localize_mapped(_canon_process(parsed.get("process") or ""), PROCESS_LOCALES, lang)
    out["roast_level"] = localize_mapped(_canon_roast(parsed.get("roast_level") or ""), ROAST_LOCALES, lang)
    out["roaster_notes"] = notes
    out["official_notes"] = notes
    out["flavor_notes"] = flavors
    out["flavor_tags"] = flavor_map
    out["suitable_for"] = extract_suitable_for(
        parsed.get("suitable_for"),
        parsed.get("official_notes"),
        notes,
        lang=lang,
    )
    story_map = _as_story_map(parsed.get("story"), lang)
    out["story"] = story_map
    out["roast_date"] = (parsed.get("roast_date") or "").strip()
    out["altitude"] = (parsed.get("altitude") or "").strip()
    out["varietal"] = (parsed.get("varietal") or parsed.get("variety") or "").strip()
    lat, lng, region = resolve_origin_geo(
        out["origin"],
        parsed.get("region_full") or "",
        parsed.get("latitude"),
        parsed.get("longitude"),
    )
    out["latitude"] = lat
    out["longitude"] = lng
    out["region_full"] = region
    out["roaster_url"] = _extract_roaster_url(parsed)
    out = refine_label_fields(out, lang=lang)
    scores = infer_intensity_scores(
        _first_present(
            out.get("roaster_acidity"),
            parsed.get("roaster_acidity"),
            out.get("acidity_score"),
            parsed.get("acidity_score"),
        ),
        _first_present(
            out.get("roaster_body"),
            parsed.get("roaster_body"),
            out.get("body_score"),
            parsed.get("body_score"),
        ),
        _first_present(
            out.get("roaster_roast_level"),
            parsed.get("roaster_roast_level"),
            out.get("roast_level_score"),
            parsed.get("roast_level_score"),
        ),
        out.get("roast_level") or "",
        out.get("origin") or "",
        out.get("process") or "",
        out.get("name") or "",
    )
    out.update(scores)
    out["roaster_acidity"] = scores["acidity_score"]
    out["roaster_body"] = scores["body_score"]
    out["roaster_roast_level"] = scores["roast_level_score"]
    if not isinstance(out.get("flavor_tags"), dict):
        out["flavor_tags"] = flavor_tags_lang_map(out.get("flavor_tags"), out.get("flavor_notes"))
    out["flavor_notes"] = get_localized(out["flavor_tags"], lang) or []
    if not isinstance(out.get("story"), dict):
        out["story"] = _as_story_map(out.get("story"), lang)
    brew = infer_brew_recommendation(out, lang=lang)
    out["brew_recommendation"] = brew
    flat = get_localized(brew, lang)
    if isinstance(flat, dict):
        out.update(flat)
    return ensure_scan_schema(out, lang=lang)


def _as_story_map(value: Any, lang: str) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            str(key).lower().strip(): str(item or "").strip()
            for key, item in value.items()
            if str(item or "").strip()
        }
    text = str(value or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return _as_story_map(parsed, lang)
        except json.JSONDecodeError:
            pass
    return {_copy_lang(lang): text}


def _canon_listed(value: str, options: list[str], aliases: dict[str, list[str]]) -> str:
    raw = re.sub(r"\s+", " ", (value or "").strip())
    if not raw:
        return ""
    for option in options:
        if option.lower() == raw.lower():
            return option
    lowered = raw.lower()
    for option, names in aliases.items():
        if any(name in lowered for name in names):
            return option
    return raw


def infer_brew_recommendation(parsed: dict[str, Any], lang: str = "da") -> dict[str, dict[str, str]]:
    """Use Gemini's brew object when present; otherwise infer from roast/origin/process."""
    raw = parsed.get("brew_recommendation")
    seed: dict[str, Any] = {}
    if isinstance(raw, dict) and any(isinstance(item, dict) for item in raw.values()):
        mapped = brew_recommendation_lang_map(raw)
        if mapped and all(
            mapped.get(code, {}).get("recommended_method")
            and mapped.get(code, {}).get("grind_size")
            and mapped.get(code, {}).get("water_temp")
            and mapped.get(code, {}).get("brew_ratio")
            for code in SUPPORTED_LANGUAGES
        ):
            return mapped
        seed = next((item for item in raw.values() if isinstance(item, dict)), {})
    elif isinstance(raw, dict):
        seed = raw
    else:
        seed = {
            "recommended_method": parsed.get("recommended_method") or "",
            "grind_size": parsed.get("grind_size") or "",
            "water_temp": parsed.get("water_temp") or "",
            "brew_ratio": parsed.get("brew_ratio") or "",
        }
    method = _canon_listed(str(seed.get("recommended_method") or ""), BREW_METHODS_REC, METHOD_ALIASES)
    grind = _canon_listed(str(seed.get("grind_size") or ""), GRIND_SIZES, GRIND_ALIASES)
    temp = re.sub(r"\s+", " ", str(seed.get("water_temp") or "").strip())
    ratio = re.sub(r"\s+", " ", str(seed.get("brew_ratio") or "").strip())

    roast = (parsed.get("roast_level") or "").lower()
    process = (parsed.get("process") or "").lower()
    origin = (parsed.get("origin") or "").lower()
    name = (parsed.get("name") or parsed.get("bean_name") or "").lower()
    notes = (parsed.get("roaster_notes") or parsed.get("official_notes") or "").lower()
    espresso_hint = any(
        token in f"{name} {roast} {notes}"
        for token in ("espresso", "mørk", "dark", "medium-mørk", "medium-dark")
    )
    african = any(token in origin for token in ("ethiopia", "etiopien", "kenya", "rwanda", "burundi"))
    light = any(token in roast for token in ("lys", "light"))
    natural = any(token in process for token in ("natural", "anaerob", "anaerobic"))

    if espresso_hint and not light:
        inferred = {
            "recommended_method": "Espresso",
            "grind_size": "Fin",
            "brew_key": "espresso",
        }
    elif light or african:
        inferred = {
            "recommended_method": "V60 / Pour-over",
            "grind_size": "Medium-fin",
            "brew_key": "pour_over",
        }
    elif natural:
        inferred = {
            "recommended_method": "Stempelkande (French Press)",
            "grind_size": "Grov",
            "brew_key": "press",
        }
    else:
        inferred = {
            "recommended_method": "Filter",
            "grind_size": "Medium",
            "brew_key": "filter",
        }
    method = method or inferred["recommended_method"]
    grind = grind or inferred["grind_size"]
    temp = temp or "92-94°C"
    out: dict[str, dict[str, str]] = {}
    for code in SUPPORTED_LANGUAGES:
        out[code] = {
            "recommended_method": localize_mapped(method, METHOD_LOCALES, code),
            "grind_size": localize_mapped(grind, GRIND_LOCALES, code),
            "water_temp": temp,
            "brew_ratio": _localize_brew_ratio(ratio, code) if ratio else BREW_RATIO_COPY[inferred["brew_key"]].get(code) or BREW_RATIO_COPY[inferred["brew_key"]].get(FALLBACK_LANG, ""),
        }
    if isinstance(raw, dict) and any(isinstance(item, dict) for item in raw.values()):
        for code, item in raw.items():
            if isinstance(item, dict):
                key = str(code).lower().strip()
                current = out.get(key, {})
                out[key] = {
                    "recommended_method": str(item.get("recommended_method") or current.get("recommended_method") or ""),
                    "grind_size": str(item.get("grind_size") or current.get("grind_size") or ""),
                    "water_temp": str(item.get("water_temp") or current.get("water_temp") or temp),
                    "brew_ratio": str(item.get("brew_ratio") or current.get("brew_ratio") or ""),
                }
    return out


def _localize_brew_ratio(ratio: str, lang: str) -> str:
    """Keep Gemini's numbers; swap coffee/water wording to the active language."""
    code = _copy_lang(lang)
    text = re.sub(r"\s+", " ", (ratio or "").strip())
    if not text:
        return BREW_RATIO_COPY["espresso"][code] if code else text
    if code == "en":
        text = re.sub(r"\bkaffe\b", "coffee", text, flags=re.IGNORECASE)
        text = re.sub(r"\bvand\b", "water", text, flags=re.IGNORECASE)
        text = re.sub(r"\bpr\.\b", "per", text, flags=re.IGNORECASE)
        text = re.sub(r"\btil\b", "to", text, flags=re.IGNORECASE)
    else:
        text = re.sub(r"\bcoffee\b", "kaffe", text, flags=re.IGNORECASE)
        text = re.sub(r"\bwater\b", "vand", text, flags=re.IGNORECASE)
        text = re.sub(r"\bper\b", "pr.", text, flags=re.IGNORECASE)
        text = re.sub(r"\bto\b", "til", text, flags=re.IGNORECASE)
    return text


_ORIGIN_PRINTED = {
    "ethiopia": "Etiopien",
    "etiopien": "Etiopien",
    "brazil": "Brasilien",
    "brasilien": "Brasilien",
    "colombia": "Colombia",
    "kenya": "Kenya",
    "guatemala": "Guatemala",
    "yemen": "Yemen",
    "jemen": "Yemen",
    "indonesia": "Indonesia",
    "indonesien": "Indonesia",
    "india": "India",
    "indien": "India",
}

def _blob_of(parsed: dict[str, Any], *keys: str) -> str:
    parts: list[str] = []
    for key in keys:
        value = parsed.get(key)
        if isinstance(value, dict):
            parts.extend(str(item or "") for item in value.values())
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item or "") for item in value)
        else:
            parts.append(str(value or ""))
    return " ".join(parts)


def _localize_origin_text(origin: str, lang: str) -> str:
    parts = [part.strip() for part in re.split(r"\s*(?:&|/|,| og | and )\s*", origin) if part.strip()]
    mapped = [_ORIGIN_PRINTED.get(part.lower(), part) for part in parts]
    localized = [localize_mapped(part, ORIGIN_PRINT_LOCALES, lang) for part in mapped]
    return " & ".join(part for part in localized if part)


def refine_label_fields(parsed: dict[str, Any], lang: str = "da") -> dict[str, Any]:
    """Canonicalize Gemini/Tesseract strings for printed specialty labels."""
    code = _copy_lang(lang)
    out = dict(parsed)
    blob = _blob_of(
        out,
        "name",
        "bean_name",
        "roaster",
        "origin",
        "official_notes",
        "roaster_notes",
        "altitude",
        "varietal",
        "raw_text",
        "story",
    )
    name = (out.get("name") or out.get("bean_name") or "").strip()
    out["name"] = name
    out["bean_name"] = name
    out["roaster"] = (out.get("roaster") or "").strip()

    origin = (out.get("origin") or "").strip()
    search = f"{origin} {blob}".lower()
    printed: list[str] = []
    if re.search(r"brasilien|brazil", search):
        printed.append("Brasilien")
    if re.search(r"etiopien|ethiopia", search):
        printed.append("Etiopien")
    if len(printed) >= 2:
        origin = " & ".join(localize_mapped(part, ORIGIN_PRINT_LOCALES, code) for part in printed)
    elif origin:
        origin = _localize_origin_text(origin, code)
    out["origin"] = origin

    altitude = (out.get("altitude") or "").strip()
    range_match = re.search(r"(\d{3,4})\s*[-–to]{1,3}\s*(\d{3,4})", altitude or blob, re.I)
    if range_match:
        out["altitude"] = f"{range_match.group(1)} - {range_match.group(2)} M."
    else:
        out["altitude"] = altitude

    varietal = (out.get("varietal") or out.get("variety") or "").strip()
    varietal = varietal.replace("í", "i").replace("Í", "I")
    variety_parts = [
        _pretty(part) for part in re.split(r"\s*(?:&|/|,| og | and )\s*", varietal) if part.strip()
    ]
    if variety_parts:
        out["varietal"] = " & ".join(variety_parts)

    if out.get("suitable_for"):
        out["suitable_for"] = extract_suitable_for(out.get("suitable_for"), lang=code)

    return out


def _gemini_prompt(lang: str = "da") -> str:
    code = _copy_lang(lang)
    story_lang = STORY_LANG.get(code, "Danish")
    lang_keys = ", ".join(f'"{item}"' for item in SUPPORTED_LANGUAGES)
    flavor_lines = "\n".join(
        f'    "{item}": array of 1–6 descriptors chosen only from ['
        + ", ".join(f'"{name}"' for name in flavor_notes_for(item))
        + "]"
        for item in SUPPORTED_LANGUAGES
    )
    processes = ", ".join(f'"{name}"' for name in processes_for(code))
    roasts = ", ".join(f'"{name}"' for name in roast_levels_for(code))
    brew_lines = []
    for item in SUPPORTED_LANGUAGES:
        methods = ", ".join(f'"{METHOD_LOCALES[name].get(item, name)}"' for name in BREW_METHODS_REC)
        grinds = ", ".join(f'"{GRIND_LOCALES[name].get(item, name)}"' for name in GRIND_SIZES)
        espresso_ratio = BREW_RATIO_COPY["espresso"].get(item, BREW_RATIO_COPY["espresso"]["en"])
        pour_ratio = BREW_RATIO_COPY["pour_over"].get(item, BREW_RATIO_COPY["pour_over"]["en"])
        brew_lines.append(
            f'    "{item}": {{"recommended_method": one of [{methods}], '
            f'"grind_size": one of [{grinds}], "water_temp": e.g. "92-94°C", '
            f'"brew_ratio": e.g. "{espresso_ratio}" or "{pour_ratio}"}}'
        )
    espresso_ratio = BREW_RATIO_COPY["espresso"].get(code, BREW_RATIO_COPY["espresso"]["en"])
    pour_ratio = BREW_RATIO_COPY["pour_over"].get(code, BREW_RATIO_COPY["pour_over"]["en"])
    origin_example = "Brazil & Ethiopia" if code == "en" else "Brasilien & Etiopien"
    press_name = METHOD_LOCALES["Stempelkande (French Press)"].get(code, "French Press")
    grind_fine = GRIND_LOCALES["Fin"].get(code, "Fine")
    grind_med_fine = GRIND_LOCALES["Medium-fin"].get(code, "Medium-fine")
    grind_coarse = GRIND_LOCALES["Grov"].get(code, "Coarse")
    suitable = ", ".join(f'"{name}"' for name in suitable_for_catalog(code))
    da_flavors = ", ".join(f'"{name}"' for name in flavor_notes_for("da"))
    en_flavors = ", ".join(f'"{name}"' for name in flavor_notes_for("en"))
    return (
        "You are a specialty-coffee label reader for BeanNote. "
        "STEP 1 — STRICT OPTICAL READING. "
        "Inspect this coffee bag photo and extract only values that are visibly "
        "printed or shown as icons. Do not browse the web and do not guess from "
        "typical-origin knowledge. Return ONE JSON object only (no markdown) "
        "with these keys:\n"
        '- "roaster": roaster / brand name\n'
        '- "bean_name": the exact primary product name rendered on the bag. '
        "Read the largest title together with any product-line text printed "
        "immediately above it. "
        "Copy the product identity as a title — do not pull words from the "
        "tasting paragraph. Cup-quality words in a blurb "
        "('flot crema', 'beautiful crema', 'crema') are not the bean name.\n"
        f'- "origin": countries in {story_lang} only if printed. Join two origins with " & " '
        f'(e.g. "{origin_example}"). Empty string if not printed.\n'
        '- "region_full": printed origin place name, e.g. "Yirgacheffe, Gedeo, Ethiopia"\n'
        '- "latitude": float WGS84 latitude only if printed, else null\n'
        '- "longitude": float WGS84 longitude only if printed, else null\n'
        '- "roast_date": roast date string if printed, else ""\n'
        '- "altitude": copy the printed MASL string exactly, e.g. "800 - 2100 M."\n'
        '- "varietal": copy printed varieties in ASCII '
        '(e.g. "Catuai & Heirloom", never "Catuaí")\n'
        f'- "process": exactly one of [{processes}] if printed — write this in {story_lang}. '
        "Empty string if the bag does not name a process.\n"
        f'- "roast_level": exactly one of [{roasts}] if printed, else ""\n'
        '- "roaster_acidity": integer 1–5 counted from printed bean-meters / dots / bars '
        "for acidity / brightness (filled icons out of 5). "
        "null if the bag does not print this meter. "
        'Alias: "acidity_score".\n'
        '- "roaster_body": integer 1–5 counted from printed bean-meters / dots / bars '
        "for body / mouthfeel. null if not printed. "
        'Alias: "body_score".\n'
        '- "roaster_roast_level": integer 1–5 counted from printed bean-meters / dots / bars '
        "for roast depth (1 = Light / Lys, 3 = Medium, 5 = Dark / Mørk). "
        "Map a printed Medium-Light meter to 2 and Medium-Dark to 4. "
        "null if not printed. "
        'Alias: "roast_level_score".\n'
        f'- "flavor_tags": language map keyed by {lang_keys}. Same flavors, same order, '
        "translated per key, taken only from printed tasting notes. Schema:\n"
        f"{flavor_lines}\n"
        f'  Example: {{"da": ["Mørk chokolade"], "en": ["Dark chocolate"]}}. '
        f"Danish catalog: [{da_flavors}]. English catalog: [{en_flavors}]. "
        "Use empty arrays when no flavor words are printed.\n"
        f'- "suitable_for": array of 1–4 brew-suitability labels in {story_lang} chosen only from '
        f"[{suitable}]. Read printed brew icons such as FOR MACHINES (Espresso), FOR FILTER, "
        "IDEAL FOR LATTE MACCHIATO (milk drinks), and French Press / Stempelkande. "
        "Empty array if no brew icons are printed.\n"
        f'- "official_notes": tasting-notes text copied from the bag and rewritten in {story_lang}. '
        "Empty string if none are printed.\n"
        f'- "story": language map keyed by {lang_keys}. Copy a printed producer/farm story only. '
        "Otherwise use empty strings — a later official-page lookup writes the coffee story. "
        'Example: {"da": "Høstet i 1.900 meters højde i Yirgacheffe-regionen...", '
        '"en": "Harvested at 1,900 meters in the Yirgacheffe region..."}. '
        "Each language key must stay in that language only — no mixed languages inside a key.\n"
        f'- "brew_recommendation": language map keyed by {lang_keys} with localized text fields:\n'
        f'{chr(10).join(brew_lines)}\n'
        "Copy printed brew advice only. If the bag does not print a recipe, "
        "use empty strings for recommended_method, grind_size, water_temp, and brew_ratio. "
        f"Do not infer V60 / {grind_med_fine} / {grind_fine} / {press_name} / {grind_coarse} "
        f"or ratios such as {espresso_ratio} / {pour_ratio} during this optical pass.\n"
        f"LANGUAGE MAPS: keys {lang_keys} MUST all be present for story, flavor_tags, and "
        "brew_recommendation. Adding another ISO key later (de, fr, es) uses the same shape. "
        f"Scalar fields (origin, process, roast_level, suitable_for, official_notes) stay in "
        f"{story_lang} because lang={code}.\n"
        "Read printed text and icons only. If a field is missing on the label, "
        "use an empty string, empty array, empty map, or null. "
        "Never invent a roaster, bean_name, farm, URL, bean-meter score, or story.\n"
        '- "roaster_url": official https homepage of the roaster if printed on the bag '
        "(www.example.com or https://…). Homepage only — never a product "
        "image, CDN asset, or marketplace listing. Empty string if unknown. Never invent a URL.\n"
        '- "image_candidates": array of up to 3 real public https URLs of official '
        "high-resolution studio packshots / product-container graphics from the roaster "
        "shop or CDN (for example Shopify, Cloudinary, official shop). Fewer than 3 is OK. "
        "Never invent a URL and never return a blurry phone photo.\n"
        '- "product_image_urls": same list as image_candidates (legacy alias).\n'
        '- "product_image_url": first image_candidates URL, or "" if unknown.'
    )


def official_product_search_query(roaster: str, name: str) -> str:
    """Google query used by Gemini Search Grounding for the official product page."""
    brand = re.sub(r"\s+", " ", (roaster or "").strip())
    product = re.sub(r"\s+", " ", (name or "").strip())
    if brand and product:
        return f'"{brand}" "{product}" official site / product details'
    if brand:
        return f'"{brand}" official site / product details'
    if product:
        return f'"{product}" official site / product details'
    return ""


def _grounded_product_prompt(name: str, roaster: str, lang: str = "da") -> str:
    code = _copy_lang(lang)
    story_lang = STORY_LANG.get(code, "Danish")
    lang_keys = ", ".join(f'"{item}"' for item in SUPPORTED_LANGUAGES)
    query = official_product_search_query(roaster, name)
    processes = ", ".join(f'"{name}"' for name in processes_for(code))
    roasts = ", ".join(f'"{name}"' for name in roast_levels_for(code))
    da_flavors = ", ".join(f'"{item}"' for item in flavor_notes_for("da"))
    en_flavors = ", ".join(f'"{item}"' for item in flavor_notes_for("en"))
    flavor_lines = "\n".join(
        f'    "{item}": array of 1–6 descriptors chosen only from ['
        + ", ".join(f'"{tag}"' for tag in flavor_notes_for(item))
        + "]"
        for item in SUPPORTED_LANGUAGES
    )
    brew_lines = []
    for item in SUPPORTED_LANGUAGES:
        methods = ", ".join(f'"{METHOD_LOCALES[name].get(item, name)}"' for name in BREW_METHODS_REC)
        grinds = ", ".join(f'"{GRIND_LOCALES[name].get(item, name)}"' for name in GRIND_SIZES)
        espresso_ratio = BREW_RATIO_COPY["espresso"].get(item, BREW_RATIO_COPY["espresso"]["en"])
        pour_ratio = BREW_RATIO_COPY["pour_over"].get(item, BREW_RATIO_COPY["pour_over"]["en"])
        brew_lines.append(
            f'    "{item}": {{"recommended_method": one of [{methods}], '
            f'"grind_size": one of [{grinds}], "water_temp": e.g. "92-94°C", '
            f'"brew_ratio": e.g. "{espresso_ratio}" or "{pour_ratio}"}}'
        )
    return (
        "You are BeanNote's official product-page researcher. "
        "STEP 2 — OFFICIAL WEB LOOKUP. "
        "Enable Google Search grounding and query Google for the official product page:\n"
        f"{query}\n"
        "Prefer the roaster's own shop or official site over marketplaces "
        "(Amazon, eBay, comparison sites) unless that marketplace is the brand's "
        "only official product page. "
        "STEP 3 — DATA VERIFICATION & ENRICHMENT. "
        "Extract metadata published on that official page. "
        "Return ONE JSON object only (no markdown) with these keys:\n"
        '- "roaster": official brand / roaster name\n'
        '- "bean_name": official product name\n'
        f'- "origin": origin country or countries in {story_lang}, joined with " & "\n'
        '- "region_full": region / farm / washing station, e.g. "Yirgacheffe, Gedeo, Ethiopia"\n'
        '- "latitude": float WGS84 if the official page publishes coordinates, else null\n'
        '- "longitude": float WGS84 if published, else null\n'
        '- "altitude": official MASL string, e.g. "1700 - 1900 M."\n'
        '- "varietal": official varieties in ASCII\n'
        f'- "process": exactly one of [{processes}] — Washed/Vasket, Natural, Anaerobic/Anaerob, or Honey\n'
        f'- "roast_level": exactly one of [{roasts}]\n'
        '- "roaster_acidity": official 1–5 acidity / brightness if the page publishes a meter. '
        'Alias: "acidity_score". null if unpublished.\n'
        '- "roaster_body": official 1–5 body / mouthfeel if published. '
        'Alias: "body_score". null if unpublished.\n'
        '- "roaster_roast_level": official 1–5 roast depth if published. '
        'Alias: "roast_level_score". null if unpublished.\n'
        f'- "flavor_tags": language map keyed by {lang_keys}. Full official tasting-notes map:\n'
        f"{flavor_lines}\n"
        f"Danish catalog: [{da_flavors}]. English catalog: [{en_flavors}].\n"
        f'- "official_notes": official tasting blurb rewritten in {story_lang}\n'
        f'- "story": language map keyed by {lang_keys}. 2–4 sentences from the official '
        "coffee story / producer notes. Each language key stays in that language only.\n"
        f'- "brew_recommendation": language map keyed by {lang_keys} for the official brew recipe:\n'
        f"{chr(10).join(brew_lines)}\n"
        f"LANGUAGE MAPS: keys {lang_keys} MUST all be present for story, flavor_tags, and "
        "brew_recommendation.\n"
        '- "product_page_url": official https product page you used. Never invent a URL.\n'
        '- "roaster_url": official https homepage. Homepage only — never a product image.\n'
        '- "image_candidates": up to 3 real official studio packshot https URLs from that page.\n'
        "If you cannot find a real official page, return empty strings, empty maps, and null scores. "
        "Never invent a farm, score, URL, or flavor that the official page does not publish."
    )


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return not any(not _blank(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return not any(not _blank(item) for item in value)
    return False


def _prefer_optical(optical: Any, official: Any) -> Any:
    return official if _blank(optical) else optical


def _prefer_official(optical: Any, official: Any) -> Any:
    return official if not _blank(official) else optical


def _printed_score(*values: Any) -> int | None:
    for value in values:
        score = clamp_intensity_score(value)
        if score is not None:
            return score
    return None


def _merge_flavor_sources(optical: dict[str, Any], official: dict[str, Any]) -> dict[str, list[str]]:
    """Official tasting notes first, then any extra pills printed on the bag."""
    web = extract_flavor_canons(
        official.get("flavor_tags"),
        official.get("flavor_notes"),
        official.get("official_notes"),
        official.get("roaster_notes"),
    )
    bag = extract_flavor_canons(
        optical.get("flavor_tags"),
        optical.get("flavor_notes"),
        optical.get("official_notes"),
        optical.get("roaster_notes"),
    )
    ordered: list[str] = []
    for tag in (*web, *bag):
        if tag not in ordered:
            ordered.append(tag)
    if not ordered:
        return {}
    return {code: [localize_flavor(tag, code) for tag in ordered] for code in SUPPORTED_LANGUAGES}


SCAN_SCALAR_DEFAULTS = {
    "roaster": "",
    "bean_name": "",
    "name": "",
    "origin": "",
    "region_full": "",
    "roast_date": "",
    "altitude": "",
    "varietal": "",
    "process": "",
    "roast_level": "",
    "official_notes": "",
    "roaster_notes": "",
    "roaster_url": "",
    "product_page_url": "",
    "product_image_url": "",
    "official_image_url": "",
    "scan_source": "",
    "scan_enrichment": "optical",
}

SCAN_LIST_KEYS = ("flavor_notes", "suitable_for", "image_candidates", "product_image_urls")
SCAN_MAP_KEYS = ("flavor_tags", "story", "brew_recommendation")
SCAN_SCORE_KEYS = (
    "roaster_acidity",
    "roaster_body",
    "roaster_roast_level",
    "acidity_score",
    "body_score",
    "roast_level_score",
)


def ensure_scan_schema(parsed: dict[str, Any], lang: str = "da") -> dict[str, Any]:
    """Guarantee every scan field exists in a DB-ready shape before save."""
    out = dict(parsed or {})
    out["lang"] = _copy_lang(lang)
    for key, default in SCAN_SCALAR_DEFAULTS.items():
        if key not in out or out[key] is None:
            out[key] = default
        elif isinstance(default, str):
            out[key] = str(out[key] or "").strip() if not isinstance(out[key], str) else out[key]
    for key in SCAN_LIST_KEYS:
        value = out.get(key)
        if not isinstance(value, list):
            out[key] = [value] if isinstance(value, str) and value.strip() else []
    for key in SCAN_MAP_KEYS:
        if not isinstance(out.get(key), dict):
            out[key] = {}
    if not out.get("name"):
        out["name"] = out.get("bean_name") or ""
    if not out.get("bean_name"):
        out["bean_name"] = out.get("name") or ""
    if not out.get("roaster_notes"):
        out["roaster_notes"] = out.get("official_notes") or ""
    if not out.get("official_notes"):
        out["official_notes"] = out.get("roaster_notes") or ""
    for key in SCAN_SCORE_KEYS:
        out[key] = clamp_intensity_score(out.get(key))
    return out


def merge_optical_and_official(
    optical: dict[str, Any] | None,
    official: dict[str, Any] | None,
) -> dict[str, Any]:
    """Optical printed meters and icons win; official page fills story and missing metadata."""
    bag = dict(optical or {})
    web = dict(official or {})
    if _blank(web):
        bag.setdefault("scan_enrichment", "optical")
        return bag

    out = dict(bag)
    out["roaster"] = _prefer_optical(bag.get("roaster"), web.get("roaster"))
    out["bean_name"] = _prefer_optical(
        bag.get("bean_name") or bag.get("name"),
        web.get("bean_name") or web.get("name"),
    )
    out["name"] = out["bean_name"]

    out["roaster_acidity"] = _printed_score(
        bag.get("roaster_acidity"), bag.get("acidity_score")
    ) or _printed_score(web.get("roaster_acidity"), web.get("acidity_score"))
    out["roaster_body"] = _printed_score(
        bag.get("roaster_body"), bag.get("body_score")
    ) or _printed_score(web.get("roaster_body"), web.get("body_score"))
    out["roaster_roast_level"] = _printed_score(
        bag.get("roaster_roast_level"), bag.get("roast_level_score")
    ) or _printed_score(web.get("roaster_roast_level"), web.get("roast_level_score"))
    out["acidity_score"] = out["roaster_acidity"]
    out["body_score"] = out["roaster_body"]
    out["roast_level_score"] = out["roaster_roast_level"]

    out["suitable_for"] = _prefer_optical(bag.get("suitable_for"), web.get("suitable_for"))
    out["roast_date"] = _prefer_optical(bag.get("roast_date"), web.get("roast_date"))
    for key in ("origin", "process", "roast_level", "altitude", "varietal", "region_full"):
        out[key] = _prefer_optical(bag.get(key), web.get(key))

    out["story"] = _prefer_official(bag.get("story"), web.get("story"))
    notes = _prefer_official(
        bag.get("official_notes") or bag.get("roaster_notes"),
        web.get("official_notes") or web.get("roaster_notes"),
    )
    out["official_notes"] = notes
    out["roaster_notes"] = notes
    flavors = _merge_flavor_sources(bag, web)
    if flavors:
        out["flavor_tags"] = flavors
    out["brew_recommendation"] = _prefer_official(
        bag.get("brew_recommendation"),
        web.get("brew_recommendation"),
    )
    out["roaster_url"] = _prefer_optical(
        sanitize_roaster_url(bag.get("roaster_url")),
        sanitize_roaster_url(web.get("roaster_url") or web.get("product_page_url")),
    )
    out["product_page_url"] = sanitize_roaster_url(
        web.get("product_page_url") or bag.get("product_page_url")
    )
    out = _with_grounded_images(out)
    out["scan_enrichment"] = "optical+web"
    return out


def _response_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None) or ""
        if text:
            return text
    except Exception:
        text = ""
    chunks: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                chunks.append(piece)
    return "\n".join(chunks)


_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _strip_json_fences(text: str) -> str:
    """Remove markdown ``` / ```json wrappers before json.loads()."""
    raw = (text or "").strip().lstrip("\ufeff")
    if not raw:
        return ""
    fenced = _JSON_FENCE.search(raw)
    if fenced:
        return fenced.group(1).strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_gemini_json(text: str) -> dict[str, Any]:
    raw = _strip_json_fences(text)
    if not raw:
        raise ValueError("empty Gemini response")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Gemini response was not a JSON object")
    return data


def _register_heif_opener() -> None:
    """Optional HEIC/HEIF support when pillow-heif is installed (iOS album uploads)."""
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        return


def open_oriented_image(image_bytes: bytes) -> Image.Image:
    """Open any camera/album upload and apply EXIF orientation so it is upright."""
    _register_heif_opener()
    image = Image.open(BytesIO(image_bytes))
    image.load()
    try:
        image = ImageOps.exif_transpose(image) or image
    except Exception:
        pass
    if image.mode not in {"L", "RGB"}:
        image = image.convert("RGB")
    return image


def _prepare_scan_image(image_bytes: bytes) -> Image.Image:
    image = open_oriented_image(image_bytes)
    width, height = image.size
    longest = max(width, height)
    if longest > 1600:
        scale = 1600 / longest
        image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    return image


def _image_jpeg_bytes(image: Image.Image) -> bytes:
    """Encode an in-memory PIL image as JPEG bytes for the Vision API."""
    rgb = image.convert("RGB") if image.mode != "RGB" else image
    buffer = BytesIO()
    rgb.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def encode_scan_jpeg(image_bytes: bytes) -> bytes:
    """Normalize any camera/album upload (JPEG/PNG/WebP/HEIC) to JPEG for Gemini + storage."""
    return _image_jpeg_bytes(_prepare_scan_image(image_bytes))


def _gemini_image_part(image: Image.Image):
    """Build an inline JPEG part so the SDK never needs a filename or PIL path."""
    from google.genai import types

    return types.Part.from_bytes(data=_image_jpeg_bytes(image), mime_type="image/jpeg")


def _quota_gemini_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("429", "resource_exhausted", "exceeded your current quota"))


def _transient_gemini_error(exc: Exception) -> bool:
    if _quota_gemini_error(exc):
        return False
    text = str(exc).lower()
    return any(token in text for token in ("503", "unavailable", "high demand"))


def _tools_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "invalid_argument",
            "response_mime_type",
            "google_search",
            "search tool",
            "tools are not supported",
            "tool is not supported",
            "function calling is not enabled",
        )
    )


def _google_search_tools(types: Any) -> list[Any]:
    """Gemini built-in Search Grounding — no Programmable Search / CX keys."""
    for factory in (
        lambda: types.Tool(google_search=types.GoogleSearch()),
        lambda: types.Tool(google_search_retrieval=types.GoogleSearchRetrieval()),
    ):
        try:
            tool = factory()
        except Exception:
            continue
        if tool is not None:
            return [tool]
    return []


def _legacy_google_search_tools() -> list[Any] | None:
    return [{"google_search": {}}]


def _iter_grounding_chunks(response: Any) -> list[Any]:
    chunks: list[Any] = []
    for candidate in getattr(response, "candidates", None) or []:
        meta = getattr(candidate, "grounding_metadata", None)
        if meta is None and isinstance(candidate, dict):
            meta = candidate.get("grounding_metadata")
        if not meta:
            continue
        found = getattr(meta, "grounding_chunks", None)
        if found is None and isinstance(meta, dict):
            found = meta.get("grounding_chunks")
        if found:
            chunks.extend(list(found))
    return chunks


def _extract_grounding_urls(response: Any) -> list[str]:
    """Collect public image URLs from Search Grounding metadata and response text."""
    if response is None:
        return []
    raw: list[Any] = []
    for chunk in _iter_grounding_chunks(response):
        web = getattr(chunk, "web", None)
        if web is None and isinstance(chunk, dict):
            web = chunk.get("web") or chunk.get("retrieved_context")
        uri = getattr(web, "uri", None) if web is not None else None
        if uri is None and isinstance(web, dict):
            uri = web.get("uri") or web.get("url")
        if uri:
            raw.append(uri)
        image = getattr(chunk, "image", None)
        if image is None and isinstance(chunk, dict):
            image = chunk.get("image")
        image_uri = getattr(image, "uri", None) if image is not None else None
        if image_uri is None and isinstance(image, dict):
            image_uri = image.get("uri") or image.get("url")
        if image_uri:
            raw.append(image_uri)
    text = _response_text(response)
    if text:
        raw.extend(re.findall(r"https://[^\s\"'<>\\]+", text))
    return collect_image_urls(raw)


def _extract_grounding_page_urls(response: Any) -> list[str]:
    """Official product / roaster pages from Search Grounding (not image CDNs)."""
    if response is None:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for chunk in _iter_grounding_chunks(response):
        web = getattr(chunk, "web", None)
        if web is None and isinstance(chunk, dict):
            web = chunk.get("web") or chunk.get("retrieved_context")
        uri = getattr(web, "uri", None) if web is not None else None
        if uri is None and isinstance(web, dict):
            uri = web.get("uri") or web.get("url")
        clean = sanitize_roaster_url(uri)
        if clean and clean not in seen:
            seen.add(clean)
            found.append(clean)
    return found


def _with_grounded_images(data: dict[str, Any], response: Any | None = None) -> dict[str, Any]:
    out = dict(data or {})
    urls = collect_image_urls(
        out.get("image_candidates"),
        out.get("product_image_urls"),
        out.get("product_image_url"),
        out.get("official_image_url"),
        _extract_grounding_urls(response) if response is not None else [],
    )
    out["image_candidates"] = urls[:MAX_IMAGE_CANDIDATES]
    if urls:
        out["product_image_url"] = urls[0]
        out["product_image_urls"] = urls[:MAX_IMAGE_CANDIDATES]
        out.setdefault("official_image_url", urls[0])
    return out


def pad_image_candidates(candidates: list[str] | None, snapshot_url: str = "") -> list[str]:
    """Keep unique studio URLs, then fill remaining slots with the camera snapshot."""
    out: list[str] = []
    seen: set[str] = set()
    for url in list(candidates or []):
        clean = str(url or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= MAX_IMAGE_CANDIDATES:
            return out
    snap = str(snapshot_url or "").strip()
    if snap and snap not in seen and len(out) < MAX_IMAGE_CANDIDATES:
        out.append(snap)
    return out


def _scan_with_genai(image: Image.Image, key: str, prompt: str) -> dict[str, Any] | None:
    from google import genai
    from google.genai import types

    # Vision OCR must stay JSON-only. Search grounding on this call returns
    # thought/search parts instead of JSON and 422s the whole scan.
    client = genai.Client(api_key=key, http_options={"timeout": 45_000})
    image_part = _gemini_image_part(image)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.0,
    )
    last_error: Exception | None = None
    quota_hit = False
    for model_name in GEMINI_MODELS:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[prompt, image_part],
                    config=config,
                )
                data = _parse_gemini_json(_response_text(response))
                return _with_grounded_images(data, response)
            except Exception as exc:
                last_error = exc
                if _quota_gemini_error(exc):
                    quota_hit = True
                    break
                if "404" in str(exc) or "NOT_FOUND" in str(exc):
                    break
                if _transient_gemini_error(exc) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
    if quota_hit:
        raise RuntimeError("429 RESOURCE_EXHAUSTED")
    if last_error:
        raise last_error
    return None


def _scan_with_generativeai(image: Image.Image, key: str, prompt: str) -> dict[str, Any] | None:
    import google.generativeai as genai

    genai.configure(api_key=key)
    image_blob = {"mime_type": "image/jpeg", "data": _image_jpeg_bytes(image)}
    last_error: Exception | None = None
    for model_name in GEMINI_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                [prompt, image_blob],
                generation_config={"response_mime_type": "application/json", "temperature": 0},
            )
            data = _parse_gemini_json(_response_text(response) or getattr(response, "text", None) or "")
            return _with_grounded_images(data, response)
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return None


def verify_gemini_connection(model_name: str = GEMINI_STABLE_MODEL) -> dict[str, Any]:
    """Ping Gemini with the configured key. Never logs or returns the secret."""
    key = get_gemini_api_key()
    if not key:
        return {"ok": False, "model": model_name, "error": "GEMINI_API_KEY missing"}
    last_error = ""
    try:
        from google import genai

        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model=model_name,
            contents="Reply with the single word OK.",
        )
        text = (_response_text(response) or "").strip()
        candidates = list(getattr(response, "candidates", None) or [])
        if text or candidates:
            return {"ok": True, "model": model_name, "sdk": "google.genai"}
        last_error = "empty Gemini response"
    except Exception as exc:
        last_error = str(exc)
    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        response = genai.GenerativeModel(model_name).generate_content("Reply with the single word OK.")
        text = (getattr(response, "text", None) or "").strip()
        if text or getattr(response, "candidates", None):
            return {"ok": True, "model": model_name, "sdk": "google.generativeai"}
        last_error = last_error or "empty Gemini response"
    except Exception as exc:
        if "No module named" not in str(exc) or not last_error:
            last_error = str(exc)
    return {"ok": False, "model": model_name, "error": last_error}


def _attach_grounded_pages(data: dict[str, Any], response: Any | None) -> dict[str, Any]:
    out = _with_grounded_images(data if isinstance(data, dict) else {}, response)
    pages = _extract_grounding_page_urls(response)
    if pages and not sanitize_roaster_url(out.get("product_page_url")):
        out["product_page_url"] = pages[0]
    if pages and not sanitize_roaster_url(out.get("roaster_url")):
        out["roaster_url"] = pages[0]
    return out


def _gemini_official_product_lookup(
    name: str,
    roaster: str,
    key: str,
    lang: str = "da",
) -> dict[str, Any]:
    """Google-grounded official product page → BeanNote metadata JSON."""
    from google import genai
    from google.genai import types

    prompt = _grounded_product_prompt(name, roaster, lang)
    client = genai.Client(api_key=key, http_options={"timeout": 35_000})
    tools = _google_search_tools(types)
    if not tools:
        return {}
    for model_name in GEMINI_MODELS:
        config = types.GenerateContentConfig(temperature=0.1, tools=tools)
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            try:
                data = _parse_gemini_json(_response_text(response))
            except (ValueError, json.JSONDecodeError):
                data = {}
            return _attach_grounded_pages(data if isinstance(data, dict) else {}, response)
        except Exception as exc:
            if "404" in str(exc) or "NOT_FOUND" in str(exc):
                continue
            if _tools_unsupported(exc):
                return {}
            if _transient_gemini_error(exc):
                continue
            break
    return {}


def _lookup_official_with_generativeai(
    name: str,
    roaster: str,
    key: str,
    lang: str = "da",
) -> dict[str, Any]:
    import google.generativeai as genai

    prompt = _grounded_product_prompt(name, roaster, lang)
    genai.configure(api_key=key)
    tools = _legacy_google_search_tools()
    for model_name in GEMINI_MODELS:
        try:
            kwargs: dict[str, Any] = {}
            if tools:
                kwargs["tools"] = tools
            model = genai.GenerativeModel(model_name, **kwargs)
            response = model.generate_content(prompt, generation_config={"temperature": 0.1})
            try:
                data = _parse_gemini_json(_response_text(response) or getattr(response, "text", None) or "")
            except (ValueError, json.JSONDecodeError):
                data = {}
            return _attach_grounded_pages(data if isinstance(data, dict) else {}, response)
        except Exception as exc:
            if tools and _tools_unsupported(exc):
                tools = None
                continue
            if "404" in str(exc) or "NOT_FOUND" in str(exc):
                continue
            break
    return {}


def enrich_scan_with_official_page(optical: dict[str, Any], lang: str = "da") -> dict[str, Any]:
    """Step 2–3: grounded official-page lookup, then merge onto optical fields."""
    bag = dict(optical or {})
    name = (bag.get("name") or bag.get("bean_name") or "").strip()
    roaster = (bag.get("roaster") or "").strip()
    if not name and not roaster:
        bag.setdefault("scan_enrichment", "optical")
        return bag
    key = get_gemini_api_key()
    if not key:
        bag.setdefault("scan_enrichment", "optical")
        return bag
    official: dict[str, Any] = {}
    try:
        official = _gemini_official_product_lookup(name, roaster, key, lang)
    except Exception:
        official = {}
    if _blank(official):
        try:
            official = _lookup_official_with_generativeai(name, roaster, key, lang)
        except Exception:
            official = {}
    return merge_optical_and_official(bag, official)


def scan_label_gemini(image_bytes: bytes, lang: str = "da") -> dict[str, Any] | None:
    """Optical Vision pass, then grounded official-page enrichment, then normalize."""
    key = get_gemini_api_key()
    if not key:
        return None
    image = _prepare_scan_image(image_bytes)
    prompt = _gemini_prompt(lang)
    data: dict[str, Any] | None = None
    errors: list[str] = []
    try:
        data = _scan_with_genai(image, key, prompt)
    except Exception as exc:
        errors.append(f"google.genai: {exc}")
        try:
            data = _scan_with_generativeai(image, key, prompt)
        except Exception as exc2:
            errors.append(f"google.generativeai: {exc2}")
    if not data:
        if errors:
            raise RuntimeError("; ".join(errors))
        return None
    data = enrich_scan_with_official_page(data, lang=lang)
    parsed = normalize_scan_fields(data, lang=lang)
    parsed["raw_text"] = json.dumps(data, ensure_ascii=False, indent=2)
    parsed["scan_source"] = "gemini"
    parsed["lang"] = _copy_lang(lang)
    parsed.setdefault("scan_enrichment", data.get("scan_enrichment") or "optical")
    return parsed


def configure_tesseract() -> str | None:
    """Prefer Homebrew paths on Mac local, then PATH / container install."""
    import pytesseract

    candidates = [
        os.getenv("TESSERACT_CMD", ""),
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
        shutil.which("tesseract") or "",
    ]
    for cmd in candidates:
        if cmd and Path(cmd).exists():
            pytesseract.pytesseract.tesseract_cmd = cmd
            return cmd
    return None


def extract_text(image_bytes: bytes) -> str:
    import pytesseract

    configure_tesseract()
    image = open_oriented_image(image_bytes)
    width, height = image.size
    shortest = min(width, height)
    if shortest and shortest < 900:
        scale = 900 / shortest
        image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    try:
        raw = pytesseract.image_to_string(image, lang="eng+dan")
    except Exception:
        raw = pytesseract.image_to_string(image, lang="eng")
    return _cleanup_ocr_text(raw)


def _cleanup_ocr_text(raw: str) -> str:
    text = raw.replace("\x0c", " ")
    text = re.sub(r"[|•·●]+", " ", text)
    text = re.sub(r"[^\w\s\-æøåäöüéèáíóúâê,/&'.]", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_label(text: str) -> dict[str, Any]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    blob = " ".join(lines)

    origin = _find_origin(lines, blob)
    process = _find_process(lines, blob)
    roast_level = _find_roast(lines, blob)
    roaster = _find_roaster(lines, blob)
    name = _find_bean_name(lines, roaster, origin)
    notes_text, flavors = _find_notes(blob)
    altitude = _value_after_label(
        lines, blob, ("altitude", "højde", "masl", "m.o.h", "højde over havet")
    )
    varietal = _value_after_label(
        lines, blob, ("variety", "varietal", "sort", "varietet", "cultivar")
    )
    roast_date = _value_after_label(
        lines, blob, ("roast date", "ristet", "ristningsdato", "ristedato", "roasted")
    )

    return {
        "name": name,
        "roaster": roaster,
        "origin": origin,
        "process": process,
        "roast_level": roast_level,
        "roaster_notes": notes_text,
        "flavor_notes": flavors,
        "story": "",
        "altitude": altitude,
        "varietal": varietal,
        "roast_date": roast_date,
        "roaster_url": sanitize_roaster_url(blob),
        "raw_text": text,
    }


def _collect_image_urls(*sources: Any) -> list[str]:
    return collect_image_urls(*sources)


def _gemini_product_image_search(name: str, roaster: str, key: str) -> list[str]:
    from google import genai
    from google.genai import types

    prompt = (
        "Use Google Search to find official high-resolution studio packshots of this coffee bag. "
        f'Roaster: "{roaster}". Product name: "{name}". '
        "Prefer clean retailer/roaster container graphics (Shopify, Cloudinary, "
        "official shop). Return up to 3 distinct product shots of the same bag or "
        "the roaster's official studio photography for that product line. "
        "Never return a blurry phone snapshot or marketplace screenshot. "
        "Return ONE JSON object only with keys "
        '{"image_candidates":["https://..."],"image_urls":["https://..."],"source":"..."}. '
        "Each URL must be a direct https image (jpg/png/webp), not an HTML page. "
        'If no real official image exists, return {"image_candidates":[],"image_urls":[],"source":""}. '
        "Do not invent URLs."
    )
    client = genai.Client(api_key=key, http_options={"timeout": 12_000})
    tools = _google_search_tools(types)
    for model_name in GEMINI_MODELS:
        use_tools = list(tools)
        config_kwargs: dict[str, Any] = {"temperature": 0.1}
        if use_tools:
            config_kwargs["tools"] = use_tools
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            try:
                data = _parse_gemini_json(_response_text(response))
            except (ValueError, json.JSONDecodeError):
                data = {}
            merged = _with_grounded_images(data if isinstance(data, dict) else {}, response)
            return _collect_image_urls(merged.get("image_candidates"), merged)
        except Exception as exc:
            if "404" in str(exc) or "NOT_FOUND" in str(exc):
                continue
            if use_tools and _tools_unsupported(exc):
                tools = []
                continue
            break
    return []


def find_official_bag_images(
    name: str,
    roaster: str,
    hint_urls: str | list[str] | None = None,
) -> list[str]:
    """Return up to 3 official high-res URLs from Gemini Search Grounding, then catalog."""
    collected = _collect_image_urls(hint_urls)
    if not collected and ((name or "").strip() or (roaster or "").strip()):
        key = get_gemini_api_key()
        if key:
            try:
                found = _gemini_product_image_search(name, roaster, key)
            except Exception:
                found = []
            collected = _collect_image_urls(collected, found)
    if len(collected) < MAX_IMAGE_CANDIDATES:
        collected = _collect_image_urls(collected, curated_packshot_urls(name, roaster))
    return collected[:MAX_IMAGE_CANDIDATES]


def find_official_bag_image(name: str, roaster: str, hint_url: str = "") -> str:
    """Ask Gemini (with web search when available) for a clean official bag photo URL."""
    found = find_official_bag_images(name, roaster, hint_url)
    return found[0] if found else ""


def attach_official_bag_image(parsed: dict[str, Any]) -> dict[str, Any]:
    out = dict(parsed)
    hints = _collect_image_urls(
        out.get("product_image_urls"),
        out.get("product_image_url"),
        out.get("official_image_url"),
        out.get("image_candidates"),
    )
    name = (out.get("name") or out.get("bean_name") or "").strip()
    roaster = (out.get("roaster") or "").strip()
    candidates = find_official_bag_images(name, roaster, hints)
    official = candidates[0] if candidates else ""
    out["official_image_url"] = official
    out["product_image_url"] = official
    out["image_candidates"] = candidates[:MAX_IMAGE_CANDIDATES]
    return out


def scan_label(image_bytes: bytes, lang: str = "da") -> dict[str, Any]:
    parsed: dict[str, Any] | None = None
    gemini_error: Exception | None = None
    if gemini_available():
        try:
            parsed = scan_label_gemini(image_bytes, lang=lang)
        except Exception as exc:
            gemini_error = exc
            print(f"gemini scan failed, falling back to tesseract: {exc}")
            parsed = None
        if parsed is None and gemini_error is None:
            gemini_error = RuntimeError("Gemini Vision scan failed")
    if parsed is None:
        raw = extract_text(image_bytes)
        parsed = normalize_scan_fields(parse_label(raw), lang=lang)
        parsed["scan_source"] = "tesseract"
        if gemini_error is not None:
            parsed["scan_fallback"] = (
                "gemini_quota" if _quota_gemini_error(gemini_error) else "gemini_fail"
            )
    parsed = attach_official_bag_image(parsed)
    similar = find_similar_beans(parsed["name"], parsed["roaster"]) if parsed["name"] else []
    parsed["similar"] = similar
    parsed["match_tier"] = classify_matches(similar)
    parsed["scan_match"] = similar[0] if similar else None
    parsed["scan_confidence"] = float((similar[0].get("confidence") if similar else 0) or 0)
    parsed["scan_action"] = scan_destination(similar)
    return parsed


def _pretty(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip(" -:.,")
    if not compact:
        return ""
    if compact.isupper() or compact.islower():
        small = {"the", "of", "and", "og", "&"}
        parts: list[str] = []
        for i, word in enumerate(compact.split()):
            low = word.lower()
            if i and low in small:
                parts.append(low)
            elif low == "est.":
                parts.append("Est.")
            else:
                parts.append(low[:1].upper() + low[1:])
        return " ".join(parts)
    return compact


def _value_after_label(lines: list[str], blob: str, labels: tuple[str, ...]) -> str:
    label_alt = "|".join(re.escape(label) for label in labels)
    for index, line in enumerate(lines):
        if re.fullmatch(rf"(?i)(?:{label_alt})\s*[:.\-]?\s*", line):
            nxt = lines[index + 1] if index + 1 < len(lines) else ""
            if nxt and not FIELD_LABELS.match(nxt):
                return nxt.strip()
            continue
        match = re.match(rf"(?i)(?:{label_alt})\s*[:.\-]?\s+(.+)", line)
        if match:
            return match.group(1).strip()
    match = re.search(
        rf"(?i)(?:{label_alt})\s*[:.\-]?\s+(.+?)(?=\s+(?:{_NEXT_FIELD})\b|$)",
        blob,
    )
    return match.group(1).strip() if match else ""


def _origin_lookup() -> dict[str, str]:
    return {origin.lower(): ORIGIN_CANON.get(origin.lower(), origin) for origin in ORIGINS}


def _origin_skip_tokens() -> set[str]:
    skip = {
        "crema", "geisha", "gesha", "yirgacheffe", "sedato", "posen",
        "roaster", "coffee", "specialty", "process", "origin", "natural",
        "washed", "vasket", "honey", "anaerobic", "citrus", "karamel",
        "forarbejdning", "oprindelse", "ristningsgrad", "mellemristet",
    }
    for aliases in (*PROCESS_MAP.values(), *ROAST_MAP.values(), *FLAVOR_ALIASES.values()):
        skip.update(alias.lower() for alias in aliases)
    for pattern, title in NAME_PRIORITY:
        skip.update(title.lower().split())
    return skip


def _countries_in(text: str) -> list[str]:
    if not text:
        return []
    hits: list[tuple[int, str]] = []
    seen: set[str] = set()
    lookup = _origin_lookup()

    for origin in sorted(ORIGINS, key=len, reverse=True):
        match = re.search(rf"\b{re.escape(origin)}\b", text, re.IGNORECASE)
        if not match:
            continue
        canon = lookup[origin.lower()]
        if canon not in seen:
            seen.add(canon)
            hits.append((match.start(), canon))

    skip = _origin_skip_tokens()
    for match in re.finditer(r"[A-Za-zÆØÅÄÖÜæøåäöü]{5,}", text):
        token = match.group(0).lower()
        if token in skip or token in lookup:
            continue
        close = difflib.get_close_matches(token, list(lookup), n=1, cutoff=ORIGIN_FUZZY_CUTOFF)
        if not close:
            continue
        canon = lookup[close[0]]
        if canon not in seen:
            seen.add(canon)
            hits.append((match.start(), canon))

    hits.sort(key=lambda item: item[0])
    return [name for _, name in hits]


def _find_origin(lines: list[str], blob: str) -> str:
    labeled = _value_after_label(lines, blob, ("oprindelse", "origin"))
    countries = _countries_in(labeled) if labeled else []
    for country in _countries_in(blob):
        if country not in countries:
            countries.append(country)
    return " / ".join(countries)


def _map_aliases(text: str, mapping: dict[str, list[str]]) -> str:
    lowered = text.lower()
    for label, aliases in mapping.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in aliases):
            return label
    return ""


def _without_notes(blob: str) -> str:
    match = NOTES_LEAD.search(blob)
    return blob[: match.start()] if match else blob


def _find_process(lines: list[str], blob: str) -> str:
    for line in lines:
        if PROCESS_LINE.match(line):
            hit = _map_aliases(line, PROCESS_MAP)
            if hit:
                return hit
    labeled = _value_after_label(lines, blob, ("forarbejdning", "process", "proces"))
    if labeled:
        hit = _map_aliases(labeled, PROCESS_MAP)
        if hit:
            return hit
    return _map_aliases(_without_notes(blob), PROCESS_MAP)


def _find_roast(lines: list[str], blob: str) -> str:
    labeled = _value_after_label(lines, blob, ("ristningsgrad", "roast level", "roast", "ristning"))
    if labeled:
        hit = _map_aliases(labeled, ROAST_MAP)
        if hit:
            return hit
    compounds = {
        "Medium": ["mellemristet", "mellemristede"],
        "Lys": ["lysristet", "lysristede"],
        "Mørk": ["mørkristet", "mørkriste", "mørkpistet", "morkristet"],
    }
    hit = _map_aliases(blob, compounds)
    if hit:
        return hit
    english = {"Medium": ["medium"], "Lys": ["light"], "Mørk": ["dark"]}
    return _map_aliases(_without_notes(blob), english)


def _is_year_est(line: str) -> bool:
    return bool(re.search(r"(?i)^est\.?\s*\d{0,4}$", line.strip()))


def _is_graphic_noise(line: str) -> bool:
    return bool(GRAPHIC_NOISE.match(line.strip()) or NOISE_LINE.match(line.strip()))


def _is_roaster_line(line: str) -> bool:
    if FIELD_LABELS.match(line) or _is_year_est(line) or _is_graphic_noise(line):
        return False
    return bool(ROASTER_MARKERS.search(line))


def _find_roaster(lines: list[str], blob: str) -> str:
    for known in KNOWN_ROASTERS:
        if re.search(rf"\b{re.escape(known)}\b", blob, re.IGNORECASE):
            if known.lower() == "coffee collective":
                return "The Coffee Collective"
            return known

    labeled = _value_after_label(lines, blob, ("roaster", "risteri", "kafferisteri", "mikroristeri"))
    if labeled and not FIELD_LABELS.match(labeled):
        return _pretty(labeled.split(",")[0])[:60]

    for index, line in enumerate(lines):
        if not _is_roaster_line(line):
            continue
        if _is_year_est(line):
            continue
        words = line.split()
        if len(words) == 1 and words[0].lower() in {"roaster", "coffee", "brew", "mikroristeri", "risteri"}:
            prev = lines[index - 1] if index else ""
            if prev and not FIELD_LABELS.match(prev) and not NOISE_LINE.match(prev):
                return _pretty(f"{prev} {line}")[:60]
            nxt = lines[index + 1] if index + 1 < len(lines) else ""
            if nxt and _is_roaster_line(nxt) and not _is_year_est(nxt):
                return _pretty(f"{line} {nxt}")[:60]
        cleaned = re.sub(r"(?i)\best\.?\s*\d{2,4}\b", "", line).strip(" -")
        if cleaned:
            return _pretty(cleaned)[:60]
    return ""


def _looks_like_spec(line: str) -> bool:
    if _countries_in(line) and len(line.split()) <= 5:
        return True
    if _map_aliases(line, PROCESS_MAP) and len(line.split()) <= 3:
        return True
    if _map_aliases(line, ROAST_MAP) and len(line.split()) <= 3:
        return True
    return False


def _priority_title(text: str) -> str:
    for pattern, title in NAME_PRIORITY:
        if re.search(rf"\b(?:{pattern})\b", text, re.IGNORECASE):
            return title
    return ""


def _find_bean_name(lines: list[str], roaster: str, origin: str) -> str:
    skip = {roaster.lower(), origin.lower(), "coffee", "kaffe", "specialty"}
    for part in origin.split(" / "):
        if part:
            skip.add(part.lower())
    for known in KNOWN_ROASTERS:
        skip.add(known.lower())
    roaster_words = set(roaster.lower().split())

    mid = max(1, int(len(lines) * 0.70)) if lines else 0
    upper_mid = "\n".join(lines[:mid])
    joined = "\n".join(lines)
    priority = _priority_title(upper_mid) or _priority_title(joined)
    if (priority or "").lower() in NAME_SKIP_TITLES:
        priority = ""
    if priority:
        return priority

    for line in lines:
        lowered = line.lower()
        line_words = set(lowered.split())
        if lowered in skip or _is_roaster_line(line) or _is_year_est(line):
            continue
        if _is_graphic_noise(line):
            continue
        if roaster_words and line_words and line_words <= roaster_words:
            continue
        if FIELD_LABELS.match(line) or NOISE_LINE.match(line) or _looks_like_spec(line):
            continue
        if lowered.startswith(("www", "http", "net wt")):
            continue
        words = line.split()
        if 1 <= len(words) <= 4 and 3 <= len(line) <= 40:
            return _pretty(line)[:80]
    return ""


MAX_FLAVOR_WORDS = 2
MAX_FLAVOR_CHARS = 28


def is_short_flavor(tag: str) -> bool:
    compact = re.sub(r"\s+", " ", (tag or "").strip())
    if not compact or len(compact) > MAX_FLAVOR_CHARS:
        return False
    if re.search(r"[.!?;:/]", compact):
        return False
    return 1 <= len(compact.split()) <= MAX_FLAVOR_WORDS


def _canonical_flavor(token: str) -> str:
    lowered = re.sub(r"\s+", " ", (token or "").strip()).lower()
    if not lowered:
        return ""
    for option in FLAVOR_NOTES:
        if option.lower() == lowered:
            return option
    for option, names in FLAVOR_LOCALES.items():
        if lowered in {name.lower() for name in names.values()}:
            return option
    for option, aliases in FLAVOR_ALIASES.items():
        if lowered in {alias.lower() for alias in aliases}:
            return option
    return ""


def _dedupe_flavors(tags: list[str]) -> list[str]:
    """Keep official pills only; drop generics covered by a longer official tag."""
    order = {name: index for index, name in enumerate(FLAVOR_NOTES)}
    unique: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag not in order or tag in seen:
            continue
        seen.add(tag)
        unique.append(tag)
    kept = [
        tag
        for tag in unique
        if not any(tag != other and tag.lower() in other.lower() for other in unique)
    ]
    kept.sort(key=lambda name: order[name])
    return kept


def _match_flavors(text: str) -> list[str]:
    lowered = (text or "").lower()
    hits: list[str] = []
    consumed = lowered
    for option in sorted(FLAVOR_NOTES, key=len, reverse=True):
        aliases = sorted(FLAVOR_ALIASES.get(option, [option.lower()]), key=len, reverse=True)
        if any(re.search(rf"\b{re.escape(alias)}\b", consumed) for alias in aliases):
            hits.append(option)
            for alias in aliases:
                consumed = re.sub(rf"\b{re.escape(alias)}\b", " ", consumed)
    return _dedupe_flavors(hits)


def extract_flavor_canons(*sources: Any) -> list[str]:
    """Official flavor pills as canonical (Danish catalog) names."""
    hits: list[str] = []
    blobs: list[str] = []
    for source in _flatten_i18n_sources(*sources):
        if not source:
            continue
        if isinstance(source, (list, tuple)):
            for item in source:
                text = str(item).strip()
                if not text:
                    continue
                if not is_short_flavor(text) and len(text.split()) > MAX_FLAVOR_WORDS:
                    blobs.append(text)
                    continue
                canon = _canonical_flavor(text)
                if canon:
                    hits.append(canon)
                elif is_short_flavor(text):
                    hits.extend(_match_flavors(text))
                blobs.append(text)
            continue
        blobs.append(str(source))
    blob = " ".join(blobs)
    if blob:
        hits.extend(_match_flavors(blob))
    return [tag for tag in _dedupe_flavors(hits) if is_short_flavor(tag) and tag in FLAVOR_NOTES]


def extract_flavor_tags(*sources: Any, lang: str = "da") -> list[str]:
    """Official 1–2 word flavor pills only, localized to lang. Never sentence fragments."""
    return [localize_flavor(tag, lang) for tag in extract_flavor_canons(*sources)]


def compare_flavor_notes(
    roaster_notes: str,
    user_notes: str,
    extra_roaster: Any = None,
    lang: str = "da",
) -> dict[str, list[str]]:
    roaster = extract_flavor_tags(roaster_notes, extra_roaster, lang=lang)
    user = extract_flavor_tags(user_notes, lang=lang)
    overlap = [tag for tag in roaster if tag in user]
    return {"roaster": roaster, "user": user, "overlap": overlap}


def _flavor_source(blob: str, snippet: str) -> str:
    if snippet:
        return snippet
    stripped = blob
    for aliases in (*PROCESS_MAP.values(), *ROAST_MAP.values()):
        for alias in aliases:
            stripped = re.sub(rf"\b{re.escape(alias)}\b", " ", stripped, flags=re.IGNORECASE)
    return stripped


def _find_notes(blob: str) -> tuple[str, list[str]]:
    match = NOTES_LEAD.search(blob)
    snippet = ""
    if match:
        snippet = re.split(rf"(?i)\s+(?:{_NEXT_FIELD})\b", match.group(1), maxsplit=1)[0]
        snippet = re.sub(r"\s+", " ", snippet).strip(" -:.,")[:180]
    flavors = extract_flavor_tags(_flavor_source(blob, snippet))
    if not snippet and flavors:
        snippet = ", ".join(flavors)
    return snippet, flavors


def _gear_lookup_prompt(query: str, kind: str = "", lang: str = "da") -> str:
    language = STORY_LANG.get(normalize_lang(lang), "Danish")
    slot = (kind or "auto").strip().lower()
    if slot in {"espresso_machine", "machine"}:
        slot = "machine"
    elif slot not in {"grinder", "brewer", "other", "auto"}:
        slot = "auto"
    return f"""You are a coffee equipment catalog specialist. Use Google Search to find current official models and studio product photos.

Query: {query}
Preferred gear_type: {slot}

If the query is a brand or partial brand (e.g. "Profitec", "Mahlkönig", "DF"), return up to 4 popular current models from that brand.
If the query is a specific model, return that model first, plus up to 3 closely related models from the same brand.

For each model, find an official manufacturer studio product photo — a direct https image URL (jpg/png/webp). Prefer official brand sites, authorized dealers, Shopify, Cloudinary, or manufacturer CDNs.

Write summary in {language}. Return ONLY JSON:

{{
  "gear_candidates": [
    {{
      "model_name": "canonical product name",
      "brand": "brand",
      "gear_type": "machine | grinder | brewer",
      "image_url": "https://official-studio-photo.jpg",
      "specs": {{
        "boiler": "Dual Boiler / Heat Exchanger / Thermoblock / n/a",
        "pid": true,
        "pump": "Rotary / Vibratory / n/a",
        "burrs": "Flat / Conical / n/a",
        "burr_size": "e.g. 64mm",
        "group": "e.g. E61"
      }},
      "highlights": ["Dual Boiler", "PID", "Rotary Pump"],
      "summary": "1-2 sentence equipment summary"
    }}
  ]
}}

gear_type must be exactly "machine", "grinder", or "brewer".
Never invent model names the brand does not make.
Never invent image URLs. If no official photo is found, use "".
If the query is a grinder brand, prefer burr size and burr type in highlights.
If the query is an espresso-machine brand, prefer boiler type, PID, and pump in highlights.
"""


def _attach_grounded_gear_images(candidates: list[dict[str, Any]], response: Any | None) -> list[dict[str, Any]]:
    extras = _extract_grounding_urls(response) if response is not None else []
    used = {str(card.get("image_url") or "") for card in candidates if card.get("image_url")}
    leftover = [url for url in extras if url not in used]
    idx = 0
    for card in candidates:
        if card.get("image_url"):
            continue
        if idx >= len(leftover):
            break
        card["image_url"] = leftover[idx]
        idx += 1
    return candidates


def _normalize_gear_lookup_list(data: Any, query: str, kind: str = "", response: Any | None = None) -> list[dict[str, Any]]:
    payload = data if isinstance(data, (dict, list)) else {}
    cards = normalize_gear_catalog(payload, query=query, kind=kind)
    return _attach_grounded_gear_images(cards, response)


def _gear_lookup_with_genai(query: str, kind: str, key: str, lang: str) -> list[dict[str, Any]]:
    from google import genai
    from google.genai import types

    prompt = _gear_lookup_prompt(query, kind, lang)
    client = genai.Client(api_key=key, http_options={"timeout": 28_000})
    tools = _google_search_tools(types)
    last_error: Exception | None = None
    for model_name in GEMINI_MODELS:
        use_tools = bool(tools)
        for _attempt in range(2):
            config_kwargs: dict[str, Any] = {"temperature": 0.1}
            if use_tools:
                config_kwargs["tools"] = tools
            else:
                config_kwargs["response_mime_type"] = "application/json"
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                data = _parse_gemini_json(_response_text(response))
                return _normalize_gear_lookup_list(data, query, kind, response)
            except Exception as exc:
                last_error = exc
                if use_tools:
                    use_tools = False
                    continue
                if "404" in str(exc) or "NOT_FOUND" in str(exc) or _transient_gemini_error(exc):
                    break
                break
        else:
            continue
    if last_error:
        raise last_error
    return []


def _gear_lookup_with_generativeai(query: str, kind: str, key: str, lang: str) -> list[dict[str, Any]]:
    import google.generativeai as genai

    prompt = _gear_lookup_prompt(query, kind, lang)
    genai.configure(api_key=key)
    tools = _legacy_google_search_tools()
    last_error: Exception | None = None
    for model_name in GEMINI_MODELS:
        try:
            kwargs: dict[str, Any] = {}
            if tools:
                kwargs["tools"] = tools
            model = genai.GenerativeModel(model_name, **kwargs)
            response = model.generate_content(prompt, generation_config={"temperature": 0.1})
            data = _parse_gemini_json(_response_text(response) or getattr(response, "text", None) or "")
            return _normalize_gear_lookup_list(data, query, kind, response)
        except Exception as exc:
            last_error = exc
            if tools and _tools_unsupported(exc):
                tools = None
                continue
            if "404" in str(exc) or "NOT_FOUND" in str(exc):
                continue
            break
    if last_error:
        raise last_error
    return []


def lookup_gear_catalog(query: str, kind: str = "", lang: str = "da") -> list[dict[str, Any]]:
    """Grounded Gemini catalog: up to 4 brand/model candidates with studio images."""
    q = " ".join((query or "").split())
    if len(q) < 2:
        raise ValueError("gear_query_required")
    slot = (kind or "").strip().lower()
    if slot in {"machine", "espresso"}:
        slot = "espresso_machine"
    if slot not in {"espresso_machine", "grinder", "brewer", "other"}:
        slot = ""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    key = get_gemini_api_key()
    if not key:
        raise RuntimeError("ocr_missing")
    data: list[dict[str, Any]] = []
    errors: list[str] = []

    def _run() -> list[dict[str, Any]]:
        try:
            return _gear_lookup_with_genai(q, slot, key, lang)
        except Exception as exc:
            errors.append(f"google.genai: {exc}")
            return _gear_lookup_with_generativeai(q, slot, key, lang)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            data = pool.submit(_run).result(timeout=40)
    except FuturesTimeout as exc:
        raise ValueError("gear_lookup_fail") from exc
    except Exception as exc:
        errors.append(str(exc))
    if not data:
        if errors:
            raise RuntimeError("; ".join(errors))
        raise ValueError("gear_lookup_fail")
    return data[:4]


def lookup_gear_specs(query: str, kind: str = "", lang: str = "da") -> dict[str, Any]:
    """First catalog hit — kept for callers that still expect a single card."""
    hits = lookup_gear_catalog(query, kind=kind, lang=lang)
    if not hits:
        raise ValueError("gear_lookup_fail")
    return hits[0]
