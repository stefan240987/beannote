"""BeanNote SQLite layer: dual-env paths, schema, fuzzy dedupe, queries."""

from __future__ import annotations

import csv
import difflib
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import bcrypt

VERSION = "2.5.0"
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
                flavor_tags TEXT DEFAULT '[]',
                story TEXT DEFAULT '',
                image_url TEXT DEFAULT '',
                community_acidity REAL DEFAULT 3.4,
                community_sweetness REAL DEFAULT 3.5,
                community_body REAL DEFAULT 3.3,
                community_aftertaste REAL DEFAULT 3.4,
                recommended_method TEXT DEFAULT '',
                grind_size TEXT DEFAULT '',
                water_temp TEXT DEFAULT '',
                brew_ratio TEXT DEFAULT '',
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
        if should_auto_flush():
            _wipe_all_tables(conn)
    get_images_dir()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    beans = {row[1] for row in conn.execute("PRAGMA table_info(beans)")}
    if "image_url" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN image_url TEXT DEFAULT ''")
    if "story" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN story TEXT DEFAULT ''")
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
    ratings = {row[1] for row in conn.execute("PRAGMA table_info(ratings)")}
    if "user_id" not in ratings:
        conn.execute("ALTER TABLE ratings ADD COLUMN user_id INTEGER")
    for column in ("grind_setting", "brew_time"):
        if column not in ratings:
            conn.execute(f"ALTER TABLE ratings ADD COLUMN {column} TEXT DEFAULT ''")
    for column in ("coffee_grams", "water_grams"):
        if column not in ratings:
            conn.execute(f"ALTER TABLE ratings ADD COLUMN {column} REAL")


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


def update_bean_story(bean_id: int, story: str) -> None:
    story = (story or "").strip()
    if not story:
        return
    with connect() as conn:
        conn.execute(
            "UPDATE beans SET story = ? WHERE id = ? AND (story IS NULL OR story = '')",
            (story, bean_id),
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
) -> dict[str, str]:
    rec = brew_recommendation if isinstance(brew_recommendation, dict) else {}
    return {
        "recommended_method": _normalize(recommended_method or rec.get("recommended_method") or ""),
        "grind_size": _normalize(grind_size or rec.get("grind_size") or ""),
        "water_temp": (water_temp or rec.get("water_temp") or "").strip(),
        "brew_ratio": (brew_ratio or rec.get("brew_ratio") or "").strip(),
    }


def _apply_brew_if_empty(conn: sqlite3.Connection, bean: dict[str, Any], brew: dict[str, str]) -> None:
    if not bean or not any(brew.values()):
        return
    if any((bean.get(key) or "").strip() for key in brew):
        return
    conn.execute(
        """
        UPDATE beans
        SET recommended_method = ?, grind_size = ?, water_temp = ?, brew_ratio = ?
        WHERE id = ?
        """,
        (
            brew["recommended_method"],
            brew["grind_size"],
            brew["water_temp"],
            brew["brew_ratio"],
            bean["id"],
        ),
    )
    bean.update(brew)
    bean["brew_recommendation"] = dict(brew)


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


