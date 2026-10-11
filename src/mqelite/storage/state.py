"""Rebuildable ingest state; canonical L2 JSON remains source of truth."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone

from mqelite.storage.layout import FILE_MODE, PersonaLayout


@dataclass(frozen=True, slots=True)
class DayState:
    memory_date: date
    content_hash: str
    source_hash: str
    empty_day: bool


class IngestState:
    def __init__(self, layout: PersonaLayout) -> None:
        self.layout = layout

    def _connect(self) -> sqlite3.Connection:
        self.layout.assert_safe(self.layout.ingest_db)
        if not self.layout.ingest_db.exists():
            descriptor = os.open(
                self.layout.ingest_db,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                FILE_MODE,
            )
            os.close(descriptor)
        connection = sqlite3.connect(self.layout.ingest_db, timeout=30)
        os.chmod(self.layout.ingest_db, FILE_MODE)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_state (
                memory_date TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                empty_day INTEGER NOT NULL CHECK (empty_day IN (0, 1)),
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
        return connection

    def get(self, memory_date: date) -> DayState | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT content_hash, source_hash, empty_day FROM daily_state WHERE memory_date = ?",
                (memory_date.isoformat(),),
            ).fetchone()
        if row is None:
            return None
        return DayState(memory_date, row[0], row[1], bool(row[2]))

    def record(self, state: DayState) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO daily_state(memory_date, content_hash, source_hash, empty_day, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(memory_date) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    source_hash=excluded.source_hash,
                    empty_day=excluded.empty_day,
                    updated_at=excluded.updated_at
                """,
                (
                    state.memory_date.isoformat(),
                    state.content_hash,
                    state.source_hash,
                    int(state.empty_day),
                    now,
                ),
            )
