"""The named surgery-date ranges of the patient list: calendar dates in the clinic's time zone
(America/Toronto), weeks Monday to Sunday, months by calendar. Pure functions, so every boundary
and clock-change week is checked with a pinned instant; the database side is in
tests/db/test_patient_list.py."""

from datetime import UTC, date, datetime

import pytest

from clinically_anchored_api.core.patient_list import SurgeryRange, month_bounds, resolve_range

W, LW = SurgeryRange.THIS_WEEK, SurgeryRange.LAST_WEEK
M, LM = SurgeryRange.THIS_MONTH, SurgeryRange.LAST_MONTH


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    "now, name, want",
    [
        # An ordinary Wednesday, 2026-10-14.
        (utc(2026, 10, 14, 15, 0), W, (date(2026, 10, 12), date(2026, 10, 18))),
        (utc(2026, 10, 14, 15, 0), LW, (date(2026, 10, 5), date(2026, 10, 11))),
        (utc(2026, 10, 14, 15, 0), M, (date(2026, 10, 1), date(2026, 10, 31))),
        (utc(2026, 10, 14, 15, 0), LM, (date(2026, 9, 1), date(2026, 9, 30))),
        # Sunday 23:30 in Toronto is already Monday in UTC (EDT, October): still the old week.
        (utc(2026, 10, 19, 3, 30), W, (date(2026, 10, 12), date(2026, 10, 18))),
        (utc(2026, 10, 19, 3, 30), LW, (date(2026, 10, 5), date(2026, 10, 11))),
        # Monday 00:30 in Toronto: the new week.
        (utc(2026, 10, 19, 4, 30), W, (date(2026, 10, 19), date(2026, 10, 25))),
        (utc(2026, 10, 19, 4, 30), LW, (date(2026, 10, 12), date(2026, 10, 18))),
        # Month end: 31 Oct 23:30 EDT is 1 Nov 03:30 UTC; 1 Nov 00:30 EDT is 04:30 UTC.
        (utc(2026, 11, 1, 3, 30), M, (date(2026, 10, 1), date(2026, 10, 31))),
        (utc(2026, 11, 1, 3, 30), LM, (date(2026, 9, 1), date(2026, 9, 30))),
        (utc(2026, 11, 1, 4, 30), M, (date(2026, 11, 1), date(2026, 11, 30))),
        (utc(2026, 11, 1, 4, 30), LM, (date(2026, 10, 1), date(2026, 10, 31))),
        # Winter month end (EST): 31 Jan 23:30 is 1 Feb 04:30 UTC.
        (utc(2027, 2, 1, 4, 30), M, (date(2027, 1, 1), date(2027, 1, 31))),
        (utc(2027, 2, 1, 5, 30), M, (date(2027, 2, 1), date(2027, 2, 28))),
        # Spring forward, Sunday 2025-03-09 (the week is still seven calendar days).
        (utc(2025, 3, 10, 3, 30), W, (date(2025, 3, 3), date(2025, 3, 9))),  # Sun 23:30 EDT
        (utc(2025, 3, 10, 4, 30), W, (date(2025, 3, 10), date(2025, 3, 16))),  # Mon 00:30 EDT
        (utc(2025, 3, 5, 17, 0), W, (date(2025, 3, 3), date(2025, 3, 9))),  # Wed, week of change
        (utc(2025, 3, 12, 17, 0), LW, (date(2025, 3, 3), date(2025, 3, 9))),  # the week after
        # Fall back, Sunday 2025-11-02.
        (utc(2025, 11, 3, 4, 30), W, (date(2025, 10, 27), date(2025, 11, 2))),  # Sun 23:30 EST
        (utc(2025, 11, 3, 5, 30), W, (date(2025, 11, 3), date(2025, 11, 9))),  # Mon 00:30 EST
        (utc(2025, 10, 30, 17, 0), W, (date(2025, 10, 27), date(2025, 11, 2))),  # spans Nov
        (utc(2025, 10, 30, 17, 0), LW, (date(2025, 10, 20), date(2025, 10, 26))),
        # A week that straddles a month and a year, and last month across New Year / leap day.
        (utc(2026, 12, 31, 17, 0), W, (date(2026, 12, 28), date(2027, 1, 3))),
        (utc(2027, 1, 4, 17, 0), LM, (date(2026, 12, 1), date(2026, 12, 31))),
        (utc(2028, 3, 10, 17, 0), LM, (date(2028, 2, 1), date(2028, 2, 29))),
    ],
)
def test_named_ranges_are_calendar_dates_in_toronto(now, name, want):
    assert resolve_range(name, now) == want


def test_month_bounds_handle_leap_years_and_year_ends():
    assert month_bounds(2028, 2) == (date(2028, 2, 1), date(2028, 2, 29))
    assert month_bounds(2027, 2) == (date(2027, 2, 1), date(2027, 2, 28))
    assert month_bounds(2026, 12) == (date(2026, 12, 1), date(2026, 12, 31))
