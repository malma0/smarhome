"""Thin SQLite bootstrap. One file, no ORM - the schema is small enough
(house-wide settings, residents, a generic preferences key/value table) that
an ORM would add more ceremony than it saves. Not async: sqlite3 has no
native async driver, and for a single local file serving one house this
blocking call per request is not worth the complexity of a thread pool.
"""

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS house_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    persona_mode TEXT NOT NULL DEFAULT 'warm',
    proactivity_level TEXT NOT NULL DEFAULT 'quiet'
);

CREATE TABLE IF NOT EXISTS residents (
    resident_id TEXT PRIMARY KEY,
    display_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS preferences (
    resident_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (resident_id, key)
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO house_settings (id) VALUES (1)")
    conn.commit()
    return conn
