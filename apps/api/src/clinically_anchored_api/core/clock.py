"""The api's one source of "now", so tests can pin it. Anything that decides what is open,
closed or expired takes its time from here (or as a parameter), never from datetime.now()
inline: core/schedule.py itself takes `now` as an argument and never reads a clock."""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)
