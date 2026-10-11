from __future__ import annotations

import unittest
from datetime import date, datetime, time, timedelta, timezone

from mqelite.domain.time_window import (
    MemoryDayWindow,
    archive_month_eligible,
    hot_cutoff,
    load_timezone,
    memory_date_for,
    month_last_date,
)
from mqelite.errors import ValidationError


class MemoryDayTests(unittest.TestCase):
    def test_boundary_is_start_inclusive_end_exclusive(self) -> None:
        before = datetime.fromisoformat("2026-07-31T02:59:59+01:00")
        start = datetime.fromisoformat("2026-07-31T03:00:00+01:00")
        end = datetime.fromisoformat("2026-08-01T03:00:00+01:00")
        self.assertEqual(date(2026, 7, 30), memory_date_for(before))
        self.assertEqual(date(2026, 7, 31), memory_date_for(start))
        window = MemoryDayWindow.for_date(date(2026, 7, 31))
        self.assertTrue(window.contains(start))
        self.assertFalse(window.contains(end))

    def test_spring_dst_day_has_23_elapsed_hours(self) -> None:
        window = MemoryDayWindow.for_date(date(2026, 3, 28))
        self.assertEqual(23 * 3600, window.elapsed_seconds)
        self.assertEqual(timedelta(0), window.start.utcoffset())
        self.assertEqual(timedelta(hours=1), window.end.utcoffset())

    def test_autumn_dst_day_has_25_elapsed_hours(self) -> None:
        window = MemoryDayWindow.for_date(date(2026, 10, 24))
        self.assertEqual(25 * 3600, window.elapsed_seconds)
        self.assertEqual(timedelta(hours=1), window.start.utcoffset())
        self.assertEqual(timedelta(0), window.end.utcoffset())

    def test_utc_instants_map_across_dst(self) -> None:
        spring_before = datetime(2026, 3, 29, 1, 59, tzinfo=timezone.utc)
        spring_at = datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc)
        self.assertEqual(date(2026, 3, 28), memory_date_for(spring_before))
        self.assertEqual(date(2026, 3, 29), memory_date_for(spring_at))

    def test_naive_instant_fails_closed(self) -> None:
        with self.assertRaises(ValidationError):
            memory_date_for(datetime(2026, 7, 31, 3, 0))

    def test_invalid_timezone_boundary_and_window_instant_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            load_timezone("Etc/Definitely-Not-A-Timezone")
        with self.assertRaises(ValidationError):
            MemoryDayWindow.for_date(date(2026, 7, 31), boundary=time(3, tzinfo=timezone.utc))
        with self.assertRaises(ValidationError):
            MemoryDayWindow.for_date(date(2026, 7, 31)).contains(datetime(2026, 7, 31, 3))


class ArchiveDatePredicateTests(unittest.TestCase):
    def test_cutoff_is_inclusive_hot_and_archive_is_strict(self) -> None:
        current = date(2026, 8, 31)
        cutoff = hot_cutoff(current, 30)
        self.assertEqual(date(2026, 8, 1), cutoff)
        self.assertTrue(archive_month_eligible(2026, 7, cutoff))
        self.assertFalse(archive_month_eligible(2026, 8, cutoff))

    def test_month_lengths_and_cross_year(self) -> None:
        self.assertFalse(archive_month_eligible(2026, 7, date(2026, 7, 31)))
        self.assertTrue(archive_month_eligible(2026, 7, date(2026, 8, 1)))
        self.assertFalse(archive_month_eligible(2026, 12, date(2026, 12, 31)))
        self.assertTrue(archive_month_eligible(2026, 12, date(2027, 1, 1)))
        self.assertFalse(archive_month_eligible(2028, 2, date(2028, 2, 29)))
        self.assertTrue(archive_month_eligible(2028, 2, date(2028, 3, 1)))

    def test_late_july_is_still_inside_august_2_window(self) -> None:
        cutoff = hot_cutoff(date(2026, 8, 2), 30)
        self.assertLessEqual(cutoff, date(2026, 7, 29))
        self.assertFalse(archive_month_eligible(2026, 7, cutoff))

    def test_invalid_hot_window_and_calendar_month_fail_closed(self) -> None:
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                hot_cutoff(date(2026, 8, 1), value)
        for year, month in ((2026, 0), (2026, 13), ("2026", 7)):
            with self.subTest(year=year, month=month), self.assertRaises(ValidationError):
                month_last_date(year, month)


if __name__ == "__main__":
    unittest.main()
