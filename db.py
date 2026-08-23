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

VERSION = "1.0.5"
EXACT_MATCH_CUTOFF = 0.90
NEAR_MATCH_CUTOFF = 0.70
FUZZY_CUTOFF = NEAR_MATCH_CUTOFF

ENVIRONMENT = os.getenv("ENVIRONMENT", "local").strip().lower()


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


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS beans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                roaster TEXT NOT NULL,
                origin TEXT DEFAULT '',
                process TEXT DEFAULT '',
                roast_level TEXT DEFAULT '',
                roaster_notes TEXT DEFAULT '',
                flavor_tags TEXT DEFAULT '[]',
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
                brew_method TEXT DEFAULT '',
                rating REAL NOT NULL,
                acidity REAL DEFAULT 3.0,
                sweetness REAL DEFAULT 3.0,
                body REAL DEFAULT 3.0,
                aftertaste REAL DEFAULT 3.0,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (bean_id) REFERENCES beans(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_beans_origin ON beans(origin);
            CREATE INDEX IF NOT EXISTS idx_beans_roast ON beans(roast_level);
            CREATE INDEX IF NOT EXISTS idx_ratings_bean ON ratings(bean_id);
            """
        )
    _seed_if_empty()


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
) -> dict[str, Any]:
    name = _normalize(name)
    roaster = _normalize(roaster)
    if not name or not roaster:
        raise ValueError("name_roaster_required")

    similar = find_similar_beans(name, roaster)
    exact = [row for row in similar if row.get("tier") == "exact"]
    if exact:
        return {"status": "exact", "similar": exact, "bean": exact[0]}
    if not skip_fuzzy and similar:
        return {"status": "fuzzy", "similar": similar}

    tags = json.dumps(flavor_tags or _tags_from_notes(roaster_notes))
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO beans (
                name, roaster, origin, process, roast_level, roaster_notes,
                flavor_tags, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                roaster,
                _normalize(origin),
                _normalize(process),
                _normalize(roast_level),
                (roaster_notes or "").strip(),
                tags,
                _now(),
            ),
        )
        if cur.rowcount == 0:
            existing = conn.execute(
                "SELECT * FROM beans WHERE name = ? AND roaster = ?",
                (name, roaster),
            ).fetchone()
            return {"status": "exists", "bean": _row_to_bean(existing)}

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


def insert_rating(
    bean_id: int,
    brew_method: str,
    rating: float,
    acidity: float,
    sweetness: float,
    body: float,
    aftertaste: float,
    notes: str = "",
) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ratings (
                bean_id, brew_method, rating, acidity, sweetness, body,
                aftertaste, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bean_id,
                _normalize(brew_method),
                float(rating),
                float(acidity),
                float(sweetness),
                float(body),
                float(aftertaste),
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


def get_flavor_profile(bean_id: int) -> dict[str, Any]:
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


def _tags_from_notes(notes: str) -> list[str]:
    return [part.strip() for part in (notes or "").replace(";", ",").split(",") if part.strip()]


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
        found = [term for term in vocab if term in lowered]
        extras = [p.strip() for p in lowered.replace(";", ",").split(",") if p.strip()]
        return sorted(set(found + extras), key=len, reverse=True)

    roaster = extract(roaster_notes)
    user = extract(user_notes)
    overlap = [
        tag for tag in roaster
        if any(tag == u or tag in u or u in tag for u in user if len(u) > 2)
    ]
    return {"roaster": roaster[:12], "user": user[:12], "overlap": overlap[:12]}
