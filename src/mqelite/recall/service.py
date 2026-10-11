"""Read-only bounded recall facade over the rebuildable index."""

from __future__ import annotations

from datetime import date, datetime, time

from mqelite.errors import ValidationError
from mqelite.index.database import RecallIndex, SearchHit


class RecallService:
    def __init__(self, index: RecallIndex) -> None:
        self.index = index

    def exact(
        self,
        memory_date: date,
        *,
        window_start: time | None = None,
        window_end: time | None = None,
        max_chars: int = 20_000,
    ) -> list[SearchHit]:
        if (window_start is None) != (window_end is None):
            raise ValidationError("both exact window endpoints are required")
        if max_chars < 1 or max_chars > 10_000_000:
            raise ValidationError("exact recall max_chars is outside the supported range")
        selected: list[SearchHit] = []
        characters = 0
        for item in self.index.excerpts_for_date(memory_date):
            if window_start is not None:
                if item.timestamp is None or item.time_precision == "day":
                    continue
                try:
                    local_time = datetime.fromisoformat(item.timestamp).timetz().replace(tzinfo=None)
                except ValueError:
                    continue
                if window_start <= window_end:
                    if not window_start <= local_time <= window_end:
                        continue
                elif not (local_time >= window_start or local_time <= window_end):
                    continue
            if characters + len(item.content) > max_chars:
                break
            selected.append(item)
            characters += len(item.content)
        return selected

    def recent(self, *, through: date, days: int = 7, limit: int = 50, max_chars: int = 30_000) -> list[SearchHit]:
        return self.index.recent(through=through, days=days, limit=limit, max_chars=max_chars)

    def search(self, query: str, **kwargs) -> list[SearchHit]:
        return self.index.search(query, **kwargs)
