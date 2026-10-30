"""The clinic's patient list: which surgery dates a named range means.

Ranges are calendar dates in the clinic's time zone, resolved from the api's clock, so "this
week" does not flip at midnight UTC (7 or 8 pm in Toronto) and a week with a clock change is
still seven calendar days: weeks run Monday to Sunday, months by calendar. The surgery date is a
date, not an instant, so the bounds are inclusive dates and no clock arithmetic is involved
beyond finding today's date there."""

import calendar
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

CLINIC_TIMEZONE = ZoneInfo("America/Toronto")
MAX_PAGE_SIZE = 50


class SurgeryRange(StrEnum):
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"


def clinic_today(now: datetime) -> date:
    return now.astimezone(CLINIC_TIMEZONE).date()


def _week(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def resolve_range(name: SurgeryRange, now: datetime) -> tuple[date, date]:
    """Inclusive (from, to) dates of a named range as of `now`, in the clinic's time zone."""
    today = clinic_today(now)
    if name is SurgeryRange.THIS_WEEK:
        return _week(today)
    if name is SurgeryRange.LAST_WEEK:
        return _week(today - timedelta(days=7))
    if name is SurgeryRange.THIS_MONTH:
        return month_bounds(today.year, today.month)
    last = today.replace(day=1) - timedelta(days=1)  # the last day of the month before
    return month_bounds(last.year, last.month)
