"""SQLite manifest. Plain SQL, no ORM.

A track row holds everything we know about a file; crates are named playlists in
a many-to-many relationship with tracks.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL DEFAULT '',
    artist      TEXT NOT NULL DEFAULT '',
    bpm         REAL,
    key_name    TEXT NOT NULL DEFAULT '',
    tonality    TEXT NOT NULL DEFAULT '',
    camelot     TEXT NOT NULL DEFAULT '',
    genre       TEXT NOT NULL DEFAULT '',
    source_url  TEXT NOT NULL DEFAULT '',
    duration    REAL NOT NULL DEFAULT 0,
    size        INTEGER NOT NULL DEFAULT 0,
    date_added  TEXT NOT NULL DEFAULT '',
    first_beat  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS crates (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS track_crates (
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    crate_id INTEGER NOT NULL REFERENCES crates(id) ON DELETE CASCADE,
    PRIMARY KEY (track_id, crate_id)
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release to existing DBs."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
    if "first_beat" not in cols:
        conn.execute("ALTER TABLE tracks ADD COLUMN first_beat REAL NOT NULL DEFAULT 0")


def add_track(
    conn: sqlite3.Connection,
    *,
    path: str,
    title: str = "",
    artist: str = "",
    bpm: float | None = None,
    key_name: str = "",
    tonality: str = "",
    camelot: str = "",
    genre: str = "",
    source_url: str = "",
    duration: float = 0.0,
    size: int = 0,
    date_added: str | None = None,
) -> int:
    """Insert a track; returns its id. Raises sqlite3.IntegrityError on dup path."""
    path = str(Path(path).expanduser().resolve())
    cur = conn.execute(
        """
        INSERT INTO tracks
            (path, title, artist, bpm, key_name, tonality, camelot, genre,
             source_url, duration, size, date_added)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            path, title, artist, bpm, key_name, tonality, camelot, genre,
            source_url, duration, size, date_added or date.today().isoformat(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_analysis(
    conn: sqlite3.Connection,
    track_id: int,
    *,
    bpm: float,
    key_name: str,
    tonality: str,
    camelot: str,
    duration: float | None = None,
    first_beat: float | None = None,
) -> None:
    sets = ["bpm=?", "key_name=?", "tonality=?", "camelot=?"]
    params: list[Any] = [bpm, key_name, tonality, camelot]
    if duration is not None:
        sets.append("duration=?")
        params.append(duration)
    if first_beat is not None:
        sets.append("first_beat=?")
        params.append(first_beat)
    params.append(track_id)
    conn.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()


def get_track(conn: sqlite3.Connection, track_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()


def get_by_path(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    path = str(Path(path).expanduser().resolve())
    return conn.execute("SELECT * FROM tracks WHERE path=?", (path,)).fetchone()


def get_by_url(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    if not url:
        return None
    return conn.execute(
        "SELECT * FROM tracks WHERE source_url=?", (url,)
    ).fetchone()


def delete_track(conn: sqlite3.Connection, track_id: int) -> None:
    conn.execute("DELETE FROM tracks WHERE id=?", (track_id,))
    conn.commit()


def all_tracks(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tracks ORDER BY id").fetchall()


# ----- crates -----

def create_crate(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("crate name cannot be empty")
    conn.execute("INSERT OR IGNORE INTO crates (name) VALUES (?)", (name,))
    conn.commit()
    row = conn.execute("SELECT id FROM crates WHERE name=?", (name,)).fetchone()
    return int(row["id"])


def get_crate(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM crates WHERE name=?", (name.strip(),)).fetchone()


def remove_crate(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("DELETE FROM crates WHERE name=?", (name.strip(),))
    conn.commit()
    return cur.rowcount > 0


def list_crates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.id, c.name, COUNT(tc.track_id) AS count
        FROM crates c
        LEFT JOIN track_crates tc ON tc.crate_id = c.id
        GROUP BY c.id, c.name
        ORDER BY c.name
        """
    ).fetchall()


def assign_track(conn: sqlite3.Connection, track_id: int, crate_name: str) -> None:
    """Assign a track to a crate, creating the crate if needed."""
    crate_id = create_crate(conn, crate_name)
    conn.execute(
        "INSERT OR IGNORE INTO track_crates (track_id, crate_id) VALUES (?, ?)",
        (track_id, crate_id),
    )
    conn.commit()


def crates_for_track(conn: sqlite3.Connection, track_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT c.name FROM crates c
        JOIN track_crates tc ON tc.crate_id = c.id
        WHERE tc.track_id = ?
        ORDER BY c.name
        """,
        (track_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def tracks_in_crate(conn: sqlite3.Connection, crate_name: str) -> list[int]:
    rows = conn.execute(
        """
        SELECT tc.track_id FROM track_crates tc
        JOIN crates c ON c.id = tc.crate_id
        WHERE c.name = ?
        ORDER BY tc.track_id
        """,
        (crate_name,),
    ).fetchall()
    return [int(r["track_id"]) for r in rows]


def filter_tracks(
    conn: sqlite3.Connection,
    *,
    crate: str | None = None,
    camelot_in: Iterable[str] | None = None,
    bpm_range: tuple[float, float] | None = None,
    sort: str = "added",
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    joins = ""
    if crate:
        joins = (
            " JOIN track_crates tc ON tc.track_id = t.id"
            " JOIN crates c ON c.id = tc.crate_id"
        )
        clauses.append("c.name = ?")
        params.append(crate)
    if camelot_in:
        codes = list(camelot_in)
        placeholders = ",".join("?" for _ in codes)
        clauses.append(f"t.camelot IN ({placeholders})")
        params.extend(codes)
    if bpm_range:
        clauses.append("t.bpm BETWEEN ? AND ?")
        params.extend([bpm_range[0], bpm_range[1]])

    order = {
        "bpm": "t.bpm ASC, t.id ASC",
        "key": "t.camelot ASC, t.id ASC",
        "added": "t.date_added ASC, t.id ASC",
    }.get(sort, "t.date_added ASC, t.id ASC")

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT DISTINCT t.* FROM tracks t{joins}{where} ORDER BY {order}"
    return conn.execute(sql, params).fetchall()
