from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from mqelite.archive import (
    ArchiveDay,
    ArchiveReadiness,
    archive_ready,
    build_archive,
    commit_archive,
    verify_archive,
)
from mqelite.domain.events import ConversationExcerpt
from mqelite.domain.l2 import L2Document
from mqelite.errors import StorageError, ValidationError
from mqelite.storage.layout import DataLayout


def native_day(day: date, content: str = "synthetic") -> ArchiveDay:
    document = L2Document(
        date=day,
        agent_id="synthetic-persona",
        conversation_excerpts=(ConversationExcerpt(role="user", content=content, turn_index=0),),
    )
    return ArchiveDay(day, document.canonical_bytes(), "native")


class ArchiveTests(unittest.TestCase):
    def test_archive_day_and_build_inputs_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            ArchiveDay(date(2026, 7, 1), b"{}", "unsupported")
        with self.assertRaises(ValidationError):
            ArchiveDay(date(2026, 7, 1), "{}", "native")  # type: ignore[arg-type]

        base = dict(
            persona_id="synthetic-persona",
            year=2026,
            month=7,
            generation=1,
            created_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            days=[native_day(date(2026, 7, 1))],
            tool_version="0.0.0-rc",
        )
        for replacement in (
            {"generation": 0},
            {"created_at": datetime(2026, 8, 5)},
            {"days": []},
            {"days": [native_day(date(2026, 7, 1)), native_day(date(2026, 7, 1))]},
            {"days": [native_day(date(2026, 8, 1))]},
            {"days": [ArchiveDay(date(2026, 7, 1), b"not-json", "native")]},
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValidationError):
                build_archive(**(base | replacement))

        noncanonical = json.dumps(
            {
                "schema_version": "mqelite.l2.v1",
                "date": "2026-07-01",
                "agent_id": "synthetic-persona",
                "conversation_excerpts": [{"role": "user", "content": "x", "turn_index": 0}],
            },
            indent=2,
        ).encode()
        with self.assertRaises(ValidationError):
            build_archive(**(base | {"days": [ArchiveDay(date(2026, 7, 1), noncanonical, "native")]}))

        legacy_wrong_date = json.dumps(
            {"date": "2026-07-02", "agent_id": "synthetic-persona", "conversation_excerpts": []}
        ).encode()
        with self.assertRaises(ValidationError):
            build_archive(
                **(base | {"days": [ArchiveDay(date(2026, 7, 1), legacy_wrong_date, "legacy_mqe")]})
            )

    def test_build_is_reproducible_and_verifiable(self) -> None:
        created = datetime(2026, 8, 5, 12, tzinfo=timezone.utc)
        args = dict(
            persona_id="synthetic-persona",
            year=2026,
            month=7,
            generation=1,
            created_at=created,
            days=[native_day(date(2026, 7, 1)), native_day(date(2026, 7, 2), "研究员 🐈")],
            tool_version="0.0.0-rc",
        )
        first = build_archive(**args)
        second = build_archive(**args)
        self.assertEqual(first, second)
        manifest = verify_archive(first.tar_bytes, first.external_manifest, expected_persona_id="synthetic-persona")
        self.assertEqual("native", manifest["source_type"])
        self.assertEqual(2, len(manifest["members"]))
        with tarfile.open(fileobj=io.BytesIO(first.tar_bytes), mode="r:gz") as archive:
            for info in archive.getmembers():
                self.assertEqual(0, info.uid)
                self.assertEqual(0, info.gid)
                self.assertEqual("", info.uname)
                self.assertEqual("", info.gname)
                self.assertEqual(0, info.mtime)
                self.assertFalse(info.name.startswith("/"))

    def test_legacy_member_bytes_are_preserved_exactly(self) -> None:
        legacy = json.dumps(
            {
                "date": "2026-07-03",
                "agent_id": "synthetic-persona",
                "status": {"trimmed": True},
                "conversation_excerpts": [{"role": "user", "content": "旧内容"}],
            },
            ensure_ascii=False,
            indent=4,
        ).encode()
        bundle = build_archive(
            persona_id="synthetic-persona",
            year=2026,
            month=7,
            generation=2,
            created_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            days=[ArchiveDay(date(2026, 7, 3), legacy, "legacy_mqe")],
            tool_version="0.0.0-rc",
            supersedes_generation=1,
        )
        verify_archive(bundle.tar_bytes, bundle.external_manifest)
        with tarfile.open(fileobj=io.BytesIO(bundle.tar_bytes), mode="r:gz") as archive:
            archived = archive.extractfile("days/2026-07-03_l2.json").read()
        self.assertEqual(legacy, archived)

    def test_tamper_and_unsafe_tar_are_rejected(self) -> None:
        bundle = build_archive(
            persona_id="synthetic-persona",
            year=2026,
            month=7,
            generation=1,
            created_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            days=[native_day(date(2026, 7, 1))],
            tool_version="0.0.0-rc",
        )
        tampered = bytearray(bundle.tar_bytes)
        tampered[-10] ^= 1
        with self.assertRaises(ValidationError):
            verify_archive(bytes(tampered), bundle.external_manifest)

        raw_tar = io.BytesIO()
        internal = b"{}\n"
        attack = b"synthetic"
        with tarfile.open(fileobj=raw_tar, mode="w") as archive:
            info = tarfile.TarInfo("manifest.json")
            info.size = len(internal)
            archive.addfile(info, io.BytesIO(internal))
            info = tarfile.TarInfo("../escape")
            info.size = len(attack)
            archive.addfile(info, io.BytesIO(attack))
        compressed = gzip.compress(raw_tar.getvalue(), mtime=0)
        external = json.dumps(
            {
                "tar_sha256": hashlib.sha256(compressed).hexdigest(),
                "members": [{"name": "../escape", "size": len(attack), "sha256": hashlib.sha256(attack).hexdigest()}],
            }
        ).encode()
        with self.assertRaises(ValidationError):
            verify_archive(compressed, external)

    def test_manifest_shape_and_persona_mismatches_fail_closed(self) -> None:
        bundle = build_archive(
            persona_id="synthetic-persona",
            year=2026,
            month=7,
            generation=1,
            created_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            days=[native_day(date(2026, 7, 1))],
            tool_version="0.0.0-rc",
        )
        with self.assertRaises(ValidationError):
            verify_archive(bundle.tar_bytes, b"not-json")
        with self.assertRaises(ValidationError):
            verify_archive(bundle.tar_bytes, bundle.external_manifest, expected_persona_id="other")

        external = json.loads(bundle.external_manifest)
        external["members"] = "not-a-list"
        external["tar_sha256"] = hashlib.sha256(bundle.tar_bytes).hexdigest()
        with self.assertRaises(ValidationError):
            verify_archive(bundle.tar_bytes, json.dumps(external).encode())

        external = json.loads(bundle.external_manifest)
        external["members"].append(dict(external["members"][0]))
        with self.assertRaises(ValidationError):
            verify_archive(bundle.tar_bytes, json.dumps(external).encode())

    def test_commit_is_private_and_generation_is_immutable(self) -> None:
        bundle = build_archive(
            persona_id="synthetic-persona",
            year=2026,
            month=7,
            generation=1,
            created_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            days=[native_day(date(2026, 7, 1))],
            tool_version="0.0.0-rc",
        )
        with tempfile.TemporaryDirectory() as temporary:
            layout = DataLayout(Path(temporary) / "data").persona("synthetic-persona")
            tar_name, manifest_name = commit_archive(layout, bundle)
            tar_path = layout.archive_root / "2026" / tar_name
            manifest_path = layout.archive_root / "2026" / manifest_name
            self.assertEqual(bundle.tar_bytes, tar_path.read_bytes())
            self.assertEqual(0o600, tar_path.stat().st_mode & 0o777)
            self.assertEqual(0o600, manifest_path.stat().st_mode & 0o777)
            self.assertEqual((tar_name, manifest_name), commit_archive(layout, bundle))
            different = build_archive(
                persona_id="synthetic-persona",
                year=2026,
                month=7,
                generation=1,
                created_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
                days=[native_day(date(2026, 7, 1), "different")],
                tool_version="0.0.0-rc",
            )
            with self.assertRaises(StorageError):
                commit_archive(layout, different)


class ArchiveEligibilityTests(unittest.TestCase):
    def test_requires_complete_month_index_and_no_pending_work(self) -> None:
        dates = frozenset(date(2026, 7, day) for day in range(1, 32))
        ready = ArchiveReadiness(present_dates=dates, index_complete=True)
        self.assertTrue(archive_ready(2026, 7, date(2026, 8, 1), ready))
        self.assertFalse(archive_ready(2026, 7, date(2026, 7, 31), ready))
        self.assertFalse(
            archive_ready(
                2026,
                7,
                date(2026, 8, 1),
                ArchiveReadiness(present_dates=dates, index_complete=True, pending_conflict=True),
            )
        )
        self.assertFalse(
            archive_ready(
                2026,
                7,
                date(2026, 8, 1),
                ArchiveReadiness(present_dates=dates - {date(2026, 7, 5)}, index_complete=True),
            )
        )


if __name__ == "__main__":
    unittest.main()
