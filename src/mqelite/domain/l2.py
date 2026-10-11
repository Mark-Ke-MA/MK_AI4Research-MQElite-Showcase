"""Strict MQElite L2 writer and byte-preserving legacy reader."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from mqelite.domain.events import ConversationExcerpt
from mqelite.domain.time_window import DEFAULT_BOUNDARY, DEFAULT_TIMEZONE, load_timezone
from mqelite.errors import ValidationError

L2_SCHEMA_VERSION = "mqelite.l2.v1"
_ROUTER_COVERAGE_ORIGINS = {"exact_initialization", "migration_baseline"}
_UNIX_NS = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _parse_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValidationError("L2 date must be a YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError("L2 date must be a valid calendar date") from exc
    if parsed.isoformat() != value:
        raise ValidationError("L2 date must use canonical YYYY-MM-DD form")
    return parsed


@dataclass(frozen=True, slots=True)
class L2SourceCoverage:
    """Explicit provenance for the one allowed Router bootstrap partial day."""

    start_unix_ns: str
    end_unix_ns: str
    router_origin: str
    kind: str = "router_bootstrap_partial"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind != "router_bootstrap_partial":
            raise ValidationError("L2 source coverage kind is unsupported")
        if not isinstance(self.router_origin, str) or self.router_origin not in _ROUTER_COVERAGE_ORIGINS:
            raise ValidationError("L2 source coverage Router origin is invalid")
        if (
            not isinstance(self.start_unix_ns, str)
            or _UNIX_NS.fullmatch(self.start_unix_ns) is None
            or not isinstance(self.end_unix_ns, str)
            or _UNIX_NS.fullmatch(self.end_unix_ns) is None
            or int(self.start_unix_ns) >= int(self.end_unix_ns)
        ):
            raise ValidationError("L2 source coverage interval is invalid")

    @classmethod
    def from_dict(cls, value: object) -> "L2SourceCoverage":
        if not isinstance(value, dict) or set(value) != {
            "kind",
            "start_unix_ns",
            "end_unix_ns",
            "router_origin",
        }:
            raise ValidationError("L2 source coverage fields are invalid")
        return cls(
            kind=value["kind"],
            start_unix_ns=value["start_unix_ns"],
            end_unix_ns=value["end_unix_ns"],
            router_origin=value["router_origin"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "start_unix_ns": self.start_unix_ns,
            "end_unix_ns": self.end_unix_ns,
            "router_origin": self.router_origin,
        }


@dataclass(frozen=True, slots=True)
class L2Document:
    date: date
    agent_id: str
    conversation_excerpts: tuple[ConversationExcerpt, ...] = ()
    source_coverage: L2SourceCoverage | None = None
    schema_version: str = L2_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != L2_SCHEMA_VERSION:
            raise ValidationError("strict writer schema_version mismatch")
        if not isinstance(self.agent_id, str) or not self.agent_id.strip():
            raise ValidationError("agent_id must be a non-empty string")
        if self.source_coverage is not None and not isinstance(self.source_coverage, L2SourceCoverage):
            raise ValidationError("source_coverage must be an L2SourceCoverage value")
        if any(item.turn_index != index for index, item in enumerate(self.conversation_excerpts)):
            raise ValidationError("turn_index must be contiguous and zero-based")

    @classmethod
    def from_dict(cls, value: Mapping[str, object], *, expected_agent_id: str | None = None) -> "L2Document":
        required = {"schema_version", "date", "agent_id", "conversation_excerpts"}
        if not required <= set(value) or not set(value) <= required | {"source_coverage"}:
            raise ValidationError("strict L2 top-level fields do not match the writer schema")
        excerpts_value = value.get("conversation_excerpts")
        if not isinstance(excerpts_value, list):
            raise ValidationError("conversation_excerpts must be a list")
        excerpts: list[ConversationExcerpt] = []
        allowed_excerpt = {"role", "content", "turn_index", "time", "timestamp", "message_type"}
        required_excerpt = {"role", "content", "turn_index"}
        for raw in excerpts_value:
            if not isinstance(raw, dict) or not required_excerpt <= set(raw) or not set(raw) <= allowed_excerpt:
                raise ValidationError("strict excerpt fields do not match the writer schema")
            excerpts.append(ConversationExcerpt(**raw))
        agent_id = value.get("agent_id")
        if not isinstance(agent_id, str):
            raise ValidationError("agent_id must be a string")
        if expected_agent_id is not None and agent_id != expected_agent_id:
            raise ValidationError("agent_id does not match persona-scoped writer")
        return cls(
            schema_version=value.get("schema_version"),
            date=_parse_date(value.get("date")),
            agent_id=agent_id,
            conversation_excerpts=tuple(excerpts),
            source_coverage=(
                L2SourceCoverage.from_dict(value["source_coverage"])
                if "source_coverage" in value
                else None
            ),
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "date": self.date.isoformat(),
            "agent_id": self.agent_id,
            "conversation_excerpts": [item.to_dict() for item in self.conversation_excerpts],
        }
        if self.source_coverage is not None:
            value["source_coverage"] = self.source_coverage.to_dict()
        return value

    def canonical_bytes(self) -> bytes:
        return (json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )


@dataclass(frozen=True, slots=True)
class LegacyExcerpt:
    role: str
    content: str
    turn_index: int
    time: str | None
    timestamp: str | None
    message_type: str | None
    time_precision: str
    extensions: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LegacyL2Document:
    date: date
    agent_id: str
    conversation_excerpts: tuple[LegacyExcerpt, ...]
    raw_bytes: bytes
    extensions: Mapping[str, Any]


def _date_from_member_name(member_name: str | None) -> date | None:
    if member_name is None:
        return None
    name = PurePosixPath(member_name).name
    candidates = (name[:10], name.removesuffix("_l2.json"))
    for candidate in candidates:
        try:
            return _parse_date(candidate)
        except ValidationError:
            pass
    return None


def _legacy_timestamp(day: date, display_time: str, timezone: str, boundary: time) -> str | None:
    formats = ("%H:%M:%S", "%H:%M")
    parsed_time: time | None = None
    for fmt in formats:
        try:
            parsed_time = datetime.strptime(display_time, fmt).time()
            break
        except ValueError:
            pass
    if parsed_time is None:
        return None
    local_date = day + timedelta(days=1) if parsed_time < boundary else day
    value = datetime.combine(local_date, parsed_time, tzinfo=load_timezone(timezone))
    return value.isoformat()


def parse_legacy_l2(
    raw_bytes: bytes,
    *,
    mapped_agent_id: str | None = None,
    member_name: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    boundary: time = DEFAULT_BOUNDARY,
) -> LegacyL2Document:
    """Parse a legacy view without altering or reserializing its source bytes."""

    if not isinstance(raw_bytes, bytes):
        raise ValidationError("legacy payload must be bytes")
    try:
        decoded = raw_bytes.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("legacy payload is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("legacy L2 must be a JSON object")

    explicit_date: date | None = None
    if "date" in payload:
        explicit_date = _parse_date(payload["date"])
    inferred_date = _date_from_member_name(member_name)
    if explicit_date is not None and inferred_date is not None and explicit_date != inferred_date:
        raise ValidationError("legacy date conflicts with trusted member name")
    document_date = explicit_date or inferred_date
    if document_date is None:
        raise ValidationError("legacy date is missing and cannot be inferred")

    embedded_agent = payload.get("agent_id")
    if embedded_agent is not None and (not isinstance(embedded_agent, str) or not embedded_agent.strip()):
        raise ValidationError("legacy agent_id is invalid")
    if mapped_agent_id is not None and embedded_agent and mapped_agent_id != embedded_agent:
        raise ValidationError("legacy agent_id conflicts with explicit mapping")
    agent_id = mapped_agent_id or embedded_agent
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValidationError("legacy persona mapping is missing")

    raw_excerpts = payload.get("conversation_excerpts")
    if not isinstance(raw_excerpts, list):
        raise ValidationError("legacy conversation_excerpts must be a list")
    excerpts: list[LegacyExcerpt] = []
    known_excerpt = {"role", "content", "turn_index", "time", "timestamp", "message_type"}
    for index, raw_excerpt in enumerate(raw_excerpts):
        if not isinstance(raw_excerpt, dict):
            raise ValidationError("legacy excerpt must be an object")
        content = raw_excerpt.get("content")
        if not isinstance(content, str):
            raise ValidationError("legacy excerpt content must be a string")
        role = raw_excerpt.get("role", "unknown")
        if not isinstance(role, str) or not role:
            role = "unknown"
        turn_index = raw_excerpt.get("turn_index", index)
        if not isinstance(turn_index, int) or isinstance(turn_index, bool) or turn_index < 0:
            turn_index = index
        display_time = raw_excerpt.get("time") if isinstance(raw_excerpt.get("time"), str) else None
        timestamp = raw_excerpt.get("timestamp") if isinstance(raw_excerpt.get("timestamp"), str) else None
        precision = "minute" if display_time else "day"
        if timestamp is None and display_time is not None:
            timestamp = _legacy_timestamp(document_date, display_time, timezone, boundary)
            if timestamp is None:
                precision = "day"
        elif timestamp is not None:
            try:
                parsed_timestamp = datetime.fromisoformat(timestamp)
                if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
                    raise ValueError
                precision = "second"
            except ValueError:
                timestamp = None
                precision = "day" if display_time is None else precision
        excerpts.append(
            LegacyExcerpt(
                role=role,
                content=content,
                turn_index=turn_index,
                time=display_time,
                timestamp=timestamp,
                message_type=raw_excerpt.get("message_type") if isinstance(raw_excerpt.get("message_type"), str) else None,
                time_precision=precision,
                extensions=MappingProxyType({key: value for key, value in raw_excerpt.items() if key not in known_excerpt}),
            )
        )
    known_top = {"schema_version", "date", "agent_id", "conversation_excerpts"}
    return LegacyL2Document(
        date=document_date,
        agent_id=agent_id,
        conversation_excerpts=tuple(excerpts),
        raw_bytes=raw_bytes,
        extensions=MappingProxyType({key: value for key, value in payload.items() if key not in known_top}),
    )
