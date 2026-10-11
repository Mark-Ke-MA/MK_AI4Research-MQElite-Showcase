from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date, time
from pathlib import Path

from mqelite.domain.events import ConversationExcerpt
from mqelite.domain.l2 import L2Document, parse_legacy_l2
from mqelite.errors import StorageError, ValidationError
from mqelite.index import IndexedDocument, RecallIndex, index_terms, query_terms
from mqelite.recall import RecallService
from mqelite.storage.layout import DataLayout


def native(day: date, *contents: tuple[str, str, str | None]) -> IndexedDocument:
    excerpts = tuple(
        ConversationExcerpt(
            role=role,
            content=content,
            turn_index=index,
            timestamp=timestamp,
            time=timestamp[11:19] if timestamp else None,
        )
        for index, (role, content, timestamp) in enumerate(contents)
    )
    return IndexedDocument.from_native(
        L2Document(date=day, agent_id="synthetic-persona", conversation_excerpts=excerpts)
    )


class TokenizerTests(unittest.TestCase):
    def test_latin_cjk_normalization_is_deterministic(self) -> None:
        terms = query_terms("MemoQUASAR 研究员博士 １２３")
        self.assertIn("memoquasar", terms)
        self.assertIn("研究", terms)
        self.assertIn("究员", terms)
        self.assertIn("博士", terms)
        self.assertIn("123", terms)
        self.assertEqual(index_terms("ＡＢＣ"), "abc")


class RecallIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        layout = DataLayout(Path(self.temporary.name) / "data").persona("synthetic-persona")
        self.index = RecallIndex(layout)
        self.service = RecallService(self.index)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_search_handles_cjk_mixed_language_filters_and_bounded_recency(self) -> None:
        old = native(
            date(2026, 1, 1),
            ("assistant", "研究员博士讨论 memoquasar archive design", "2026-01-01T10:00:00+00:00"),
        )
        recent = native(
            date(2026, 7, 30),
            ("assistant", "研究员今天处理别的事情", "2026-07-30T10:00:00+01:00"),
            ("user", "memoquasar", "2026-07-30T10:01:00+01:00"),
        )
        self.index.rebuild([old, recent], source_root_hash=hashlib.sha256(b"synthetic root").hexdigest())
        hits = self.service.search("研究员 memoquasar", current_memory_date=date(2026, 7, 31), limit=10)
        self.assertEqual(date(2026, 1, 1), hits[0].memory_date)
        self.assertGreater(hits[0].score, hits[-1].score)
        user_hits = self.service.search("memoquasar", role="user", limit=10)
        self.assertEqual(1, len(user_hits))
        self.assertEqual("user", user_hits[0].role)
        bounded = self.service.search("研究员", limit=10, max_chars=5)
        self.assertEqual([], bounded)
        self.assertEqual(0o600, self.index.layout.recall_db.stat().st_mode & 0o777)

    def test_replacing_day_removes_stale_fts_rows(self) -> None:
        day = date(2026, 7, 1)
        self.index.replace_document(native(day, ("user", "obsolete phrase", None)))
        self.assertEqual(1, len(self.service.search("obsolete")))
        self.index.replace_document(native(day, ("user", "replacement phrase", None)))
        self.assertEqual([], self.service.search("obsolete"))
        self.assertEqual(1, len(self.service.search("replacement")))

    def test_exact_window_excludes_day_precision_legacy_without_fabrication(self) -> None:
        raw = json.dumps(
            {
                "date": "2026-07-01",
                "agent_id": "synthetic-persona",
                "conversation_excerpts": [
                    {"role": "user", "content": "known minute", "time": "10:15"},
                    {"content": "unknown time", "time": "invalid"},
                ],
            }
        ).encode()
        legacy = parse_legacy_l2(raw)
        self.index.replace_document(IndexedDocument.from_legacy(legacy))
        whole_day = self.service.exact(date(2026, 7, 1))
        self.assertEqual(["known minute", "unknown time"], [hit.content for hit in whole_day])
        windowed = self.service.exact(
            date(2026, 7, 1), window_start=time(10, 0), window_end=time(10, 30)
        )
        self.assertEqual(["known minute"], [hit.content for hit in windowed])

    def test_untimed_interrupted_native_is_recalled_without_window_fabrication(self) -> None:
        day = date(2026, 7, 1)
        document = L2Document(
            date=day,
            agent_id="synthetic-persona",
            conversation_excerpts=(
                ConversationExcerpt(
                    role="user",
                    content="before interruption",
                    turn_index=0,
                    time="10:00:00",
                    timestamp="2026-07-01T10:00:00+01:00",
                ),
                ConversationExcerpt(
                    role="assistant",
                    content="recoverable untimed evidence",
                    turn_index=1,
                    message_type="text_untimed_interrupted",
                ),
                ConversationExcerpt(
                    role="user",
                    content="after interruption",
                    turn_index=2,
                    time="10:05:00",
                    timestamp="2026-07-01T10:05:00+01:00",
                ),
            ),
        )
        self.index.replace_document(IndexedDocument.from_native(document))

        whole_day = self.service.exact(day)
        self.assertEqual(
            [
                "before interruption",
                "recoverable untimed evidence",
                "after interruption",
            ],
            [hit.content for hit in whole_day],
        )
        windowed = self.service.exact(
            day, window_start=time(9, 59), window_end=time(10, 6)
        )
        self.assertEqual(
            ["before interruption", "after interruption"],
            [hit.content for hit in windowed],
        )
        search = self.service.search("recoverable untimed evidence")
        self.assertEqual(1, len(search))
        self.assertEqual("day", search[0].time_precision)

    def test_recent_is_date_and_size_bounded(self) -> None:
        documents = [
            native(date(2026, 7, day), ("user", f"day-{day}", None)) for day in range(1, 11)
        ]
        self.index.rebuild(documents, source_root_hash=hashlib.sha256(b"root").hexdigest())
        hits = self.service.recent(through=date(2026, 7, 10), days=3, limit=2, max_chars=100)
        self.assertEqual([date(2026, 7, 10), date(2026, 7, 9)], [hit.memory_date for hit in hits])
        self.assertEqual([], self.service.recent(through=date(2026, 7, 10), days=3, limit=2, max_chars=2))

    def test_invalid_bounds_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.search("query", limit=0)
        with self.assertRaises(ValidationError):
            self.service.exact(date(2026, 7, 1), window_start=time(10, 0))

    def test_recall_connection_is_strictly_read_only(self) -> None:
        self.index.rebuild(
            [native(date(2026, 7, 1), ("user", "read-only marker", None))],
            source_root_hash=hashlib.sha256(b"read-only root").hexdigest(),
        )
        database = self.index.layout.recall_db
        before_bytes = database.read_bytes()
        before_stat = database.stat()

        self.assertEqual(1, len(self.service.search("read-only marker")))
        self.assertEqual(1, len(self.service.exact(date(2026, 7, 1))))
        self.assertEqual(1, len(self.service.recent(through=date(2026, 7, 1), days=1)))

        after_stat = database.stat()
        self.assertEqual(before_bytes, database.read_bytes())
        self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_recall_does_not_create_a_missing_index(self) -> None:
        missing_root = Path(self.temporary.name) / "missing" / "data"
        missing = RecallService(RecallIndex(DataLayout(missing_root).persona("synthetic-persona")))
        with self.assertRaises(StorageError):
            missing.search("marker")
        self.assertFalse(missing_root.exists())

    def test_recall_rejects_an_unready_index_without_repairing_it(self) -> None:
        self.index.rebuild(
            [native(date(2026, 7, 1), ("user", "unready marker", None))],
            source_root_hash=hashlib.sha256(b"unready root").hexdigest(),
        )
        database = self.index.layout.recall_db
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE index_meta SET value='0' WHERE key='ready'")
        before = database.read_bytes()
        with self.assertRaises(StorageError):
            self.service.search("unready marker")
        self.assertEqual(before, database.read_bytes())

    def test_exact_rejects_char_bounds_skips_bad_time_and_supports_midnight_window(self) -> None:
        for max_chars in (0, 10_000_001):
            with self.subTest(max_chars=max_chars), self.assertRaises(ValidationError):
                self.service.exact(date(2026, 7, 1), max_chars=max_chars)

        day = date(2026, 7, 1)
        self.index.replace_document(
            native(
                day,
                ("user", "late", "2026-07-01T23:30:00+01:00"),
                ("assistant", "early", "2026-07-01T01:30:00+01:00"),
                ("assistant", "daytime", "2026-07-01T12:00:00+01:00"),
            )
        )
        midnight = self.service.exact(day, window_start=time(23), window_end=time(2))
        self.assertEqual(["late", "early"], [hit.content for hit in midnight])
        self.assertEqual([], self.service.exact(day, max_chars=1))


if __name__ == "__main__":
    unittest.main()
