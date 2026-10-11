"""Durable polling adapter for canonical persisted app-server turns."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from mqelite.errors import ValidationError
from mqelite.codex.transport import WebSocketMessageTooLarge


class RpcRequester(Protocol):
    def request(self, method: str, params: dict[str, Any], *, timeout: float = 20.0) -> dict[str, Any]: ...

    def reset(self) -> None: ...


class MainIdentity(Protocol):
    persona_id: str
    generation: int
    thread_id: str


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    durable_polling: bool
    reliable_turn_timestamps: bool
    full_persisted_items: bool
    pagination: bool


@dataclass(frozen=True, slots=True)
class NormalizedConversationEvent:
    stable_digest: str
    canonical_order: tuple[int, int, int]
    role: str
    timestamp: datetime | None
    content: str
    message_type: str = "text"


@dataclass(frozen=True, slots=True)
class SourcePage:
    events: tuple[NormalizedConversationEvent, ...]
    next_cursor: str | None


def _epoch_nanoseconds(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValidationError("persisted turn timestamp is missing or invalid")
    if value <= 10_000_000_000:
        return value * 1_000_000_000
    if value <= 10_000_000_000_000:
        return value * 1_000_000
    raise ValidationError("persisted turn timestamp unit is unsupported")


def _optional_epoch_nanoseconds(value: object) -> int | None:
    if value is None:
        return None
    return _epoch_nanoseconds(value)


def _datetime_from_unix_ns(value: int, timezone: ZoneInfo) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone).replace(microsecond=nanoseconds // 1_000)


def _visible_text(item: dict[str, Any]) -> tuple[str, str] | None:
    item_type = item.get("type")
    if item_type == "agentMessage":
        text = item.get("text")
        return ("assistant", text) if isinstance(text, str) and text else None
    if item_type != "userMessage":
        return None
    content = item.get("content")
    if not isinstance(content, list):
        return None
    text_parts = [
        block.get("text")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    text = "\n".join(part for part in text_parts if part)
    return ("user", text) if text else None


def _turn_metadata(turn: dict[str, Any]) -> tuple[str, str, int | None, int | None]:
    status = turn.get("status")
    if status not in {"inProgress", "completed", "interrupted", "failed"}:
        raise ValidationError("persisted turn status is missing or unsupported")
    turn_id = turn.get("id")
    if not isinstance(turn_id, str) or not turn_id:
        raise ValidationError("persisted turn stable identity is missing")
    started = _optional_epoch_nanoseconds(turn.get("startedAt"))
    completed = _optional_epoch_nanoseconds(turn.get("completedAt"))
    abrupt_interruption = status == "interrupted" and completed is None
    if status == "inProgress":
        if completed is not None:
            raise ValidationError("in-progress turn unexpectedly has a completion timestamp")
        if started is None:
            return status, turn_id, started, completed
    elif abrupt_interruption:
        if started is None:
            raise ValidationError("abrupt interrupted turn start timestamp is missing or invalid")
    else:
        if started is None or completed is None:
            raise ValidationError("terminal turn timestamp is missing or invalid")
        if completed < started:
            raise ValidationError("terminal turn completion precedes its start")
    return status, turn_id, started, completed


class SourceSnapshotChanged(ValidationError):
    """A bounded read could not retain consistent turn metadata."""


@dataclass(frozen=True, slots=True)
class _MetadataPage:
    anchor: str | None
    next_cursor: str | None
    turns: tuple[dict[str, Any], ...]


class DurablePollingSource:
    def __init__(
        self,
        rpc: RpcRequester,
        *,
        persona_id: str,
        digest_key: bytes,
        timezone: str = "Europe/London",
        page_size: int = 100,
        max_metadata_pages: int = 10_000,
    ) -> None:
        if len(digest_key) < 16:
            raise ValidationError("source digest key must be at least 16 bytes")
        if page_size < 1 or page_size > 500:
            raise ValidationError("source page size is outside the supported range")
        self.rpc = rpc
        self.persona_id = persona_id
        self.digest_key = digest_key
        self.timezone = ZoneInfo(timezone)
        self.page_size = page_size
        if max_metadata_pages < 1:
            raise ValidationError("metadata page bound must be positive")
        self.max_metadata_pages = max_metadata_pages
        self._metadata: dict[str, tuple[_MetadataPage, ...]] = {}
        self._batch_limit = page_size
        self.stats = dict(metadata_requests=0, full_requests=0, full_turns=0,
                          oversized_retries=0, snapshot_refreshes=0)

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(True, True, True, True)

    def _digest(self, raw_id: str) -> str:
        return hashlib.blake2b(raw_id.encode(), key=self.digest_key, digest_size=16).hexdigest()

    def normalize_page(
        self,
        result: dict[str, Any],
        *,
        main: MainIdentity,
        start_unix_ns: int,
        end_unix_ns: int,
        turn_position_offset: int = 0,
    ) -> SourcePage:
        if main.persona_id != self.persona_id:
            raise ValidationError("conversation source persona mismatch")
        if (
            not isinstance(start_unix_ns, int)
            or isinstance(start_unix_ns, bool)
            or not isinstance(end_unix_ns, int)
            or isinstance(end_unix_ns, bool)
            or start_unix_ns < 0
            or start_unix_ns >= end_unix_ns
        ):
            raise ValidationError("conversation source nanosecond window is invalid")
        turns = result.get("data")
        if not isinstance(turns, list):
            raise ValidationError("thread turn page has an invalid shape")
        events: list[NormalizedConversationEvent] = []
        for turn_position, turn in enumerate(turns, start=turn_position_offset):
            if not isinstance(turn, dict):
                raise ValidationError("persisted turn is not an object")
            status, turn_id, started, completed = _turn_metadata(turn)
            items = turn.get("items")
            if not isinstance(items, list):
                raise ValidationError("persisted turn does not contain full items")
            if started is None:
                continue
            abrupt_interruption = status == "interrupted" and completed is None
            assert started is not None
            for item_position, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                visible = _visible_text(item)
                if visible is None:
                    continue
                role, content = visible
                if status == "inProgress" and role == "assistant":
                    continue
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    raise ValidationError("persisted visible item stable identity is missing")
                untimed_interrupted_assistant = abrupt_interruption and role == "assistant"
                timestamp_ns = None if untimed_interrupted_assistant else (started if role == "user" else completed)
                inclusion_ns = started if timestamp_ns is None else timestamp_ns
                if not start_unix_ns <= inclusion_ns < end_unix_ns:
                    continue
                timestamp = (
                    None
                    if timestamp_ns is None
                    else _datetime_from_unix_ns(timestamp_ns, self.timezone)
                )
                stable = self._digest(f"{main.generation}\0{turn_id}\0{item_id}\0{item_position}")
                events.append(
                    NormalizedConversationEvent(
                        stable_digest=stable,
                        canonical_order=(started, turn_position, item_position),
                        role=role,
                        timestamp=timestamp,
                        content=content,
                        message_type=(
                            "text_untimed_interrupted"
                            if untimed_interrupted_assistant
                            else "text"
                        ),
                    )
                )
        events.sort(key=lambda item: item.canonical_order)
        next_cursor = result.get("nextCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ValidationError("thread page cursor has an invalid shape")
        return SourcePage(tuple(events), next_cursor)

    def _request_page(self, thread_id: str, view: str, limit: int,
                      cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = dict(threadId=thread_id, limit=limit,
                                     sortDirection="asc", itemsView=view)
        if cursor is not None:
            params["cursor"] = cursor
        self.stats["metadata_requests" if view == "notLoaded" else "full_requests"] += 1
        result = self.rpc.request("thread/turns/list", params)
        data = result.get("data")
        following = result.get("nextCursor")
        if (not isinstance(data, list) or len(data) > limit
                or any(not isinstance(turn, dict) for turn in data)):
            raise ValidationError("thread turn page has an invalid shape")
        if following is not None and (not isinstance(following, str) or not following):
            raise ValidationError("thread page cursor has an invalid shape")
        if following is not None and (not data or following == cursor):
            raise ValidationError("thread pagination did not advance")
        return result

    def _inventory(self, thread_id: str) -> tuple[_MetadataPage, ...]:
        cached = self._metadata.get(thread_id)
        if cached is not None:
            return cached
        pages = []
        cursor = None
        seen_cursors: set[str] = set()
        seen_turns: set[str] = set()
        for _ in range(self.max_metadata_pages):
            result = self._request_page(thread_id, "notLoaded", self.page_size, cursor)
            turns = []
            for turn in result["data"]:
                _status, identity, _started, _completed = _turn_metadata(turn)
                if identity in seen_turns:
                    raise SourceSnapshotChanged("turn inventory contains repeated identities")
                seen_turns.add(identity)
                turns.append({field: turn.get(field) for field in
                              ("id", "status", "startedAt", "completedAt")})
            following = result.get("nextCursor")
            pages.append(_MetadataPage(cursor, following, tuple(turns)))
            if following is None:
                snapshot = tuple(pages)
                self._metadata[thread_id] = snapshot
                return snapshot
            if following in seen_cursors:
                raise ValidationError("thread metadata cursor repeated")
            seen_cursors.add(following)
            cursor = following
        raise ValidationError("thread metadata exceeded the page safety limit")

    @staticmethod
    def _same_metadata(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return all(left.get(field) == right.get(field) for field in
                   ("id", "status", "startedAt", "completedAt"))

    def _read_window(self, main: MainIdentity, start: int, end: int) -> SourcePage:
        events: list[NormalizedConversationEvent] = []
        for page in self._inventory(main.thread_id):
            selected = [any(value is not None and start <= value < end for value in
                            _turn_metadata(turn)[2:]) for turn in page.turns]
            page_events: list[NormalizedConversationEvent] = []
            offset = 0
            cursor = page.anchor
            positioned = 0
            while offset < len(page.turns):
                if not selected[offset]:
                    offset += 1
                    continue
                if positioned != offset:
                    prefix = self._request_page(main.thread_id, "notLoaded", offset, page.anchor)
                    if (len(prefix["data"]) != offset or any(
                            not self._same_metadata(left, right)
                            for left, right in zip(prefix["data"], page.turns[:offset]))):
                        raise SourceSnapshotChanged("turn inventory changed during positioning")
                    cursor = prefix.get("nextCursor")
                    if cursor is None:
                        raise SourceSnapshotChanged("selected turn cursor is no longer available")
                count = 1
                while (offset + count < len(page.turns) and selected[offset + count]
                       and count < self._batch_limit):
                    count += 1
                while True:
                    try:
                        result = self._request_page(main.thread_id, "full", count, cursor)
                        break
                    except WebSocketMessageTooLarge:
                        if count == 1:
                            raise
                        self.rpc.reset()
                        count = max(1, count // 2)
                        self._batch_limit = count
                        self.stats["oversized_retries"] += 1
                data = result["data"]
                if not data:
                    raise SourceSnapshotChanged("selected turns disappeared during reading")
                for position, turn in enumerate(data, offset):
                    if not self._same_metadata(turn, page.turns[position]):
                        raise SourceSnapshotChanged("turn metadata changed during reading")
                    if not isinstance(turn.get("items"), list):
                        raise ValidationError("persisted turn does not contain full items")
                normalized = self.normalize_page(
                    {"data": data}, main=main, start_unix_ns=start, end_unix_ns=end,
                    turn_position_offset=offset)
                page_events.extend(normalized.events)
                self.stats["full_turns"] += len(data)
                offset += len(data)
                cursor = result.get("nextCursor")
                positioned = offset
                if cursor is None and (offset < len(page.turns) or page.next_cursor is not None):
                    raise SourceSnapshotChanged("turn inventory shortened during reading")
            page_events.sort(key=lambda event: event.canonical_order)
            events.extend(page_events)
        return SourcePage(tuple(events), None)

    def fetch_canonical_events(
        self, *, main: MainIdentity, start_unix_ns: int, end_unix_ns: int,
        cursor: str | None = None,
    ) -> SourcePage:
        self.normalize_page({"data": []}, main=main, start_unix_ns=start_unix_ns,
                            end_unix_ns=end_unix_ns)
        if cursor is not None:
            raise ValidationError("date-first source returns a complete window")
        cached = self._metadata.get(main.thread_id, ())
        if any(turn["status"] == "inProgress" for page in cached for turn in page.turns):
            self._metadata.pop(main.thread_id, None)
        for attempt in range(2):
            try:
                return self._read_window(main, start_unix_ns, end_unix_ns)
            except SourceSnapshotChanged:
                self._metadata.pop(main.thread_id, None)
                if attempt:
                    raise
                self.stats["snapshot_refreshes"] += 1
        raise AssertionError("unreachable")
