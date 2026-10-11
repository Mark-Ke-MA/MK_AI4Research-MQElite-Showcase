"""Full archive eligibility beyond the calendar-date predicate."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from mqelite.domain.time_window import archive_month_eligible


@dataclass(frozen=True, slots=True)
class ArchiveReadiness:
    present_dates: frozenset[date]
    pending_conflict: bool = False
    pending_quarantine: bool = False
    coverage_complete: bool = True
    accepted_gaps: bool = False
    index_complete: bool = False
    reconcile_running: bool = False
    import_running: bool = False


def expected_dates(year: int, month: int) -> frozenset[date]:
    return frozenset(date(year, month, day) for day in range(1, calendar.monthrange(year, month)[1] + 1))


def archive_ready(year: int, month: int, cutoff: date, readiness: ArchiveReadiness) -> bool:
    return (
        archive_month_eligible(year, month, cutoff)
        and readiness.present_dates == expected_dates(year, month)
        and not readiness.pending_conflict
        and not readiness.pending_quarantine
        and (readiness.coverage_complete or readiness.accepted_gaps)
        and readiness.index_complete
        and not readiness.reconcile_running
        and not readiness.import_running
    )
