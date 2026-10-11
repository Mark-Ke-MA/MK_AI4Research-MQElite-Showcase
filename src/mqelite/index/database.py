"""Persona-scoped SQLite/FTS index; L2 and archive files remain canonical."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

from mqelite.domain.l2 import L2Document, LegacyL2Document
from mqelite.errors import StorageError, ValidationError
from mqelite.index.tokenize import index_terms, normalize_text, query_terms
from mqelite.storage.layout import FILE_MODE, PersonaLayout

INDEX_SCHEMA_VERSION = "mqelite.index.v1"


@dataclass(frozen=True, slots=True)
class IndexExcerpt:
    ordinal: int
    role: str
    timestamp: str | None
    time_value: str | None
    time_precision: str
    content: str
    message_type: str | None


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    memory_date: date
    source_kind: str
    content_hash: str
    excerpts: tuple[IndexExcerpt, ...]
    archive_generation: int | None = None

    @classmethod
    def from_native(cls, document: L2Document, *, archive_generation: int | None = None) -> "IndexedDocument":
        excerpts = tuple(
            IndexExcerpt(
                ordinal=index,
                role=item.role,
                timestamp=item.timestamp,
                time_value=item.time,
                time_precision="second" if item.timestamp else ("minute" if item.time else "day"),
                content=item.content,
                message_type=item.message_type,
            )
            for index, item in enumerate(document.conversation_excerpts)
        )
        return cls(
            document.date,
            "native",
            hashlib.sha256(document.canonical_bytes()).hexdigest(),
            excerpts,
            archive_generation,
        )

    @classmethod
    def from_legacy(cls, document: LegacyL2Document, *, archive_generation: int | None = None) -> "IndexedDocument":
        excerpts = tuple(
            IndexExcerpt(
                ordinal=index,
                role=item.role,
                timestamp=item.timestamp,
                time_value=item.time,
                time_precision=item.time_precision,
                content=item.content,
                message_type=item.message_type,
            )
            for index, item in enumerate(document.conversation_excerpts)
        )
        return cls(
            document.date,
            "legacy_mqe",
            hashlib.sha256(document.raw_bytes).hexdigest(),
            excerpts,
            archive_generation,
        )


@dataclass(frozen=True, slots=True)
class SearchHit:
    memory_date: date
    ordinal: int
    role: str
    timestamp: str | None
    time_precision: str
    content: str
    score: float


class RecallIndex:
    def __init__(self, layout: PersonaLayout) -> None:
        self.layout = layout

    def _connect_writer(self) -> sqlite3.Connection:
        self.layout.initialize()
        self.layout.assert_safe(self.layout.recall_db)
        if not self.layout.recall_db.exists():
            descriptor = os.open(
                self.layout.recall_db,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                FILE_MODE,
            )
            os.close(descriptor)
        connection = sqlite3.connect(self.layout.recall_db, timeout=30)
        connection.row_factory = sqlite3.Row
        os.chmod(self.layout.recall_db, FILE_MODE)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_probe USING fts5(value)")
            connection.execute("DROP TABLE fts_probe")
        except sqlite3.OperationalError as exc:
            connection.close()
            raise StorageError("SQLite FTS5 is required for bounded recall") from exc
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                persona_id TEXT NOT NULL,
                memory_date TEXT NOT NULL UNIQUE,
                source_kind TEXT NOT NULL,
                archive_generation INTEGER,
                content_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS excerpts (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                role TEXT NOT NULL,
                timestamp TEXT,
                time_value TEXT,
                time_precision TEXT NOT NULL,
                content TEXT NOT NULL,
                message_type TEXT,
                search_terms TEXT NOT NULL,
                UNIQUE(document_id, ordinal)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS excerpts_fts USING fts5(
                excerpt_id UNINDEXED,
                search_terms,
                tokenize='unicode61'
            );
            CREATE TABLE IF NOT EXISTS index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('schema_version', ?)",
            (INDEX_SCHEMA_VERSION,),
        )
        connection.commit()
        return connection

    def _connect_reader(self) -> sqlite3.Connection:
        """Open an existing ready index without mutating filesystem or SQLite state."""

        self.layout.assert_safe(self.layout.recall_db)
        path = self.layout.recall_db
        if path.is_symlink() or not path.is_file():
            raise StorageError("recall index is unavailable")
        if stat.S_IMODE(path.stat().st_mode) != FILE_MODE:
            raise StorageError("recall index mode is unsafe")
        connection: sqlite3.Connection | None = None
        try:
            uri = path.resolve(strict=True).as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA foreign_keys=ON")
            metadata = {
                row["key"]: row["value"]
                for row in connection.execute(
                    "SELECT key,value FROM index_meta WHERE key IN ('schema_version','ready')"
                ).fetchall()
            }
            if metadata != {"schema_version": INDEX_SCHEMA_VERSION, "ready": "1"}:
                raise StorageError("recall index is not ready or has an unsupported schema")
            return connection
        except (OSError, sqlite3.Error, StorageError) as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, StorageError):
                raise
            raise StorageError("recall index is unavailable") from None

    def _replace_document_tx(self, connection: sqlite3.Connection, document: IndexedDocument) -> None:
        if document.source_kind not in {"native", "legacy_mqe"}:
            raise ValidationError("index source kind is unsupported")
        old_rows = connection.execute(
            "SELECT e.id FROM excerpts e JOIN documents d ON d.id=e.document_id WHERE d.memory_date=?",
            (document.memory_date.isoformat(),),
        ).fetchall()
        for row in old_rows:
            connection.execute("DELETE FROM excerpts_fts WHERE excerpt_id=?", (row[0],))
        connection.execute("DELETE FROM documents WHERE memory_date=?", (document.memory_date.isoformat(),))
        cursor = connection.execute(
            "INSERT INTO documents(persona_id,memory_date,source_kind,archive_generation,content_hash) VALUES (?,?,?,?,?)",
            (
                self.layout.persona_id,
                document.memory_date.isoformat(),
                document.source_kind,
                document.archive_generation,
                document.content_hash,
            ),
        )
        document_id = cursor.lastrowid
        for item in document.excerpts:
            terms = index_terms(item.content)
            cursor = connection.execute(
                """
                INSERT INTO excerpts(document_id,ordinal,role,timestamp,time_value,time_precision,content,message_type,search_terms)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    document_id,
                    item.ordinal,
                    item.role,
                    item.timestamp,
                    item.time_value,
                    item.time_precision,
                    item.content,
                    item.message_type,
                    terms,
                ),
            )
            connection.execute(
                "INSERT INTO excerpts_fts(excerpt_id,search_terms) VALUES (?,?)",
                (cursor.lastrowid, terms),
            )

    def replace_document(self, document: IndexedDocument) -> None:
        with closing(self._connect_writer()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            self._replace_document_tx(connection, document)
            connection.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES ('ready','1')")

    def document_fingerprints(self) -> dict[date, tuple[str, str, int | None]]:
        """Return content-free coverage evidence from the ready read-only index."""

        with closing(self._connect_reader()) as connection:
            rows = connection.execute(
                "SELECT memory_date,source_kind,content_hash,archive_generation "
                "FROM documents WHERE persona_id=? ORDER BY memory_date",
                (self.layout.persona_id,),
            ).fetchall()
        return {
            date.fromisoformat(row["memory_date"]): (
                row["source_kind"],
                row["content_hash"],
                row["archive_generation"],
            )
            for row in rows
        }

    def rebuild(self, documents: Iterable[IndexedDocument], *, source_root_hash: str) -> None:
        if len(source_root_hash) != 64:
            raise ValidationError("source_root_hash must be a SHA-256 hex digest")
        try:
            bytes.fromhex(source_root_hash)
        except ValueError as exc:
            raise ValidationError("source_root_hash must be a SHA-256 hex digest") from exc
        documents = tuple(sorted(documents, key=lambda item: item.memory_date))
        with closing(self._connect_writer()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES ('ready','0')")
            connection.execute("DELETE FROM excerpts_fts")
            connection.execute("DELETE FROM excerpts")
            connection.execute("DELETE FROM documents")
            for document in documents:
                self._replace_document_tx(connection, document)
            connection.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES ('source_root_hash',?)", (source_root_hash,))
            connection.execute(
                "INSERT OR REPLACE INTO index_meta(key,value) VALUES ('built_at',?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            connection.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES ('ready','1')")

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        role: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        current_memory_date: date | None = None,
        max_chars: int = 20_000,
    ) -> list[SearchHit]:
        if limit < 1 or limit > 50 or max_chars < 1 or max_chars > 100_000:
            raise ValidationError("recall bounds are outside the supported range")
        terms = query_terms(query)
        if not terms:
            return []
        fts_query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        clauses = ["excerpts_fts MATCH ?", "d.persona_id = ?"]
        parameters: list[object] = [fts_query, self.layout.persona_id]
        if role is not None:
            clauses.append("e.role = ?")
            parameters.append(role)
        if date_from is not None:
            clauses.append("d.memory_date >= ?")
            parameters.append(date_from.isoformat())
        if date_to is not None:
            clauses.append("d.memory_date <= ?")
            parameters.append(date_to.isoformat())
        candidate_limit = min(500, limit * 20)
        parameters.append(candidate_limit)
        sql = f"""
            SELECT d.memory_date,e.ordinal,e.role,e.timestamp,e.time_precision,e.content,e.search_terms
            FROM excerpts_fts
            JOIN excerpts e ON e.id=excerpts_fts.excerpt_id
            JOIN documents d ON d.id=e.document_id
            WHERE {' AND '.join(clauses)}
            ORDER BY bm25(excerpts_fts)
            LIMIT ?
        """
        with closing(self._connect_reader()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        normalized_query = normalize_text(query)
        scored: list[SearchHit] = []
        term_set = set(terms)
        for row in rows:
            indexed = set(row["search_terms"].split())
            overlap = len(term_set & indexed)
            lexical = float(overlap)
            if normalized_query and normalized_query in normalize_text(row["content"]):
                lexical += 1.0
            memory_date = date.fromisoformat(row["memory_date"])
            recency = 1.0
            if current_memory_date is not None:
                age = max(0, (current_memory_date - memory_date).days)
                recency += 0.1 / (1.0 + age / 30.0)
            scored.append(
                SearchHit(
                    memory_date,
                    row["ordinal"],
                    row["role"],
                    row["timestamp"],
                    row["time_precision"],
                    row["content"],
                    lexical * recency,
                )
            )
        scored.sort(key=lambda item: (-item.score, -item.memory_date.toordinal(), item.ordinal))
        selected: list[SearchHit] = []
        characters = 0
        for item in scored:
            if len(selected) >= limit or characters + len(item.content) > max_chars:
                break
            selected.append(item)
            characters += len(item.content)
        return selected

    def excerpts_for_date(self, memory_date: date) -> list[SearchHit]:
        with closing(self._connect_reader()) as connection:
            rows = connection.execute(
                """
                SELECT d.memory_date,e.ordinal,e.role,e.timestamp,e.time_precision,e.content
                FROM excerpts e JOIN documents d ON d.id=e.document_id
                WHERE d.persona_id=? AND d.memory_date=? ORDER BY e.ordinal
                """,
                (self.layout.persona_id, memory_date.isoformat()),
            ).fetchall()
        return [
            SearchHit(
                date.fromisoformat(row["memory_date"]),
                row["ordinal"],
                row["role"],
                row["timestamp"],
                row["time_precision"],
                row["content"],
                0.0,
            )
            for row in rows
        ]

    def recent(self, *, through: date, days: int, limit: int, max_chars: int) -> list[SearchHit]:
        if days < 1 or days > 366 or limit < 1 or limit > 10_000 or max_chars < 1 or max_chars > 10_000_000:
            raise ValidationError("recent recall bounds are outside the supported range")
        start = through.fromordinal(through.toordinal() - days + 1)
        with closing(self._connect_reader()) as connection:
            rows = connection.execute(
                """
                SELECT d.memory_date,e.ordinal,e.role,e.timestamp,e.time_precision,e.content
                FROM excerpts e JOIN documents d ON d.id=e.document_id
                WHERE d.persona_id=? AND d.memory_date BETWEEN ? AND ?
                ORDER BY d.memory_date DESC,e.ordinal DESC LIMIT ?
                """,
                (self.layout.persona_id, start.isoformat(), through.isoformat(), limit),
            ).fetchall()
        result: list[SearchHit] = []
        characters = 0
        for row in rows:
            if characters + len(row["content"]) > max_chars:
                break
            result.append(
                SearchHit(
                    date.fromisoformat(row["memory_date"]),
                    row["ordinal"],
                    row["role"],
                    row["timestamp"],
                    row["time_precision"],
                    row["content"],
                    0.0,
                )
            )
            characters += len(row["content"])
        return result
