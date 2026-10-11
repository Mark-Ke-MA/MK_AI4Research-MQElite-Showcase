"""Idempotent daily L2 replacement with JSON-first recovery ordering."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mqelite.domain.l2 import L2Document
from mqelite.errors import StorageError, ValidationError
from mqelite.storage.atomic import atomic_write_bytes
from mqelite.storage.layout import PersonaLayout
from mqelite.storage.locking import exclusive_lock
from mqelite.storage.state import DayState, IngestState


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    changed: bool
    state_repaired: bool
    content_hash: str


class DailyReconciler:
    def __init__(self, layout: PersonaLayout, *, state: IngestState | None = None) -> None:
        self.layout = layout
        self.state = state or IngestState(layout)

    def reconcile(self, document: L2Document, *, source_hash: str) -> ReconcileResult:
        if document.agent_id != self.layout.persona_id:
            raise ValidationError("document agent_id does not match persona storage")
        if not isinstance(source_hash, str) or len(source_hash) != 64:
            raise ValidationError("source_hash must be a SHA-256 hex digest")
        try:
            bytes.fromhex(source_hash)
        except ValueError as exc:
            raise ValidationError("source_hash must be a SHA-256 hex digest") from exc
        payload = document.canonical_bytes()
        content_hash = hashlib.sha256(payload).hexdigest()
        target = self.layout.hot_day(document.date)
        self.layout.initialize()
        self.layout.assert_safe(target)

        def validate(staged: bytes) -> None:
            if staged != payload:
                raise StorageError("staged L2 bytes changed before commit")
            import json

            decoded = json.loads(staged)
            L2Document.from_dict(decoded, expected_agent_id=self.layout.persona_id)

        with exclusive_lock(self.layout, self.layout.daily_lock):
            self.layout.assert_safe(target)
            existing = target.read_bytes() if target.exists() else None
            changed = existing != payload
            if changed:
                atomic_write_bytes(self.layout, target, payload, validate=validate)
            expected = DayState(
                memory_date=document.date,
                content_hash=content_hash,
                source_hash=source_hash,
                empty_day=not document.conversation_excerpts,
            )
            before = self.state.get(document.date)
            state_repaired = before != expected
            if state_repaired:
                self.state.record(expected)
        return ReconcileResult(changed=changed, state_repaired=state_repaired, content_hash=content_hash)