def _row_to_bean(row: sqlite3.Row | None, is_favorite: bool = False) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    tags = data.get("flavor_tags") or "[]"
    try:
        data["flavor_tags"] = json.loads(tags)
    except json.JSONDecodeError:
        data["flavor_tags"] = [t.strip() for t in str(tags).split(",") if t.strip()]
    data["story"] = (data.get("story") or "").strip()
    brew = {
        "recommended_method": (data.get("recommended_method") or "").strip(),
        "grind_size": (data.get("grind_size") or "").strip(),
        "water_temp": (data.get("water_temp") or "").strip(),
        "brew_ratio": (data.get("brew_ratio") or "").strip(),
    }
    data.update(brew)
    data["brew_recommendation"] = brew
    lat, lng, region = resolve_origin_geo(
        data.get("origin") or "",
        data.get("region_full") or "",
        data.get("latitude"),
        data.get("longitude"),
    )
    data["roast_date"] = (data.get("roast_date") or "").strip()
    data["altitude"] = (data.get("altitude") or "").strip()
    data["varietal"] = (data.get("varietal") or "").strip()
    data["latitude"] = lat
    data["longitude"] = lng
    data["region_full"] = region
    favorite = data.pop("is_favorite", None)
    data["is_favorite"] = bool(is_favorite if favorite is None else favorite)
    return data


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
    flavor_tags: list[str] | None = None,
    skip_fuzzy: bool = False,
    image_url: str = "",
    story: str = "",
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
) -> dict[str, Any]:
    name = _normalize(name)
    roaster = _normalize(roaster)
    image_url = (image_url or "").strip()
    story = (story or "").strip()
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
    if not name or not roaster:
        raise ValueError("name_roaster_required")

    similar = find_similar_beans(name, roaster)
    exact = [row for row in similar if row.get("tier") == "exact"]
    if exact:
        bean = exact[0]
        if image_url and not (bean.get("image_url") or "").strip():
            update_bean_image(bean["id"], image_url)
            bean["image_url"] = image_url
        if story and not (bean.get("story") or "").strip():
            update_bean_story(bean["id"], story)
            bean["story"] = story
        if any(brew.values()) or any(meta.values()):
            with connect() as conn:
                if any(brew.values()):
                    _apply_brew_if_empty(conn, bean, brew)
                _apply_meta_if_empty(conn, bean, meta)
        return {"status": "exact", "similar": exact, "bean": bean}
    if not skip_fuzzy and similar:
        return {"status": "fuzzy", "similar": similar}

    tags = json.dumps(flavor_tags or _tags_from_notes(roaster_notes))
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO beans (
                name, roaster, origin, process, roast_level, roaster_notes,
                flavor_tags, story, image_url, recommended_method, grind_size,
                water_temp, brew_ratio, roast_date, altitude, varietal,
                latitude, longitude, region_full, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                roaster,
                _normalize(origin),
                _normalize(process),
                _normalize(roast_level),
                (roaster_notes or "").strip(),
                tags,
                story,
                image_url,
                brew["recommended_method"],
                brew["grind_size"],
                brew["water_temp"],
                brew["brew_ratio"],
                meta["roast_date"],
                meta["altitude"],
                meta["varietal"],
                meta["latitude"],
                meta["longitude"],
                meta["region_full"],
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
            if bean and story and not (bean.get("story") or "").strip():
                conn.execute(
                    "UPDATE beans SET story = ? WHERE id = ?",
                    (story, bean["id"]),
                )
                bean["story"] = story
            if bean:
                _apply_brew_if_empty(conn, bean, brew)
                _apply_meta_if_empty(conn, bean, meta)
            return {"status": "exists", "bean": bean}

        bean = conn.execute(
            "SELECT * FROM beans WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return {"status": "created", "bean": _row_to_bean(bean)}


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
        query += " AND (b.name LIKE ? OR b.roaster LIKE ? OR b.origin LIKE ? OR b.flavor_tags LIKE ?)"
        like = f"%{search}%"
        params.extend([like, like, like, like])
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
    return dict(row)


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
    return [dict(r) for r in rows]


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
    user = dict(latest) if latest else None
    if user:
        user["my_recipe"] = {
            "grind_setting": (user.get("grind_setting") or "").strip(),
            "coffee_grams": user.get("coffee_grams"),
            "water_grams": user.get("water_grams"),
            "brew_time": (user.get("brew_time") or "").strip(),
        }
    return {"bean": bean, "community": community, "user": user}


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


def _public_user(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data.pop("password_hash", None)
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
                email, username, password_hash, auth_provider, oauth_id, created_at
            ) VALUES (?, ?, ?, 'email', '', ?)
            """,
            (email, username, hash_password(password), _now()),
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
                    oauth_id = ?
                WHERE id = ?
                """,
                (username, provider, oauth_id, existing["id"]),
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
                email, username, password_hash, auth_provider, oauth_id, created_at
            ) VALUES (?, ?, '', ?, ?, ?)
            """,
            (email, username, provider, oauth_id, _now()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    user = _public_user(row)
    if not user:
        raise ValueError("oauth_create_failed")
    return user
