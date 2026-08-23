"""Coffee bag scanner: Gemini 1.5 Flash Vision with local Tesseract fallback."""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from db import classify_matches, find_similar_beans, scan_destination

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

GEMINI_MODELS = ("gemini-1.5-flash", "gemini-2.0-flash", "gemini-flash-latest")
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
NAME_PRIORITY = [
    (r"slow\s+roast", "Slow Roast"),
    (r"yirgacheffe", "Yirgacheffe"),
    (r"geisha|gesha", "Geisha"),
    (r"crema", "Crema"),
]

ORIGIN_FUZZY_CUTOFF = 0.80

PROCESS_LINE = re.compile(
    r"(?i)^\s*(natural|naturlig|washed|vasket|wet\s+process|fully\s+washed|"
    r"honey|pulped\s+natural|anaerobic|anaerob|carbonic|dry\s+process)\s*$",
)

NOTES_LEAD = re.compile(
    r"(?:noter\s+af|smag\s+af|tasting\s+notes?|smagsnoter|notes?)\s*[:.\-]?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

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

_NEXT_FIELD = (
    r"oprindelse|origin|forarbejdning|process|proces|ristningsgrad|"
    r"roast|ristning|noter|smag|tasting|variety|varietal"
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def load_local_env() -> None:
    """Load root .env into os.environ without overwriting existing vars."""
    env_path = _project_root() / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def ensure_local_env() -> Path:
    """Create an empty local .env if missing. Never writes a real API key."""
    env_path = _project_root() / ".env"
    if not env_path.exists():
        env_path.write_text(ENV_PLACEHOLDER, encoding="utf-8")
    return env_path


def get_gemini_api_key() -> str:
    load_local_env()
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if key:
        return key
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is not None:
            return str(secrets.get("GEMINI_API_KEY") or "").strip()
    except Exception:
        return ""
    return ""


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


def normalize_scan_fields(parsed: dict[str, Any]) -> dict[str, Any]:
    """Map Gemini/Tesseract fields onto the add-bean widget keys."""
    notes = (parsed.get("official_notes") or parsed.get("roaster_notes") or "").strip()
    name = (parsed.get("bean_name") or parsed.get("name") or "").strip()
    flavors = extract_flavor_tags(
        parsed.get("flavor_tags"),
        parsed.get("flavor_notes"),
        notes,
    )
    out = dict(parsed)
    out["name"] = name
    out["roaster"] = (parsed.get("roaster") or "").strip()
    out["origin"] = (parsed.get("origin") or "").strip()
    out["process"] = _canon_process(parsed.get("process") or "")
    out["roast_level"] = _canon_roast(parsed.get("roast_level") or "")
    out["roaster_notes"] = notes
    out["official_notes"] = notes
    out["flavor_notes"] = flavors
    out["flavor_tags"] = flavors
    out["story"] = (parsed.get("story") or "").strip()
    return out


def _gemini_prompt() -> str:
    flavors = ", ".join(f'"{name}"' for name in FLAVOR_NOTES)
    processes = ", ".join(f'"{name}"' for name in PROCESSES)
    roasts = ", ".join(f'"{name}"' for name in ROAST_LEVELS)
    return (
        "You are a specialty-coffee label reader for BeanNote. "
        "Inspect this coffee bag photo and return ONE JSON object only "
        "(no markdown) with these keys:\n"
        '- "roaster": roaster / brand name\n'
        '- "bean_name": coffee / lot name (not the roaster)\n'
        '- "origin": country and optional region, e.g. "Ethiopia, Yirgacheffe"\n'
        f'- "process": exactly one of [{processes}]\n'
        f'- "roast_level": exactly one of [{roasts}]\n'
        f'- "flavor_tags": array of 1–6 descriptors chosen only from [{flavors}]\n'
        '- "official_notes": raw tasting-notes text from the label\n'
        '- "story": 2–4 engaging Danish sentences ("Kaffens Historie"). '
        "Combine printed label facts (farm, region, altitude, varietals, process, "
        "flavor notes) with your specialty-coffee knowledge into a short "
        "background story. Include farm, region, altitude, varietals, or a "
        "flavor-characteristic narrative when known. Example tone: "
        '"Høstet i 1.900 meters højde i Yirgacheffe-regionen af småbønder, '
        'der selektivt håndplukker de mest modne bær..." '
        "Do not invent a specific farm or producer name unless the label shows it; "
        "you may use well-known regional context.\n"
        "Read printed text first. If a field is missing on the label, "
        "use your coffee knowledge (origin, process, typical roast and flavors "
        "for that lot or origin) to fill it. Prefer Danish process/roast labels. "
        "Never invent a roaster or bean_name if the label does not show them — "
        "use an empty string instead."
    )


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


def _parse_gemini_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Gemini response was not a JSON object")
    return data


def _prepare_scan_image(image_bytes: bytes) -> Image.Image:
    image = Image.open(BytesIO(image_bytes))
    if image.mode not in {"L", "RGB"}:
        image = image.convert("RGB")
    width, height = image.size
    longest = max(width, height)
    if longest > 1600:
        scale = 1600 / longest
        image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
    return image


def _scan_with_genai(image: Image.Image, key: str, prompt: str) -> dict[str, Any] | None:
    from google import genai

    client = genai.Client(api_key=key)
    last_error: Exception | None = None
    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[prompt, image],
                config={"response_mime_type": "application/json"},
            )
            return _parse_gemini_json(_response_text(response))
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return None


def _scan_with_generativeai(image: Image.Image, key: str, prompt: str) -> dict[str, Any] | None:
    import google.generativeai as genai

    genai.configure(api_key=key)
    last_error: Exception | None = None
    for model_name in GEMINI_MODELS:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                [prompt, image],
                generation_config={"response_mime_type": "application/json"},
            )
            return _parse_gemini_json(response.text or "")
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return None


