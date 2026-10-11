from __future__ import annotations

import json
import unittest
from datetime import date

from mqelite.domain.events import ConversationExcerpt
from mqelite.domain.l2 import L2Document, L2SourceCoverage, parse_legacy_l2
from mqelite.errors import ValidationError


class StrictL2Tests(unittest.TestCase):
    def test_round_trip_unicode_and_canonical_bytes(self) -> None:
        content = "研究员记得博士 🐈‍⬛\n" + "长文本" * 10_000
        document = L2Document(
            date=date(2026, 7, 31),
            agent_id="synthetic-persona",
            conversation_excerpts=(
                ConversationExcerpt(
                    role="user",
                    content=content,
                    turn_index=0,
                    time="10:04:05",
                    timestamp="2026-07-31T10:04:05+01:00",
                ),
            ),
        )
        raw = document.canonical_bytes()
        self.assertIn("研究员".encode(), raw)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertIn(b'\n  "agent_id": "synthetic-persona",\n', raw)
        self.assertGreater(raw.count(b"\n"), 5)
        decoded = json.loads(raw)
        rebuilt = L2Document.from_dict(decoded, expected_agent_id="synthetic-persona")
        self.assertEqual(document, rebuilt)
        self.assertEqual(raw, rebuilt.canonical_bytes())

    def test_empty_day_is_valid(self) -> None:
        document = L2Document(date=date(2026, 7, 31), agent_id="synthetic-persona")
        self.assertEqual([], json.loads(document.canonical_bytes())["conversation_excerpts"])

    def test_partial_router_coverage_round_trips_without_changing_legacy_shape(self) -> None:
        coverage = L2SourceCoverage(
            start_unix_ns="1785420000000000000",
            end_unix_ns="1785470400000000000",
            router_origin="migration_baseline",
        )
        document = L2Document(
            date=date(2026, 7, 30),
            agent_id="synthetic-persona",
            source_coverage=coverage,
        )
        payload = json.loads(document.canonical_bytes())
        self.assertEqual("router_bootstrap_partial", payload["source_coverage"]["kind"])
        self.assertEqual(document, L2Document.from_dict(payload))
        self.assertNotIn(
            "source_coverage",
            json.loads(L2Document(date(2020, 1, 2), "synthetic-persona").canonical_bytes()),
        )

    def test_partial_router_coverage_is_strictly_validated(self) -> None:
        invalid = (
            {"start_unix_ns": "2", "end_unix_ns": "2", "router_origin": "migration_baseline"},
            {"start_unix_ns": "02", "end_unix_ns": "3", "router_origin": "migration_baseline"},
            {"start_unix_ns": 2, "end_unix_ns": "3", "router_origin": "migration_baseline"},
            {"start_unix_ns": "2", "end_unix_ns": "3", "router_origin": "unknown"},
            {
                "start_unix_ns": "2",
                "end_unix_ns": "3",
                "router_origin": "migration_baseline",
                "kind": "complete_day",
            },
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    L2SourceCoverage(**kwargs)

        payload = {
            "schema_version": "mqelite.l2.v1",
            "date": "2026-07-30",
            "agent_id": "synthetic-persona",
            "conversation_excerpts": [],
            "source_coverage": {
                "kind": "router_bootstrap_partial",
                "start_unix_ns": "2",
                "end_unix_ns": "3",
                "router_origin": "migration_baseline",
                "unexpected": True,
            },
        }
        with self.assertRaises(ValidationError):
            L2Document.from_dict(payload)

    def test_rejects_extra_fields_and_wrong_agent(self) -> None:
        value = {
            "schema_version": "mqelite.l2.v1",
            "date": "2026-07-31",
            "agent_id": "other",
            "conversation_excerpts": [],
            "status": {"trimmed": False},
        }
        with self.assertRaises(ValidationError):
            L2Document.from_dict(value)
        value.pop("status")
        with self.assertRaises(ValidationError):
            L2Document.from_dict(value, expected_agent_id="synthetic-persona")

    def test_rejects_non_contiguous_order_and_naive_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            L2Document(
                date=date(2026, 7, 31),
                agent_id="synthetic-persona",
                conversation_excerpts=(ConversationExcerpt(role="user", content="x", turn_index=1),),
            )
        with self.assertRaises(ValidationError):
            ConversationExcerpt(
                role="assistant",
                content="x",
                turn_index=0,
                timestamp="2026-07-31T10:04:05",
            )

    def test_strict_excerpt_type_and_format_failures(self) -> None:
        invalid = (
            {"role": "tool", "content": "x", "turn_index": 0},
            {"role": "user", "content": 1, "turn_index": 0},
            {"role": "user", "content": "x", "turn_index": -1},
            {"role": "user", "content": "x", "turn_index": 0, "time": 10},
            {"role": "user", "content": "x", "turn_index": 0, "time": "25:00"},
            {"role": "user", "content": "x", "turn_index": 0, "timestamp": 10},
            {"role": "user", "content": "x", "turn_index": 0, "timestamp": "not-a-time"},
            {"role": "user", "content": "x", "turn_index": 0, "message_type": ""},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    ConversationExcerpt(**kwargs)
        for value in (
            {"schema_version": "wrong", "date": "2026-07-31", "agent_id": "x", "conversation_excerpts": []},
            {"schema_version": "mqelite.l2.v1", "date": "invalid", "agent_id": "x", "conversation_excerpts": []},
            {"schema_version": "mqelite.l2.v1", "date": "2026-07-31", "agent_id": "", "conversation_excerpts": []},
            {"schema_version": "mqelite.l2.v1", "date": "2026-07-31", "agent_id": "x", "conversation_excerpts": {}},
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    L2Document.from_dict(value)


class LegacyL2Tests(unittest.TestCase):
    def test_unknown_fields_and_trimmed_status_are_preserved(self) -> None:
        payload = {
            "schema_version": "3.2",
            "date": "2026-07-01",
            "agent_id": "legacy-persona",
            "status": {"trimmed": True, "unknown": {"future": [1, 2]}},
            "unknown_top": {"nested": ["echo"]},
            "conversation_excerpts": [
                {
                    "role": "user",
                    "time": "02:30",
                    "content": "凌晨内容 🐈",
                    "message_type": "text",
                    "turn_index": 7,
                    "future_excerpt": {"nested": True},
                }
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, indent=3).encode("utf-8")
        parsed = parse_legacy_l2(raw)
        self.assertIs(parsed.raw_bytes, raw)
        self.assertTrue(parsed.extensions["status"]["trimmed"])
        self.assertEqual({"nested": True}, parsed.conversation_excerpts[0].extensions["future_excerpt"])
        self.assertEqual("2026-07-02T02:30:00+01:00", parsed.conversation_excerpts[0].timestamp)
        self.assertEqual("凌晨内容 🐈", parsed.conversation_excerpts[0].content)

    def test_date_and_agent_can_come_from_trusted_import_context(self) -> None:
        raw = b'{"conversation_excerpts":[{"content":"old"}]}'
        parsed = parse_legacy_l2(
            raw,
            mapped_agent_id="mapped-persona",
            member_name="days/2025-12-31_l2.json",
        )
        self.assertEqual(date(2025, 12, 31), parsed.date)
        self.assertEqual("unknown", parsed.conversation_excerpts[0].role)
        self.assertEqual("day", parsed.conversation_excerpts[0].time_precision)

    def test_invalid_time_stays_day_precision_without_fabrication(self) -> None:
        raw = b'{"date":"2026-07-01","agent_id":"legacy","conversation_excerpts":[{"content":"old","time":"25:90"}]}'
        parsed = parse_legacy_l2(raw)
        self.assertIsNone(parsed.conversation_excerpts[0].timestamp)
        self.assertEqual("day", parsed.conversation_excerpts[0].time_precision)

    def test_fail_closed_on_missing_core_evidence_or_conflict(self) -> None:
        fixtures = (
            b"[]",
            b'{"date":"2026-07-01","agent_id":"legacy"}',
            b'{"date":"2026-07-01","agent_id":"legacy","conversation_excerpts":[{}]}',
        )
        for raw in fixtures:
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    parse_legacy_l2(raw)
        raw = b'{"date":"2026-07-01","agent_id":"legacy","conversation_excerpts":[]}'
        with self.assertRaises(ValidationError):
            parse_legacy_l2(raw, member_name="2026-07-02_l2.json")
        with self.assertRaises(ValidationError):
            parse_legacy_l2(raw, mapped_agent_id="other")


if __name__ == "__main__":
    unittest.main()
