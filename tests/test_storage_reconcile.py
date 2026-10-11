from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path

from mqelite.domain.events import ConversationExcerpt
from mqelite.domain.l2 import L2Document
from mqelite.errors import StorageError, ValidationError
from mqelite.storage.layout import DataLayout
from mqelite.storage.atomic import atomic_write_bytes
from mqelite.storage.reconcile import DailyReconciler
from mqelite.storage.state import IngestState


def synthetic_document(content: str = "synthetic") -> L2Document:
    return L2Document(
        date=date(2026, 7, 31),
        agent_id="synthetic-persona",
        conversation_excerpts=(
            ConversationExcerpt(
                role="user",
                content=content,
                turn_index=0,
                time="10:00",
                timestamp="2026-07-31T10:00:00+01:00",
            ),
        ),
    )


class FailingState:
    def get(self, memory_date: date):
        return None

    def record(self, state) -> None:
        raise RuntimeError("synthetic state failure")


class StorageReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "data"
        self.persona = DataLayout(self.root).persona("synthetic-persona")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_requires_explicit_absolute_root_and_safe_persona(self) -> None:
        with self.assertRaises(ValidationError):
            DataLayout(Path("relative"))
        with self.assertRaises(ValidationError):
            DataLayout(self.root).persona("../escape")

    def test_private_modes_atomic_content_and_idempotency(self) -> None:
        document = synthetic_document()
        source_hash = hashlib.sha256(b"canonical synthetic source").hexdigest()
        reconciler = DailyReconciler(self.persona)
        first = reconciler.reconcile(document, source_hash=source_hash)
        second = reconciler.reconcile(document, source_hash=source_hash)
        target = self.persona.hot_day(document.date)

        self.assertTrue(first.changed)
        self.assertTrue(first.state_repaired)
        self.assertFalse(second.changed)
        self.assertFalse(second.state_repaired)
        self.assertEqual(document.canonical_bytes(), target.read_bytes())
        self.assertEqual(0o700, stat.S_IMODE(self.root.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(target.parent.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(target.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(self.persona.ingest_db.stat().st_mode))
        self.assertEqual([], list(target.parent.glob(f".{target.name}.tmp-*")))

    def test_canonical_file_survives_state_failure_and_repairs_on_retry(self) -> None:
        document = synthetic_document()
        source_hash = hashlib.sha256(b"canonical synthetic source").hexdigest()
        with self.assertRaisesRegex(RuntimeError, "synthetic state failure"):
            DailyReconciler(self.persona, state=FailingState()).reconcile(
                document, source_hash=source_hash
            )
        target = self.persona.hot_day(document.date)
        self.assertEqual(document.canonical_bytes(), target.read_bytes())

        result = DailyReconciler(self.persona).reconcile(document, source_hash=source_hash)
        self.assertFalse(result.changed)
        self.assertTrue(result.state_repaired)
        state = IngestState(self.persona).get(document.date)
        self.assertEqual(result.content_hash, state.content_hash)

    def test_changed_canonical_history_replaces_daily_file(self) -> None:
        source_one = hashlib.sha256(b"source one").hexdigest()
        source_two = hashlib.sha256(b"source two").hexdigest()
        reconciler = DailyReconciler(self.persona)
        first = reconciler.reconcile(synthetic_document("first"), source_hash=source_one)
        second = reconciler.reconcile(synthetic_document("revised"), source_hash=source_two)
        self.assertTrue(first.changed)
        self.assertTrue(second.changed)
        self.assertIn(b"revised", self.persona.hot_day(date(2026, 7, 31)).read_bytes())
        self.assertNotIn(b"first", self.persona.hot_day(date(2026, 7, 31)).read_bytes())

    def test_symlink_at_managed_parent_or_target_is_rejected(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        data = DataLayout(self.root)
        data.initialize()
        symlink_persona = data.personas_root / "synthetic-persona"
        symlink_persona.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StorageError):
            data.persona("synthetic-persona").initialize()

        symlink_persona.unlink()
        self.persona.initialize()
        target = self.persona.hot_day(date(2026, 7, 31))
        target.parent.mkdir(parents=True, exist_ok=True)
        outside_file = outside / "outside.json"
        outside_file.write_text("unchanged")
        target.symlink_to(outside_file)
        with self.assertRaises(StorageError):
            DailyReconciler(self.persona).reconcile(
                synthetic_document(), source_hash=hashlib.sha256(b"source").hexdigest()
            )
        self.assertEqual("unchanged", outside_file.read_text())

    def test_invalid_source_hash_fails_before_write(self) -> None:
        with self.assertRaises(ValidationError):
            DailyReconciler(self.persona).reconcile(synthetic_document(), source_hash="not-a-hash")
        self.assertFalse(self.persona.hot_day(date(2026, 7, 31)).exists())

    def test_atomic_write_rejects_non_bytes_and_cleans_failed_validation(self) -> None:
        self.persona.initialize()
        target = self.persona.state_root / "atomic-canary.bin"
        with self.assertRaises(StorageError):
            atomic_write_bytes(self.persona, target, "not-bytes")  # type: ignore[arg-type]

        def reject(_: bytes) -> None:
            raise ValidationError("synthetic validator rejection")

        with self.assertRaisesRegex(ValidationError, "synthetic validator rejection"):
            atomic_write_bytes(self.persona, target, b"candidate", validate=reject)
        self.assertFalse(target.exists())
        self.assertEqual([], list(target.parent.glob(f".{target.name}.tmp-*")))


if __name__ == "__main__":
    unittest.main()