def scan_label_gemini(image_bytes: bytes) -> dict[str, Any] | None:
    """Call Gemini 1.5 Flash Vision and return a normalized BeanNote field dict."""
    key = get_gemini_api_key()
    if not key:
        return None
    image = _prepare_scan_image(image_bytes)
    prompt = _gemini_prompt()
    data: dict[str, Any] | None = None
    try:
        data = _scan_with_genai(image, key, prompt)
    except Exception:
        try:
            data = _scan_with_generativeai(image, key, prompt)
        except Exception:
            return None
    if not data:
        return None
    parsed = normalize_scan_fields(data)
    parsed["raw_text"] = json.dumps(data, ensure_ascii=False, indent=2)
    parsed["scan_source"] = "gemini"
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
    image = Image.open(BytesIO(image_bytes))
    if image.mode not in {"L", "RGB"}:
        image = image.convert("RGB")
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

    return {
        "name": name,
        "roaster": roaster,
        "origin": origin,
        "process": process,
        "roast_level": roast_level,
        "roaster_notes": notes_text,
        "flavor_notes": flavors,
        "story": "",
        "raw_text": text,
    }


def scan_label(image_bytes: bytes) -> dict[str, Any]:
    parsed = scan_label_gemini(image_bytes)
    if not parsed or not ((parsed.get("name") or "").strip() or (parsed.get("roaster") or "").strip()):
        raw = extract_text(image_bytes)
        parsed = normalize_scan_fields(parse_label(raw))
        parsed["scan_source"] = parsed.get("scan_source") or "tesseract"
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
    priority = _priority_title(upper_mid) or _priority_title("\n".join(lines))
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


def extract_flavor_tags(*sources: str | list[str] | None) -> list[str]:
    """Official 1–2 word flavor pills only. Never returns sentence fragments."""
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


def compare_flavor_notes(
    roaster_notes: str,
    user_notes: str,
    extra_roaster: list[str] | None = None,
) -> dict[str, list[str]]:
    roaster = extract_flavor_tags(roaster_notes, extra_roaster)
    user = extract_flavor_tags(user_notes)
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
