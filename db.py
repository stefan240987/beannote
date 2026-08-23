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

VERSION = "2.0.0"
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
    path = get_db_path()
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


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
                created_at TEXT NOT NULL,
                FOREIGN KEY (bean_id) REFERENCES beans(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_beans_origin ON beans(origin);
            CREATE INDEX IF NOT EXISTS idx_beans_roast ON beans(roast_level);
            CREATE INDEX IF NOT EXISTS idx_ratings_bean ON ratings(bean_id);
            CREATE INDEX IF NOT EXISTS idx_ratings_user ON ratings(user_id);
            CREATE INDEX IF NOT EXISTS idx_users_oauth ON users(auth_provider, oauth_id);
            """
        )
        _ensure_columns(conn)
    get_images_dir()
    _seed_if_empty()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    beans = {row[1] for row in conn.execute("PRAGMA table_info(beans)")}
    if "image_url" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN image_url TEXT DEFAULT ''")
    if "story" not in beans:
        conn.execute("ALTER TABLE beans ADD COLUMN story TEXT DEFAULT ''")
    ratings = {row[1] for row in conn.execute("PRAGMA table_info(ratings)")}
    if "user_id" not in ratings:
        conn.execute("ALTER TABLE ratings ADD COLUMN user_id INTEGER")


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


def _seed_if_empty() -> None:
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM beans").fetchone()[0]
        if count:
            return

        seeds = [
            {
                "name": "Worka Sakaro",
                "roaster": "Prolog Coffee",
                "origin": "Ethiopia",
                "process": "Natural",
                "roast_level": "Light",
                "roaster_notes": "Jasmine, peach, bergamot, tropical florals",
                "flavor_tags": ["jasmine", "peach", "bergamot", "floral"],
                "community_acidity": 4.4,
                "community_sweetness": 4.2,
                "community_body": 3.1,
                "community_aftertaste": 4.0,
            },
            {
                "name": "Las Flores",
                "roaster": "La Cabra",
                "origin": "Colombia",
                "process": "Washed",
                "roast_level": "Light",
                "roaster_notes": "Red apple, caramel, cocoa, cane sugar",
                "flavor_tags": ["apple", "caramel", "cocoa"],
                "community_acidity": 3.8,
                "community_sweetness": 4.1,
                "community_body": 3.6,
                "community_aftertaste": 3.7,
            },
            {
                "name": "Kii",
                "roaster": "The Coffee Collective",
                "origin": "Kenya",
                "process": "Washed",
                "roast_level": "Light",
                "roaster_notes": "Blackcurrant, grapefruit, floral, sparkling acidity",
                "flavor_tags": ["blackcurrant", "grapefruit", "floral"],
                "community_acidity": 4.6,
                "community_sweetness": 3.7,
                "community_body": 3.2,
                "community_aftertaste": 4.1,
            },
            {
                "name": "Sítio Serra do Cigano",
                "roaster": "April Coffee",
                "origin": "Brazil",
                "process": "Natural",
                "roast_level": "Medium",
                "roaster_notes": "Milk chocolate, hazelnut, dried fruit",
                "flavor_tags": ["chocolate", "hazelnut", "dried fruit"],
                "community_acidity": 2.6,
                "community_sweetness": 4.3,
                "community_body": 4.2,
                "community_aftertaste": 3.8,
            },
            {
                "name": "Elida Estate Geisha",
                "roaster": "The Barn",
                "origin": "Panama",
                "process": "Washed",
                "roast_level": "Light",
                "roaster_notes": "Bergamot, jasmine, tropical fruit, tea-like",
                "flavor_tags": ["bergamot", "jasmine", "tropical", "tea"],
                "community_acidity": 4.3,
                "community_sweetness": 4.0,
                "community_body": 2.8,
                "community_aftertaste": 4.4,
            },
            {
                "name": "Guji Hambela",
                "roaster": "La Cabra",
                "origin": "Ethiopia",
                "process": "Anaerobic",
                "roast_level": "Light",
                "roaster_notes": "Blueberry, wine, floral, ripe stone fruit",
                "flavor_tags": ["blueberry", "wine", "floral", "stone fruit"],
                "community_acidity": 4.1,
                "community_sweetness": 4.4,
                "community_body": 3.5,
                "community_aftertaste": 4.0,
            },
        ]
        for bean in seeds:
            conn.execute(
                """
                INSERT INTO beans (
                    name, roaster, origin, process, roast_level, roaster_notes,
                    flavor_tags, community_acidity, community_sweetness,
                    community_body, community_aftertaste, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bean["name"],
                    bean["roaster"],
                    bean["origin"],
                    bean["process"],
                    bean["roast_level"],
                    bean["roaster_notes"],
                    json.dumps(bean["flavor_tags"]),
                    bean["community_acidity"],
                    bean["community_sweetness"],
                    bean["community_body"],
                    bean["community_aftertaste"],
                    _now(),
                ),
            )


def _row_to_bean(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    tags = data.get("flavor_tags") or "[]"
    try:
        data["flavor_tags"] = json.loads(tags)
    except json.JSONDecodeError:
        data["flavor_tags"] = [t.strip() for t in str(tags).split(",") if t.strip()]
    data["story"] = (data.get("story") or "").strip()
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
) -> dict[str, Any]:
    name = _normalize(name)
    roaster = _normalize(roaster)
    image_url = (image_url or "").strip()
    story = (story or "").strip()
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
        return {"status": "exact", "similar": exact, "bean": bean}
    if not skip_fuzzy and similar:
        return {"status": "fuzzy", "similar": similar}

    tags = json.dumps(flavor_tags or _tags_from_notes(roaster_notes))
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO beans (
                name, roaster, origin, process, roast_level, roaster_notes,
                flavor_tags, story, image_url, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            return {"status": "exists", "bean": bean}

        bean = conn.execute(
            "SELECT * FROM beans WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return {"status": "created", "bean": _row_to_bean(bean)}


def get_bean(bean_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM beans WHERE id = ?", (bean_id,)).fetchone()
    return _row_to_bean(row)


def list_beans(
    search: str = "",
    origin: str = "",
    roast_level: str = "",
    min_rating: float = 0.0,
) -> list[dict[str, Any]]:
    query = """
        SELECT b.*,
               AVG(r.rating) AS avg_rating,
               COUNT(r.id) AS rating_count,
               AVG(r.acidity) AS avg_acidity,
               AVG(r.sweetness) AS avg_sweetness,
               AVG(r.body) AS avg_body,
               AVG(r.aftertaste) AS avg_aftertaste
        FROM beans b
        LEFT JOIN ratings r ON r.bean_id = b.id
        WHERE 1=1
    """
    params: list[Any] = []
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
) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ratings (
                bean_id, user_id, brew_method, rating, acidity, sweetness, body,
                aftertaste, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    bean = get_bean(bean_id)
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
