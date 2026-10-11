"""Canonical visible conversation excerpts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mqelite.errors import ValidationError

ALLOWED_ROLES = frozenset({"user", "assistant"})


def _parse_offset_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("excerpt timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("excerpt timestamp must include a UTC offset")
    return parsed


def _validate_time(value: str) -> None:
    formats = ("%H:%M", "%H:%M:%S")
    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            return
        except ValueError:
            pass
    raise ValidationError("excerpt time must be HH:MM or HH:MM:SS")


@dataclass(frozen=True, slots=True)
class ConversationExcerpt:
    """One user-visible persisted message in canonical daily order."""

    role: str
    content: str
    turn_index: int
    time: str | None = None
    timestamp: str | None = None
    message_type: str | None = "text"

    def __post_init__(self) -> None:
        if self.role not in ALLOWED_ROLES:
            raise ValidationError("excerpt role is not supported by the strict writer")
        if not isinstance(self.content, str):
            raise ValidationError("excerpt content must be a string")
        if not isinstance(self.turn_index, int) or isinstance(self.turn_index, bool) or self.turn_index < 0:
            raise ValidationError("excerpt turn_index must be a non-negative integer")
        if self.time is not None:
            if not isinstance(self.time, str):
                raise ValidationError("excerpt time must be a string")
            _validate_time(self.time)
        if self.timestamp is not None:
            if not isinstance(self.timestamp, str):
                raise ValidationError("excerpt timestamp must be a string")
            _parse_offset_timestamp(self.timestamp)
        if self.message_type is not None and (
            not isinstance(self.message_type, str) or not self.message_type
        ):
            raise ValidationError("excerpt message_type must be a non-empty string")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "role": self.role,
            "content": self.content,
            "turn_index": self.turn_index,
        }
        if self.time is not None:
            result["time"] = self.time
        if self.timestamp is not None:
            result["timestamp"] = self.timestamp
        if self.message_type is not None:
            result["message_type"] = self.message_type
        return result
