"""Persistent state: house-wide settings (persona mode, proactivity level)
and per-resident preferences (device preferences once domains exist, style
signals for the adaptive persona today). See docs/TZ.md section 5.
"""

import sqlite3

VALID_PERSONA_MODES = {"butler", "warm", "adaptive"}
VALID_PROACTIVITY_LEVELS = {"quiet", "balanced", "chatty"}


class MemoryStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @property
    def connection(self) -> sqlite3.Connection:
        """For other stores in the same database (app.reminders)."""
        return self._conn

    def get_persona_mode(self) -> str:
        row = self._conn.execute("SELECT persona_mode FROM house_settings WHERE id = 1").fetchone()
        return row["persona_mode"]

    def set_persona_mode(self, mode: str) -> None:
        if mode not in VALID_PERSONA_MODES:
            raise ValueError(f"Unknown persona mode {mode!r}. Valid: {sorted(VALID_PERSONA_MODES)}")
        self._conn.execute("UPDATE house_settings SET persona_mode = ? WHERE id = 1", (mode,))
        self._conn.commit()

    def get_proactivity_level(self) -> str:
        row = self._conn.execute("SELECT proactivity_level FROM house_settings WHERE id = 1").fetchone()
        return row["proactivity_level"]

    def set_proactivity_level(self, level: str) -> None:
        if level not in VALID_PROACTIVITY_LEVELS:
            raise ValueError(
                f"Unknown proactivity level {level!r}. Valid: {sorted(VALID_PROACTIVITY_LEVELS)}"
            )
        self._conn.execute("UPDATE house_settings SET proactivity_level = ? WHERE id = 1", (level,))
        self._conn.commit()

    def ensure_resident(self, resident_id: str, display_name: str | None = None) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO residents (resident_id, display_name) VALUES (?, ?)",
            (resident_id, display_name),
        )
        self._conn.commit()

    def get_preference(self, resident_id: str, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM preferences WHERE resident_id = ? AND key = ?", (resident_id, key)
        ).fetchone()
        return row["value"] if row else default

    def set_preference(self, resident_id: str, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO preferences (resident_id, key, value, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT (resident_id, key)
            DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (resident_id, key, value),
        )
        self._conn.commit()

    def get_preferences(self, resident_id: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT key, value FROM preferences WHERE resident_id = ?", (resident_id,)
        ).fetchall()
        return {row["key"]: row["value"] for row in rows}

    def remove_resident(self, resident_id: str) -> None:
        """The resident and everything kept about them (their voice profile is a preference too)."""
        self._conn.execute("DELETE FROM preferences WHERE resident_id = ?", (resident_id,))
        self._conn.execute("DELETE FROM residents WHERE resident_id = ?", (resident_id,))
        self._conn.commit()

    def list_resident_ids(self) -> list[str]:
        rows = self._conn.execute("SELECT resident_id FROM residents").fetchall()
        return [row["resident_id"] for row in rows]
