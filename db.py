"""BeanNote SQLite layer: dual-env paths, schema, fuzzy dedupe, queries."""

from __future__ import annotations

import csv
import difflib
import io
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse, urlunparse

import bcrypt

from translations import FALLBACK_LANG, SUPPORTED_LANGUAGES, normalize_lang

VERSION = "5.0.0"
_BREW_KEYS = ("recommended_method", "grind_size", "water_temp", "brew_ratio")
_ROASTER_URL_RE = re.compile(
    r"(https?://[^\s<>\"']+|www\.[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/[^\s<>\"']*)?)",
    re.IGNORECASE,
)
_BLOCKED_SITE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_IMAGE_PATH_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif")
EXACT_MATCH_CUTOFF = 0.90
NEAR_MATCH_CUTOFF = 0.70
SCAN_MATCH_CUTOFF = 0.85
FUZZY_CUTOFF = NEAR_MATCH_CUTOFF
BCRYPT_ROUNDS = 12

ENVIRONMENT = os.getenv("ENVIRONMENT", "local").strip().lower()
RESET_DB_ON_START = os.getenv("RESET_DB_ON_START", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
LOCAL_ADMIN_EMAILS = {
    "google_test_user@beannote.local",
    "apple_test_user@beannote.local",
}


def get_db_path() -> Path:
    if ENVIRONMENT == "production":
        path = Path(os.getenv("BEANNOTE_DB_PATH", "/app/data/beannote.db"))
    else:
        path = Path(os.getenv("BEANNOTE_DB_PATH", "./data/beannote.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sanitize_roaster_url(value: Any) -> str:
    """Keep public http(s) roaster homepages; extract from messy Gemini/OCR text."""
    raw = str(value or "").strip().strip("'\"")
    if not raw or raw.lower() in {"none", "null", "undefined", "unknown", "n/a", "-"}:
        return ""
    match = _ROASTER_URL_RE.search(raw)
    candidate = (match.group(1) if match else raw).rstrip(").,;]'\"")
    if candidate.lower().startswith("www."):
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    host = (parsed.hostname or "").lower()
    if not host or "." not in host:
        return ""
    if host in _BLOCKED_SITE_HOSTS or host.endswith(".local"):
        return ""
    path = parsed.path or ""
    if any(path.lower().endswith(suffix) for suffix in _IMAGE_PATH_SUFFIXES):
        return ""
    if path == "/":
        path = ""
    return urlunparse(("https", parsed.netloc.lower(), path, "", parsed.query, ""))


def _has_localized_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _maybe_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def _is_flat_brew(obj: Any) -> bool:
    return isinstance(obj, dict) and any(key in obj for key in _BREW_KEYS)


def _is_lang_map(obj: Any) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    if _is_flat_brew(obj) and not any(
        isinstance(val, dict) and _is_flat_brew(val) for val in obj.values()
    ):
        return False
    return True


def get_localized(json_obj: Any, active_lang: str | None, fallback_lang: str = FALLBACK_LANG) -> Any:
    """Pick a value from a language map. Falls back to fallbackLang, then any remaining key.

    Accepts a dict, a JSON string, a legacy plain string, or a legacy list.
    """
    parsed = _maybe_json(json_obj)
    if parsed is None:
        return "" if json_obj in (None, "") else json_obj
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return parsed
    if not _is_lang_map(parsed):
        return parsed
    lang = normalize_lang(active_lang, fallback_lang)
    fallback = normalize_lang(fallback_lang)
    for code in (lang, fallback, *SUPPORTED_LANGUAGES):
        value = parsed.get(code)
        if _has_localized_value(value):
            return value
    for value in parsed.values():
        if _has_localized_value(value):
            return value
    return ""


def coerce_text_map(value: Any, lang: str | None = None) -> dict[str, str]:
    parsed = _maybe_json(value)
    if isinstance(parsed, dict) and _is_lang_map(parsed):
        out: dict[str, str] = {}
        for key, item in parsed.items():
            text = str(item or "").strip()
            if text:
                out[str(key).lower().strip()] = text
        return out
    text = str(parsed or "").strip() if parsed is not None else ""
    if isinstance(parsed, dict):
        text = ""
    if not text:
        return {}
    return {normalize_lang(lang): text}


def coerce_list_map(value: Any, lang: str | None = None) -> dict[str, list[str]]:
    parsed = _maybe_json(value)
    if isinstance(parsed, dict) and _is_lang_map(parsed):
        out: dict[str, list[str]] = {}
        for key, item in parsed.items():
            if isinstance(item, list):
                tags = [str(tag).strip() for tag in item if str(tag).strip()]
            elif isinstance(item, str) and item.strip():
                tags = [part.strip() for part in item.split(",") if part.strip()]
            else:
                tags = []
            if tags:
                out[str(key).lower().strip()] = tags
        return _complete_flavor_map(out)
    if isinstance(parsed, list):
        tags = [str(item).strip() for item in parsed if str(item).strip()]
        return _complete_flavor_map({normalize_lang(lang): tags} if tags else {})
    if isinstance(parsed, str) and parsed.strip():
        tags = [part.strip() for part in parsed.split(",") if part.strip()]
        return _complete_flavor_map({normalize_lang(lang): tags} if tags else {})
    return {}


def coerce_brew_map(
    value: Any,
    extra: dict[str, Any] | None = None,
    lang: str | None = None,
) -> dict[str, dict[str, str]]:
    parsed = _maybe_json(value)
    extra = extra or {}
    if isinstance(parsed, dict) and any(
        isinstance(item, dict) and _is_flat_brew(item) for item in parsed.values()
    ):
        out: dict[str, dict[str, str]] = {}
        for key, item in parsed.items():
            if isinstance(item, dict):
                cleaned = _clean_brew_obj(item)
                if any(cleaned.values()):
                    out[str(key).lower().strip()] = cleaned
        return _complete_brew_map(out)
    flat = _clean_brew_obj(parsed if isinstance(parsed, dict) else {})
    if not any(flat.values()):
        flat = _clean_brew_obj(extra)
    if not any(flat.values()):
        return {}
    return _complete_brew_map({normalize_lang(lang): flat})


def _clean_brew_obj(obj: dict[str, Any]) -> dict[str, str]:
    return {key: str(obj.get(key) or "").strip() for key in _BREW_KEYS}


def _complete_flavor_map(raw: dict[str, list[str]]) -> dict[str, list[str]]:
    if not raw:
        return {}
    try:
        from ocr import flavor_tags_lang_map

        merged: list[str] = []
        for tags in raw.values():
            merged.extend(tags)
        completed = flavor_tags_lang_map(merged)
        if completed:
            for code, tags in raw.items():
                if code not in completed and tags:
                    completed[code] = tags
            return completed
    except Exception:
        pass
    out = dict(raw)
    seed = next((tags for tags in raw.values() if tags), [])
    for code in SUPPORTED_LANGUAGES:
        out.setdefault(code, list(seed))
    return {code: tags for code, tags in out.items() if tags}


def _complete_brew_map(raw: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    if not raw:
        return {}
    try:
        from ocr import brew_recommendation_lang_map

        completed = brew_recommendation_lang_map(raw)
        if completed:
            for code, brew in raw.items():
                if code not in completed and any(brew.values()):
                    completed[code] = brew
            return completed
    except Exception:
        pass
    out = dict(raw)
    seed = next((brew for brew in raw.values() if any(brew.values())), {})
    for code in SUPPORTED_LANGUAGES:
        out.setdefault(code, dict(seed))
    return {code: brew for code, brew in out.items() if any(brew.values())}


def _dump_lang_map(value: dict[str, Any]) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _story_blank(story: Any) -> bool:
    if isinstance(story, dict):
        return not any(str(item or "").strip() for item in story.values())
    text = get_localized(story, FALLBACK_LANG)
    return not str(text or "").strip()


def merge_text_map(existing: Any, incoming: Any, lang: str | None = None) -> dict[str, str]:
    merged = dict(coerce_text_map(existing, lang))
    for code, text in coerce_text_map(incoming, lang).items():
        if text and not (merged.get(code) or "").strip():
            merged[code] = text
    return merged


def merge_list_map(existing: Any, incoming: Any, lang: str | None = None) -> dict[str, list[str]]:
    current = coerce_list_map(existing, lang)
    extra = coerce_list_map(incoming, lang)
    if current:
        for code, tags in extra.items():
            if tags and not current.get(code):
                current[code] = tags
        return current
    return extra


def merge_brew_map(existing: Any, incoming: Any, lang: str | None = None) -> dict[str, dict[str, str]]:
    current = coerce_brew_map(existing, lang=lang)
    extra = coerce_brew_map(incoming, lang=lang)
    if current:
        for code, brew in extra.items():
            if any(brew.values()) and not any((current.get(code) or {}).values()):
                current[code] = brew
        return current
    return extra


def should_auto_flush() -> bool:
    """Wipe the local/test SQLite file on startup. Production on Unraid is never flushed."""
    if ENVIRONMENT == "production":
        return False
    return ENVIRONMENT == "local" or RESET_DB_ON_START


def _flush_local_db() -> None:
    """Delete the SQLite file, WAL sidecars, and stored bag photos."""
    path = get_db_path()
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            if candidate.exists():
                candidate.unlink()
        except OSError:
            pass
    images = path.parent / "images"
    if images.is_dir():
        for child in images.iterdir():
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass


def _wipe_all_tables(conn: sqlite3.Connection) -> None:
    """Empty every application table so local test runs start clean."""
    conn.execute("PRAGMA foreign_keys = OFF")
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("PRAGMA foreign_keys = ON")


def init_db() -> None:
    if should_auto_flush():
        _flush_local_db()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT DEFAULT '',
                password_hash TEXT DEFAULT '',
                auth_provider TEXT NOT NULL DEFAULT 'email',
                oauth_id TEXT DEFAULT '',
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS beans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                roaster TEXT NOT NULL,
                origin TEXT DEFAULT '',
                process TEXT DEFAULT '',
                roast_level TEXT DEFAULT '',
                roaster_notes TEXT DEFAULT '',
                flavor_tags TEXT DEFAULT '{}',
                suitable_for TEXT DEFAULT '[]',
                story TEXT DEFAULT '{}',
                image_url TEXT DEFAULT '',
                roaster_url TEXT DEFAULT '',
                community_acidity REAL DEFAULT 3.4,
                community_sweetness REAL DEFAULT 3.5,
                community_body REAL DEFAULT 3.3,
                community_aftertaste REAL DEFAULT 3.4,
                acidity_score INTEGER,
                body_score INTEGER,
                roast_level_score INTEGER,
                recommended_method TEXT DEFAULT '',
                grind_size TEXT DEFAULT '',
                water_temp TEXT DEFAULT '',
                brew_ratio TEXT DEFAULT '',
                brew_recommendation TEXT DEFAULT '{}',
                roast_date TEXT DEFAULT '',
                altitude TEXT DEFAULT '',
                varietal TEXT DEFAULT '',
                latitude REAL,
                longitude REAL,
                region_full TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE (name, roaster) ON CONFLICT IGNORE
            );

            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bean_id INTEGER NOT NULL,
                user_id INTEGER,
                brew_method TEXT DEFAULT '',
                rating REAL NOT NULL,
                acidity REAL DEFAULT 3.0,
                sweetness REAL DEFAULT 3.0,
                body REAL DEFAULT 3.0,
                aftertaste REAL DEFAULT 3.0,
                notes TEXT DEFAULT '',
                grind_setting TEXT DEFAULT '',
                coffee_grams REAL,
                water_grams REAL,
                brew_time TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (bean_id) REFERENCES beans(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER NOT NULL,
                bean_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, bean_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (bean_id) REFERENCES beans(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_beans_origin ON beans(origin);
            CREATE INDEX IF NOT EXISTS idx_beans_roast ON beans(roast_level);
            CREATE INDEX IF NOT EXISTS idx_ratings_bean ON ratings(bean_id);
            CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id);
            CREATE INDEX IF NOT EXISTS idx_users_oauth ON users(auth_provider, oauth_id);
            CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);
            """
        )
        _ensure_columns(conn)
        _migrate_localized_json(conn)
        if should_auto_flush():
            _wipe_all_tables(conn)
    get_images_dir()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    beans = {row[1] for row in conn.execute("PRAGMA table_info(beans)")}
    if "image_url" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN image_url TEXT DEFAULT ''")
    if "roaster_url" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN roaster_url TEXT DEFAULT ''")
    if "story" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN story TEXT DEFAULT ''")
    if "suitable_for" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN suitable_for TEXT DEFAULT '[]'")
    if "brew_recommendation" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN brew_recommendation TEXT DEFAULT '{}'")
    for column in (
        "recommended_method",
        "grind_size",
        "water_temp",
        "brew_ratio",
        "roast_date",
        "altitude",
        "varietal",
        "region_full",
    ):
        if column not in beans:
            conn.execute(f"ALTER TABLE beans ADD COLUMN {column} TEXT DEFAULT ''")
    for column, decl in (("latitude", "REAL"), ("longitude", "REAL")):
        if column not in beans:
            conn.execute(f"ALTER TABLE beans ADD COLUMN {column} {decl}")
    for column in ("acidity_score", "body_score", "roast_level_score"):
        if column not in beans:
            conn.execute(f"ALTER TABLE beans ADD COLUMN {column} INTEGER")
    users = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "is_admin" not in users:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    ratings = {row[1] for row in conn.execute("PRAGMA table_info(ratings)")}
    if "user_id" not in ratings:
        conn.execute("ALTER TABLE ratings ADD COLUMN user_id INTEGER")
    for column in ("grind_setting", "brew_time"):
        if column not in ratings:
            conn.execute(f"ALTER TABLE ratings ADD COLUMN {column} TEXT DEFAULT ''")
    for column in ("coffee_grams", "water_grams"):
        if column not in ratings:
            conn.execute(f"ALTER TABLE ratings ADD COLUMN {column} REAL")


def _migrate_localized_json(conn: sqlite3.Connection) -> None:
    """Rewrite legacy plain-string story / array flavor_tags / flat brew columns into lang maps."""
    rows = conn.execute(
        """
        SELECT id, story, flavor_tags, brew_recommendation, recommended_method,
               grind_size, water_temp, brew_ratio
        FROM beans
        """
    ).fetchall()
    for row in rows:
        story_map = coerce_text_map(row["story"])
        flavor_map = coerce_list_map(row["flavor_tags"])
        brew_map = coerce_brew_map(
            row["brew_recommendation"],
            {
                "recommended_method": row["recommended_method"],
                "grind_size": row["grind_size"],
                "water_temp": row["water_temp"],
                "brew_ratio": row["brew_ratio"],
            },
        )
        conn.execute(
            """
            UPDATE beans
            SET story = ?, flavor_tags = ?, brew_recommendation = ?
            WHERE id = ?
            """,
            (
                _dump_lang_map(story_map),
                _dump_lang_map(flavor_map),
                _dump_lang_map(brew_map),
                row["id"],
            ),
        )


def get_images_dir() -> Path:
    path = get_db_path().parent / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_bean_image(image_bytes: bytes, filename: str = "") -> str:
    """Persist a snapped/uploaded bag photo next to the DB; return a relative path."""
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}:
        suffix = ".jpg"
    if suffix == ".jpeg":
        suffix = ".jpg"
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{os.urandom(3).hex()}{suffix}"
    dest = get_images_dir() / name
    dest.write_bytes(image_bytes)
    return f"images/{name}"


def resolve_image_path(image_url: str) -> Path | None:
    if not (image_url or "").strip():
        return None
    raw = Path(image_url)
    if raw.is_file():
        return raw
    candidate = get_db_path().parent / image_url
    return candidate if candidate.is_file() else None


def update_bean_image(bean_id: int, image_url: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE beans SET image_url = ? WHERE id = ?", (image_url, bean_id))


def update_bean_roaster_url(bean_id: int, roaster_url: str) -> None:
    clean = sanitize_roaster_url(roaster_url)
    if not clean:
        return
    with connect() as conn:
        conn.execute("UPDATE beans SET roaster_url = ? WHERE id = ?", (clean, bean_id))


def update_bean_story(bean_id: int, story: Any, lang: str | None = None) -> None:
    incoming = coerce_text_map(story, lang)
    if not incoming:
        return
    with connect() as conn:
        row = conn.execute("SELECT story FROM beans WHERE id = ?", (bean_id,)).fetchone()
        if row is None:
            return
        merged = merge_text_map(row["story"], incoming, lang)
        conn.execute(
            "UPDATE beans SET story = ? WHERE id = ?",
            (_dump_lang_map(merged), bean_id),
        )


ORIGIN_COORDS: dict[str, tuple[float, float, str]] = {
    "yirgacheffe": (6.1624, 38.2070, "Yirgacheffe, Ethiopia"),
    "sidamo": (6.6167, 38.4167, "Sidamo, Ethiopia"),
    "sidama": (6.6167, 38.4167, "Sidama, Ethiopia"),
    "guji": (5.7333, 39.2500, "Guji, Ethiopia"),
    "harrar": (9.3111, 42.1258, "Harrar, Ethiopia"),
    "limu": (8.1500, 36.3500, "Limu, Ethiopia"),
    "ethiopia": (9.1450, 38.7667, "Ethiopia"),
    "etiopien": (9.1450, 38.7667, "Ethiopia"),
    "huila": (2.5359, -75.5277, "Huila, Colombia"),
    "narino": (1.2892, -77.3579, "Nariño, Colombia"),
    "nariño": (1.2892, -77.3579, "Nariño, Colombia"),
    "antioquia": (6.2442, -75.5812, "Antioquia, Colombia"),
    "cauca": (2.4448, -76.6147, "Cauca, Colombia"),
    "colombia": (4.5709, -74.2973, "Colombia"),
    "nyeri": (-0.4167, 36.9500, "Nyeri, Kenya"),
    "kirinyaga": (-0.5000, 37.3000, "Kirinyaga, Kenya"),
    "kenya": (-0.0236, 37.9062, "Kenya"),
    "cerrado": (-16.6869, -49.2648, "Cerrado, Brazil"),
    "minas gerais": (-18.5122, -44.5550, "Minas Gerais, Brazil"),
    "sul de minas": (-21.2500, -45.0000, "Sul de Minas, Brazil"),
    "brazil": (-14.2350, -51.9253, "Brazil"),
    "brasilien": (-14.2350, -51.9253, "Brazil"),
    "antigua": (14.5611, -90.7295, "Antigua, Guatemala"),
    "huehuetenango": (15.3197, -91.4675, "Huehuetenango, Guatemala"),
    "guatemala": (15.7835, -90.2308, "Guatemala"),
    "tarrazu": (9.6500, -84.0000, "Tarrazú, Costa Rica"),
    "tarrazú": (9.6500, -84.0000, "Tarrazú, Costa Rica"),
    "costa rica": (9.7489, -83.7534, "Costa Rica"),
    "rwanda": (-1.9403, 29.8739, "Rwanda"),
    "burundi": (-3.3731, 29.9189, "Burundi"),
    "yemen": (15.5527, 48.5164, "Yemen"),
    "jemen": (15.5527, 48.5164, "Yemen"),
    "peru": (-9.1900, -75.0152, "Peru"),
    "honduras": (15.2000, -86.2419, "Honduras"),
    "el salvador": (13.7942, -88.8965, "El Salvador"),
    "panama": (8.5380, -80.7821, "Panama"),
    "boquete": (8.7800, -82.4300, "Boquete, Panama"),
    "indonesia": (-0.7893, 113.9213, "Indonesia"),
    "indonesien": (-0.7893, 113.9213, "Indonesia"),
    "sumatra": (0.5897, 101.3431, "Sumatra, Indonesia"),
    "java": (-7.6145, 110.7122, "Java, Indonesia"),
    "india": (20.5937, 78.9629, "India"),
    "indien": (20.5937, 78.9629, "India"),
    "mexico": (23.6345, -102.5528, "Mexico"),
    "tanzania": (-6.3690, 34.8888, "Tanzania"),
    "uganda": (1.3733, 32.2903, "Uganda"),
    "nicaragua": (12.2650, -85.2072, "Nicaragua"),
    "bolivia": (-16.2902, -63.5887, "Bolivia"),
}


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clamp_intensity_score(value: Any) -> int | None:
    """Keep Gemini/user flavor intensity on a closed 1–5 integer scale."""
    number = _as_float(value)
    if number is None:
        return None
    return int(min(5, max(1, round(number))))


_ROAST_SCORE_ALIASES = (
    (("medium-mørk", "medium-dark", "medium mørk", "medium dark", "mellemmørk"), 4),
    (("medium-lys", "medium-light", "medium lys", "medium light", "mellemlys"), 2),
    (("mørk", "dark", "mørkristet"), 5),
    (("lys", "light", "lysristet"), 1),
    (("medium", "mellem", "mellemristet"), 3),
)


def roast_level_to_score(roast_level: str) -> int | None:
    lowered = " ".join((roast_level or "").lower().split())
    if not lowered:
        return None
    for aliases, score in _ROAST_SCORE_ALIASES:
        if any(alias in lowered for alias in aliases):
            return score
    return None


def infer_intensity_scores(
    acidity_score: Any = None,
    body_score: Any = None,
    roast_level_score: Any = None,
    roast_level: str = "",
    origin: str = "",
    process: str = "",
    name: str = "",
) -> dict[str, int | None]:
    """Prefer explicit 1–5 scores; otherwise infer from roast, origin, and process."""
    roast = clamp_intensity_score(roast_level_score) or roast_level_to_score(roast_level)
    acidity = clamp_intensity_score(acidity_score)
    body = clamp_intensity_score(body_score)
    blob = f"{origin} {process} {name} {roast_level}".lower()
    african = any(token in blob for token in ("ethiopia", "etiopien", "kenya", "rwanda", "burundi"))
    washed = any(token in blob for token in ("vasket", "washed"))
    natural = any(token in blob for token in ("natural", "anaerob", "anaerobic", "honey"))
    espresso = any(token in blob for token in ("espresso", "mørk", "dark"))
    if roast is None:
        roast = 5 if espresso and not african else 3
    if acidity is None:
        if roast <= 2:
            acidity = 5 if african or washed else 4
        elif roast == 3:
            acidity = 4 if african else 3
        else:
            acidity = 2
    if body is None:
        if roast >= 4 or espresso:
            body = 5
        elif roast == 3:
            body = 4 if natural or espresso else 3
        else:
            body = 2 if washed and not natural else 3
    return {
        "acidity_score": acidity,
        "body_score": body,
        "roast_level_score": roast,
    }


def _rating_public(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    notes = (data.get("notes") or "").strip()
    data["notes"] = notes
    data["tasting_notes_user"] = notes
    data["grind_setting"] = (data.get("grind_setting") or "").strip()
    data["brew_time"] = (data.get("brew_time") or "").strip()
    data["brew_method"] = (data.get("brew_method") or "").strip()
    return data


def resolve_origin_geo(
    origin: str = "",
    region_full: str = "",
    latitude: Any = None,
    longitude: Any = None,
) -> tuple[float | None, float | None, str]:
    lat = _as_float(latitude)
    lng = _as_float(longitude)
    region = _normalize(region_full)
    if lat is not None and lng is not None:
        return lat, lng, region or _normalize(origin)
    blob = f"{region} {origin}".lower()
    for key, (clat, clng, label) in sorted(ORIGIN_COORDS.items(), key=lambda item: len(item[0]), reverse=True):
        if key in blob:
            return clat, clng, region or label
    return lat, lng, region or _normalize(origin)


def _normalize_meta_fields(
    roast_date: str = "",
    altitude: str = "",
    varietal: str = "",
    latitude: Any = None,
    longitude: Any = None,
    region_full: str = "",
    origin: str = "",
) -> dict[str, Any]:
    lat, lng, region = resolve_origin_geo(origin, region_full, latitude, longitude)
    return {
        "roast_date": (roast_date or "").strip(),
        "altitude": (altitude or "").strip(),
        "varietal": _normalize(varietal),
        "latitude": lat,
        "longitude": lng,
        "region_full": region,
    }


def _normalize_brew_fields(
    recommended_method: str = "",
    grind_size: str = "",
    water_temp: str = "",
    brew_ratio: str = "",
    brew_recommendation: dict[str, Any] | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    extra = {
        "recommended_method": recommended_method,
        "grind_size": grind_size,
        "water_temp": water_temp,
        "brew_ratio": brew_ratio,
    }
    brew_map = coerce_brew_map(brew_recommendation, extra, lang)
    flat = get_localized(brew_map, FALLBACK_LANG)
    if not isinstance(flat, dict) or not any(str(v or "").strip() for v in flat.values()):
        flat = get_localized(brew_map, "da")
    if not isinstance(flat, dict):
        flat = extra
    return {
        "recommended_method": _normalize(flat.get("recommended_method") or extra["recommended_method"] or ""),
        "grind_size": _normalize(flat.get("grind_size") or extra["grind_size"] or ""),
        "water_temp": str(flat.get("water_temp") or extra["water_temp"] or "").strip(),
        "brew_ratio": str(flat.get("brew_ratio") or extra["brew_ratio"] or "").strip(),
        "brew_map": brew_map,
    }


def _apply_brew_if_empty(conn: sqlite3.Connection, bean: dict[str, Any], brew: dict[str, Any]) -> None:
    incoming_map = brew.get("brew_map") if isinstance(brew.get("brew_map"), dict) else coerce_brew_map(brew)
    if not bean or not incoming_map:
        return
    merged = merge_brew_map(bean.get("brew_recommendation"), incoming_map)
    existing = coerce_brew_map(bean.get("brew_recommendation"))
    if existing and merged == existing:
        return
    if existing and not incoming_map:
        return
    flat = get_localized(merged, FALLBACK_LANG)
    if not isinstance(flat, dict):
        flat = {key: brew.get(key) or "" for key in _BREW_KEYS}
    conn.execute(
        """
        UPDATE beans
        SET recommended_method = ?, grind_size = ?, water_temp = ?, brew_ratio = ?,
            brew_recommendation = ?
        WHERE id = ?
        """,
        (
            _normalize(flat.get("recommended_method") or brew.get("recommended_method") or ""),
            _normalize(flat.get("grind_size") or brew.get("grind_size") or ""),
            str(flat.get("water_temp") or brew.get("water_temp") or "").strip(),
            str(flat.get("brew_ratio") or brew.get("brew_ratio") or "").strip(),
            _dump_lang_map(merged),
            bean["id"],
        ),
    )
    bean.update({key: brew.get(key) or "" for key in _BREW_KEYS if brew.get(key)})
    bean["brew_recommendation"] = merged


def _apply_meta_if_empty(conn: sqlite3.Connection, bean: dict[str, Any], meta: dict[str, Any]) -> None:
    if not bean:
        return
    updates: dict[str, Any] = {}
    for key in ("roast_date", "altitude", "varietal", "region_full"):
        incoming = (meta.get(key) or "").strip()
        if incoming and not (bean.get(key) or "").strip():
            updates[key] = incoming
    if meta.get("latitude") is not None and bean.get("latitude") is None:
        updates["latitude"] = meta["latitude"]
    if meta.get("longitude") is not None and bean.get("longitude") is None:
        updates["longitude"] = meta["longitude"]
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(
        f"UPDATE beans SET {assignments} WHERE id = ?",
        (*updates.values(), bean["id"]),
    )
    bean.update(updates)


def _apply_scores_if_empty(
    conn: sqlite3.Connection,
    bean: dict[str, Any],
    scores: dict[str, Any],
) -> None:
    if not bean:
        return
    incoming = infer_intensity_scores(
        scores.get("acidity_score"),
        scores.get("body_score"),
        scores.get("roast_level_score"),
        scores.get("roast_level") or bean.get("roast_level") or "",
        scores.get("origin") or bean.get("origin") or "",
        scores.get("process") or bean.get("process") or "",
        scores.get("name") or bean.get("name") or "",
    )
    updates: dict[str, Any] = {}
    for key in ("acidity_score", "body_score", "roast_level_score"):
        if incoming.get(key) is not None and bean.get(key) in (None, ""):
            updates[key] = incoming[key]
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(
        f"UPDATE beans SET {assignments} WHERE id = ?",
        (*updates.values(), bean["id"]),
    )
    bean.update(updates)


def _apply_suitable_if_empty(
    conn: sqlite3.Connection, bean: dict[str, Any], tags: list[str]
) -> None:
    incoming = [str(item).strip() for item in (tags or []) if str(item).strip()]
    if not incoming or (bean.get("suitable_for") or []):
        return
    conn.execute(
        "UPDATE beans SET suitable_for = ? WHERE id = ?",
        (json.dumps(incoming), bean["id"]),
    )
    bean["suitable_for"] = incoming


def _row_to_bean(row: sqlite3.Row | None, is_favorite: bool = False) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    flavor_map = coerce_list_map(data.get("flavor_tags"))
    data["flavor_tags"] = flavor_map
    data["suitable_for"] = _parse_json_list(data.get("suitable_for"))
    story_map = coerce_text_map(data.get("story"))
    data["story"] = story_map
    brew_map = coerce_brew_map(
        data.get("brew_recommendation"),
        {
            "recommended_method": data.get("recommended_method") or "",
            "grind_size": data.get("grind_size") or "",
            "water_temp": data.get("water_temp") or "",
            "brew_ratio": data.get("brew_ratio") or "",
        },
    )
    flat = get_localized(brew_map, FALLBACK_LANG)
    if not isinstance(flat, dict):
        flat = {
            "recommended_method": (data.get("recommended_method") or "").strip(),
            "grind_size": (data.get("grind_size") or "").strip(),
            "water_temp": (data.get("water_temp") or "").strip(),
            "brew_ratio": (data.get("brew_ratio") or "").strip(),
        }
    data.update(_clean_brew_obj(flat))
    data["brew_recommendation"] = brew_map
    lat, lng, region = resolve_origin_geo(
        data.get("origin") or "",
        data.get("region_full") or "",
        data.get("latitude"),
        data.get("longitude"),
    )
    data["roast_date"] = (data.get("roast_date") or "").strip()
    data["altitude"] = (data.get("altitude") or "").strip()
    data["varietal"] = (data.get("varietal") or "").strip()
    data["roaster_url"] = sanitize_roaster_url(data.get("roaster_url"))
    data["latitude"] = lat
    data["longitude"] = lng
    data["region_full"] = region
    scores = infer_intensity_scores(
        data.get("acidity_score"),
        data.get("body_score"),
        data.get("roast_level_score"),
        data.get("roast_level") or "",
        data.get("origin") or "",
        data.get("process") or "",
        data.get("name") or "",
    )
    data.update(scores)
    favorite = data.pop("is_favorite", None)
    data["is_favorite"] = bool(is_favorite if favorite is None else favorite)
    return data


def _parse_json_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [part.strip() for part in text.split(",") if part.strip()]


def _normalize(value: str) -> str:
    return " ".join((value or "").split()).strip()


def _bean_label(bean: dict[str, Any]) -> str:
    return f"{bean.get('name', '')} - {bean.get('roaster', '')}".strip(" -")


def match_tier(confidence: float) -> str:
    """exact >= 90%, near 70–89%, otherwise new."""
    if confidence >= EXACT_MATCH_CUTOFF:
        return "exact"
    if confidence >= NEAR_MATCH_CUTOFF:
        return "near"
    return "new"


def classify_matches(similar: list[dict[str, Any]]) -> str:
    if not similar:
        return "new"
    return match_tier(float(similar[0].get("confidence") or 0))


def scan_destination(similar: list[dict[str, Any]]) -> str:
    """Camera-first: jump to Rate when the top match is at least 85%."""
    if similar and float(similar[0].get("confidence") or 0) >= SCAN_MATCH_CUTOFF:
        return "rate"
    return "add"


def find_similar_beans(
    name: str,
    roaster: str = "",
    cutoff: float = FUZZY_CUTOFF,
) -> list[dict[str, Any]]:
    """Fuzzy-match a candidate against existing beans via difflib.get_close_matches."""
    query = _normalize(f"{name} - {roaster}".strip(" -"))
    if not query:
        return []

    beans = list_beans()
    if not beans:
        return []

    labels = [_bean_label(b) for b in beans]
    name_only = [_normalize(b["name"]) for b in beans]
    pool = labels + name_only
    hits = difflib.get_close_matches(query, pool, n=5, cutoff=cutoff)
    name_hits = difflib.get_close_matches(_normalize(name), name_only, n=5, cutoff=cutoff)

    seen: set[int] = set()
    results: list[dict[str, Any]] = []
    for hit in hits + name_hits:
        for bean in beans:
            if bean["id"] in seen:
                continue
            label = _bean_label(bean)
            if hit in {label, _normalize(bean["name"])}:
                score = max(
                    difflib.SequenceMatcher(None, query.lower(), label.lower()).ratio(),
                    difflib.SequenceMatcher(
                        None, _normalize(name).lower(), bean["name"].lower()
                    ).ratio(),
                )
                if score >= cutoff:
                    results.append({
                        **bean,
                        "confidence": round(score, 3),
                        "tier": match_tier(score),
                    })
                    seen.add(bean["id"])
    results.sort(key=lambda b: b["confidence"], reverse=True)
    return results


def insert_bean(
    name: str,
    roaster: str,
    origin: str = "",
    process: str = "",
    roast_level: str = "",
    roaster_notes: str = "",
    flavor_tags: list[str] | dict[str, Any] | None = None,
    suitable_for: list[str] | None = None,
    skip_fuzzy: bool = False,
    image_url: str = "",
    roaster_url: str = "",
    story: str | dict[str, Any] = "",
    recommended_method: str = "",
    grind_size: str = "",
    water_temp: str = "",
    brew_ratio: str = "",
    brew_recommendation: dict[str, Any] | None = None,
    roast_date: str = "",
    altitude: str = "",
    varietal: str = "",
    latitude: Any = None,
    longitude: Any = None,
    region_full: str = "",
    acidity_score: Any = None,
    body_score: Any = None,
    roast_level_score: Any = None,
) -> dict[str, Any]:
    name = _normalize(name)
    roaster = _normalize(roaster)
    image_url = (image_url or "").strip()
    roaster_url = sanitize_roaster_url(roaster_url)
    story_map = coerce_text_map(story)
    flavor_map = coerce_list_map(flavor_tags if flavor_tags is not None else _tags_from_notes(roaster_notes))
    brew = _normalize_brew_fields(
        recommended_method,
        grind_size,
        water_temp,
        brew_ratio,
        brew_recommendation,
    )
    meta = _normalize_meta_fields(
        roast_date,
        altitude,
        varietal,
        latitude,
        longitude,
        region_full,
        origin,
    )
    scores = infer_intensity_scores(
        acidity_score,
        body_score,
        roast_level_score,
        roast_level,
        origin,
        process,
        name,
    )
    if not name or not roaster:
        raise ValueError("name_roaster_required")

    similar = find_similar_beans(name, roaster)
    exact = [row for row in similar if row.get("tier") == "exact"]
    if exact:
        bean = exact[0]
        if image_url and not (bean.get("image_url") or "").strip():
            update_bean_image(bean["id"], image_url)
            bean["image_url"] = image_url
        if roaster_url and not (bean.get("roaster_url") or "").strip():
            update_bean_roaster_url(bean["id"], roaster_url)
            bean["roaster_url"] = roaster_url
        if story_map:
            update_bean_story(bean["id"], story_map)
            bean["story"] = merge_text_map(bean.get("story"), story_map)
        if brew.get("brew_map") or any(brew.get(key) for key in _BREW_KEYS) or any(meta.values()) or suitable_for or any(scores.values()):
            with connect() as conn:
                if brew.get("brew_map") or any(brew.get(key) for key in _BREW_KEYS):
                    _apply_brew_if_empty(conn, bean, brew)
                _apply_meta_if_empty(conn, bean, meta)
                _apply_suitable_if_empty(conn, bean, suitable_for or [])
                _apply_scores_if_empty(conn, bean, {**scores, "roast_level": roast_level, "origin": origin, "process": process, "name": name})
        return {"status": "exact", "similar": exact, "bean": bean}
    if not skip_fuzzy and similar:
        return {"status": "fuzzy", "similar": similar}

    tags = _dump_lang_map(flavor_map)
    suitability = json.dumps(suitable_for or [])
    story_json = _dump_lang_map(story_map)
    brew_json = _dump_lang_map(brew.get("brew_map") or {})
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO beans (
                name, roaster, origin, process, roast_level, roaster_notes,
                flavor_tags, suitable_for, story, image_url, roaster_url, recommended_method, grind_size,
                water_temp, brew_ratio, brew_recommendation, roast_date, altitude, varietal,
                latitude, longitude, region_full, acidity_score, body_score, roast_level_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                roaster,
                _normalize(origin),
                _normalize(process),
                _normalize(roast_level),
                (roaster_notes or "").strip(),
                tags,
                suitability,
                story_json,
                image_url,
                roaster_url,
                brew["recommended_method"],
                brew["grind_size"],
                brew["water_temp"],
                brew["brew_ratio"],
                brew_json,
                meta["roast_date"],
                meta["altitude"],
                meta["varietal"],
                meta["latitude"],
                meta["longitude"],
                meta["region_full"],
                scores["acidity_score"],
                scores["body_score"],
                scores["roast_level_score"],
                _now(),
            ),
        )
        if cur.rowcount == 0:
            existing = conn.execute(
                "SELECT * FROM beans WHERE name = ? AND roaster = ?",
                (name, roaster),
            ).fetchone()
            bean = _row_to_bean(existing)
            if bean and image_url and not (bean.get("image_url") or "").strip():
                conn.execute(
                    "UPDATE beans SET image_url = ? WHERE id = ?",
                    (image_url, bean["id"]),
                )
                bean["image_url"] = image_url
            if bean and roaster_url and not (bean.get("roaster_url") or "").strip():
                conn.execute(
                    "UPDATE beans SET roaster_url = ? WHERE id = ?",
                    (roaster_url, bean["id"]),
                )
                bean["roaster_url"] = roaster_url
            if bean and story_map:
                merged_story = merge_text_map(bean.get("story"), story_map)
                conn.execute(
                    "UPDATE beans SET story = ? WHERE id = ?",
                    (_dump_lang_map(merged_story), bean["id"]),
                )
                bean["story"] = merged_story
            if bean:
                _apply_brew_if_empty(conn, bean, brew)
                _apply_meta_if_empty(conn, bean, meta)
                _apply_suitable_if_empty(conn, bean, suitable_for or [])
                _apply_scores_if_empty(
                    conn,
                    bean,
                    {**scores, "roast_level": roast_level, "origin": origin, "process": process, "name": name},
                )
            return {"status": "exists", "bean": bean}

        bean = conn.execute(
            "SELECT * FROM beans WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return {"status": "created", "bean": _row_to_bean(bean)}


def update_bean(
    bean_id: int,
    name: str,
    roaster: str,
    origin: str = "",
    process: str = "",
    roast_level: str = "",
    roaster_notes: str = "",
    flavor_tags: list[str] | dict[str, Any] | None = None,
    suitable_for: list[str] | None = None,
    story: str | dict[str, Any] = "",
    image_url: str = "",
    roaster_url: str = "",
    recommended_method: str = "",
    grind_size: str = "",
    water_temp: str = "",
    brew_ratio: str = "",
    brew_recommendation: dict[str, Any] | None = None,
    roast_date: str = "",
    altitude: str = "",
    varietal: str = "",
    latitude: Any = None,
    longitude: Any = None,
    region_full: str = "",
    acidity_score: Any = None,
    body_score: Any = None,
    roast_level_score: Any = None,
) -> dict[str, Any] | None:
    """Replace bean masterdata. Callers must enforce admin authorization."""
    existing = get_bean(bean_id)
    if not existing:
        return None
    name = _normalize(name) or existing.get("name") or ""
    roaster = _normalize(roaster) or existing.get("roaster") or ""
    if not name or not roaster:
        raise ValueError("name_roaster_required")
    brew = _normalize_brew_fields(
        recommended_method,
        grind_size,
        water_temp,
        brew_ratio,
        brew_recommendation,
    )
    meta = _normalize_meta_fields(
        roast_date,
        altitude,
        varietal,
        latitude,
        longitude,
        region_full,
        origin,
    )
    flavor_map = (
        coerce_list_map(flavor_tags)
        if flavor_tags is not None
        else coerce_list_map(existing.get("flavor_tags"))
    )
    suitability = (
        suitable_for if suitable_for is not None else existing.get("suitable_for") or []
    )
    image_url = (image_url or "").strip() or (existing.get("image_url") or "")
    roaster_url = sanitize_roaster_url(roaster_url) or (existing.get("roaster_url") or "")
    story_map = coerce_text_map(story) if story else coerce_text_map(existing.get("story"))
    brew_map = brew.get("brew_map") or coerce_brew_map(existing.get("brew_recommendation"))
    scores = infer_intensity_scores(
        acidity_score if acidity_score is not None else existing.get("acidity_score"),
        body_score if body_score is not None else existing.get("body_score"),
        roast_level_score if roast_level_score is not None else existing.get("roast_level_score"),
        roast_level or existing.get("roast_level") or "",
        origin or existing.get("origin") or "",
        process or existing.get("process") or "",
        name,
    )
    with connect() as conn:
        clash = conn.execute(
            "SELECT id FROM beans WHERE name = ? AND roaster = ? AND id != ?",
            (name, roaster, bean_id),
        ).fetchone()
        if clash:
            raise ValueError("name_roaster_taken")
        conn.execute(
            """
            UPDATE beans SET
                name = ?, roaster = ?, origin = ?, process = ?, roast_level = ?,
                roaster_notes = ?, flavor_tags = ?, suitable_for = ?, story = ?, image_url = ?,
                roaster_url = ?, recommended_method = ?, grind_size = ?, water_temp = ?, brew_ratio = ?,
                brew_recommendation = ?, roast_date = ?, altitude = ?, varietal = ?, latitude = ?,
                longitude = ?, region_full = ?, acidity_score = ?, body_score = ?, roast_level_score = ?
            WHERE id = ?
            """,
            (
                name,
                roaster,
                _normalize(origin),
                _normalize(process),
                _normalize(roast_level),
                (roaster_notes or "").strip(),
                _dump_lang_map(flavor_map),
                json.dumps(suitability),
                _dump_lang_map(story_map),
                image_url,
                roaster_url,
                brew["recommended_method"],
                brew["grind_size"],
                brew["water_temp"],
                brew["brew_ratio"],
                _dump_lang_map(brew_map),
                meta["roast_date"],
                meta["altitude"],
                meta["varietal"],
                meta["latitude"],
                meta["longitude"],
                meta["region_full"],
                scores["acidity_score"],
                scores["body_score"],
                scores["roast_level_score"],
                bean_id,
            ),
        )
    return get_bean(bean_id)


def get_bean(bean_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM beans WHERE id = ?", (bean_id,)).fetchone()
        favorite = False
        if user_id is not None and row is not None:
            favorite = bool(
                conn.execute(
                    "SELECT 1 FROM favorites WHERE user_id = ? AND bean_id = ?",
                    (user_id, bean_id),
                ).fetchone()
            )
    return _row_to_bean(row, is_favorite=favorite)


def is_favorite(user_id: int, bean_id: int) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND bean_id = ?",
            (user_id, bean_id),
        ).fetchone()
    return bool(row)


def toggle_favorite(user_id: int, bean_id: int) -> bool:
    with connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND bean_id = ?",
            (user_id, bean_id),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND bean_id = ?",
                (user_id, bean_id),
            )
            return False
        conn.execute(
            "INSERT INTO favorites (user_id, bean_id, created_at) VALUES (?, ?, ?)",
            (user_id, bean_id, _now()),
        )
        return True


def list_beans(
    search: str = "",
    origin: str = "",
    roast_level: str = "",
    min_rating: float = 0.0,
    user_id: int | None = None,
    favorites_only: bool = False,
) -> list[dict[str, Any]]:
    favorite_select = "0 AS is_favorite"
    favorite_join = ""
    params: list[Any] = []
    if user_id is not None:
        favorite_select = "CASE WHEN f.bean_id IS NOT NULL THEN 1 ELSE 0 END AS is_favorite"
        if favorites_only:
            favorite_join = "INNER JOIN favorites f ON f.bean_id = b.id AND f.user_id = ?"
        else:
            favorite_join = "LEFT JOIN favorites f ON f.bean_id = b.id AND f.user_id = ?"
        params.append(user_id)
    query = f"""
        SELECT b.*,
               AVG(r.rating) AS avg_rating,
               COUNT(r.id) AS rating_count,
               AVG(r.acidity) AS avg_acidity,
               AVG(r.sweetness) AS avg_sweetness,
               AVG(r.body) AS avg_body,
               AVG(r.aftertaste) AS avg_aftertaste,
               {favorite_select}
        FROM beans b
        LEFT JOIN ratings r ON r.bean_id = b.id
        {favorite_join}
        WHERE 1=1
    """
    if search:
        query += " AND (b.name LIKE ? OR b.roaster LIKE ? OR b.origin LIKE ? OR b.flavor_tags LIKE ? OR b.suitable_for LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like, like, like])
    if origin:
        query += " AND b.origin = ?"
        params.append(origin)
    if roast_level:
        query += " AND b.roast_level = ?"
        params.append(roast_level)
    query += " GROUP BY b.id"
    if min_rating > 0:
        query += " HAVING COALESCE(AVG(r.rating), 0) >= ?"
        params.append(min_rating)
    query += " ORDER BY b.created_at DESC, b.name ASC"

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_bean(r) for r in rows]


def distinct_values(column: str) -> list[str]:
    if column not in {"origin", "process", "roast_level", "roaster"}:
        raise ValueError("invalid_column")
    with connect() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM beans WHERE {column} != '' ORDER BY {column}"
        ).fetchall()
    return [r[0] for r in rows]


def _snap_half(value: float) -> float:
    snapped = round(float(value) * 2) / 2
    return min(5.0, max(0.5, snapped))


def insert_rating(
    bean_id: int,
    brew_method: str,
    rating: float,
    acidity: float,
    sweetness: float,
    body: float,
    aftertaste: float,
    notes: str = "",
    user_id: int | None = None,
    grind_setting: str = "",
    coffee_grams: float | None = None,
    water_grams: float | None = None,
    brew_time: str = "",
) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ratings (
                bean_id, user_id, brew_method, rating, acidity, sweetness, body,
                aftertaste, notes, grind_setting, coffee_grams, water_grams,
                brew_time, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bean_id,
                user_id,
                _normalize(brew_method),
                _snap_half(rating),
                _snap_half(acidity),
                _snap_half(sweetness),
                _snap_half(body),
                _snap_half(aftertaste),
                (notes or "").strip(),
                (grind_setting or "").strip(),
                _as_float(coffee_grams),
                _as_float(water_grams),
                (brew_time or "").strip(),
                _now(),
            ),
        )
        row = conn.execute(
            "SELECT * FROM ratings WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _rating_public(row) or {}


def list_ratings(bean_id: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT r.*, b.name AS bean_name, b.roaster
        FROM ratings r
        JOIN beans b ON b.id = r.bean_id
    """
    params: list[Any] = []
    if bean_id is not None:
        sql += " WHERE r.bean_id = ?"
        params.append(bean_id)
    sql += " ORDER BY r.created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_rating_public(r) for r in rows if r is not None]


def get_flavor_profile(bean_id: int, user_id: int | None = None) -> dict[str, Any]:
    bean = get_bean(bean_id, user_id=user_id)
    if not bean:
        return {}
    with connect() as conn:
        stats = conn.execute(
            """
            SELECT
                AVG(rating) AS avg_rating,
                COUNT(id) AS rating_count,
                AVG(acidity) AS acidity,
                AVG(sweetness) AS sweetness,
                AVG(body) AS body,
                AVG(aftertaste) AS aftertaste
            FROM ratings WHERE bean_id = ?
            """,
            (bean_id,),
        ).fetchone()
        if user_id is not None:
            latest = conn.execute(
                """
                SELECT * FROM ratings
                WHERE bean_id = ? AND user_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (bean_id, user_id),
            ).fetchone()
        else:
            latest = conn.execute(
                """
                SELECT * FROM ratings WHERE bean_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (bean_id,),
            ).fetchone()

    community = {
        "acidity": stats["acidity"] if stats["rating_count"] else bean["community_acidity"],
        "sweetness": stats["sweetness"] if stats["rating_count"] else bean["community_sweetness"],
        "body": stats["body"] if stats["rating_count"] else bean["community_body"],
        "aftertaste": stats["aftertaste"] if stats["rating_count"] else bean["community_aftertaste"],
        "avg_rating": stats["avg_rating"] or 0,
        "rating_count": stats["rating_count"] or 0,
    }
    user = _rating_public(latest) if latest else None
    if user:
        user["my_recipe"] = {
            "grind_setting": (user.get("grind_setting") or "").strip(),
            "coffee_grams": user.get("coffee_grams"),
            "water_grams": user.get("water_grams"),
            "brew_time": (user.get("brew_time") or "").strip(),
        }
    history = []
    if user_id is not None:
        history = [row for row in list_ratings(bean_id) if row.get("user_id") == user_id]
    return {"bean": bean, "community": community, "user": user, "history": history}


def export_ratings(fmt: str = "csv") -> tuple[str, str, bytes]:
    rows = list_ratings()
    stamp = datetime.now().strftime("%Y%m%d")
    if fmt == "json":
        payload = json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")
        return f"beannote-log-{stamp}.json", "application/json", payload

    buffer = io.StringIO()
    fields = [
        "id",
        "bean_name",
        "roaster",
        "brew_method",
        "rating",
        "acidity",
        "sweetness",
        "body",
        "aftertaste",
        "notes",
        "grind_setting",
        "coffee_grams",
        "water_grams",
        "brew_time",
        "created_at",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return f"beannote-log-{stamp}.csv", "text/csv", buffer.getvalue().encode("utf-8")


def _is_short_tag(tag: str) -> bool:
    compact = " ".join((tag or "").split())
    if not compact or len(compact) > 24:
        return False
    if any(mark in compact for mark in ".!?;:/"):
        return False
    lowered = compact.lower()
    if any(token in lowered.split() for token in {"and", "og", "with", "med", "the", "en", "et"}):
        return False
    return 1 <= len(compact.split()) <= 2


def _tags_from_notes(notes: str) -> list[str]:
    parts = [part.strip() for part in (notes or "").replace(";", ",").split(",") if part.strip()]
    return [part for part in parts if _is_short_tag(part)]


def matching_flavor_tags(roaster_notes: str, user_notes: str) -> dict[str, list[str]]:
    vocab = {
        "jasmine", "jasmin", "bergamot", "peach", "abrikos", "apricot",
        "citrus", "lemon", "lime", "orange", "grapefruit", "berry", "bær",
        "blueberry", "blåbær", "strawberry", "jordbær", "blackcurrant",
        "solbær", "apple", "æble", "stone fruit", "stenfrugt", "floral",
        "blomstret", "chocolate", "chokolade", "cocoa", "kakao", "caramel",
        "karamel", "hazelnut", "hassel", "nutty", "nøddet", "honey",
        "honning", "tropical", "tropisk", "wine", "vin", "tea", "te",
        "vanilla", "vanilje", "spice", "krydderi", "dried fruit",
        "tørret frugt",
    }
    def extract(text: str) -> list[str]:
        lowered = (text or "").lower()
        found = [term for term in vocab if _is_short_tag(term) and term in lowered]
        return sorted(set(found), key=len, reverse=True)

    roaster = extract(roaster_notes)
    user = extract(user_notes)
    overlap = [
        tag for tag in roaster
        if any(tag == u or tag in u or u in tag for u in user if len(u) > 2)
    ]
    return {"roaster": roaster[:12], "user": user[:12], "overlap": overlap[:12]}


def _is_bootstrap_admin(email: str) -> bool:
    return (email or "").strip().lower() in LOCAL_ADMIN_EMAILS


def _grant_admin(email: str) -> bool:
    if _is_bootstrap_admin(email):
        return True
    if ENVIRONMENT != "local":
        return False
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE COALESCE(is_admin, 0) = 1 LIMIT 1"
        ).fetchone()
    return row is None


def _public_user(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data.pop("password_hash", None)
    data["is_admin"] = bool(data.get("is_admin"))
    return data


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def get_user(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _public_user(row)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    email = _normalize(email).lower()
    if not email:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def get_user_by_oauth(provider: str, oauth_id: str) -> dict[str, Any] | None:
    provider = _normalize(provider).lower()
    oauth_id = (oauth_id or "").strip()
    if not provider or not oauth_id:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE auth_provider = ? AND oauth_id = ?",
            (provider, oauth_id),
        ).fetchone()
    return dict(row) if row else None


def create_email_user(email: str, password: str, username: str = "") -> dict[str, Any]:
    email = _normalize(email).lower()
    username = _normalize(username) or email.split("@")[0]
    if not email or "@" not in email:
        raise ValueError("invalid_email")
    if len(password) < 8:
        raise ValueError("password_too_short")
    if get_user_by_email(email):
        raise ValueError("email_taken")
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (
                email, username, password_hash, auth_provider, oauth_id, is_admin, created_at
            ) VALUES (?, ?, ?, 'email', '', ?, ?)
            """,
            (email, username, hash_password(password), int(_grant_admin(email)), _now()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    user = _public_user(row)
    if not user:
        raise ValueError("user_create_failed")
    return user


def authenticate_email(email: str, password: str) -> dict[str, Any] | None:
    row = get_user_by_email(email)
    if not row:
        return None
    if row.get("auth_provider") not in {"email", ""}:
        return None
    if not verify_password(password, row.get("password_hash") or ""):
        return None
    return _public_user(row)


def upsert_oauth_user(
    email: str,
    username: str,
    provider: str,
    oauth_id: str,
) -> dict[str, Any]:
    provider = _normalize(provider).lower()
    email = _normalize(email).lower()
    username = _normalize(username) or (email.split("@")[0] if email else provider)
    oauth_id = (oauth_id or "").strip()
    if provider not in {"google", "apple"} or not oauth_id:
        raise ValueError("invalid_oauth")
    if not email or "@" not in email:
        email = f"{provider}-{oauth_id[:24]}@oauth.beannote.local"

    existing = get_user_by_oauth(provider, oauth_id) or get_user_by_email(email)
    if existing:
        with connect() as conn:
            conn.execute(
                """
                UPDATE users
                SET username = CASE WHEN username = '' THEN ? ELSE username END,
                    auth_provider = ?,
                    oauth_id = ?,
                    is_admin = CASE WHEN ? = 1 THEN 1 ELSE is_admin END
                WHERE id = ?
                """,
                (
                    username,
                    provider,
                    oauth_id,
                    int(_is_bootstrap_admin(email)),
                    existing["id"],
                ),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (existing["id"],)).fetchone()
        user = _public_user(row)
        if not user:
            raise ValueError("oauth_update_failed")
        return user

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (
                email, username, password_hash, auth_provider, oauth_id, is_admin, created_at
            ) VALUES (?, ?, '', ?, ?, ?, ?)
            """,
            (email, username, provider, oauth_id, int(_grant_admin(email)), _now()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    user = _public_user(row)
    if not user:
        raise ValueError("oauth_create_failed")
    return user
