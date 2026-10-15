"""The scheduled-messaging engine: a pure function from an episode's facts and the clock
to what the patient may do right now.

Nothing here reads a clock, a database or a setting. `episode_state` takes `now` as an
argument, so every rule (and every daylight-saving edge) is tested with a pinned instant.

The rules (docs/api/README.md has the surgeon's wording):
  * A schedule is a list of day offsets from the surgery DATE. Interval N opens at
    `opens_at_local` on surgery_date + offsets[N-1] in the schedule's timezone, and stays
    open until interval N+1 opens. The last one stays open `close_after_days` days after it
    opened; then the episode is over.
  * Opening times are wall-clock times on calendar dates, not multiples of 24 hours, so an
    interval that spans a clock change is 23 or 25 hours long and still opens at 07:00.
    (A schedule may not open before 04:00, so the opening time always exists and is never
    ambiguous: Toronto's clocks change at 02:00.)
  * One scheduled message per interval; an unused interval is gone when the next opens.
  * Before the post-op check-in nothing can be sent. A late check-in does not give back
    intervals that already passed: they are reported as `missed`.
  * A closed episode (discharged, or the last interval ended) allows nothing.
  * Each interval also gets a status for the clinic's timeline (`windows` on the state) and,
    when it was missed, why: it closed before the check-in was done (nothing could have been
    sent), or the patient was able to write and did not.
Whether a doctor's message has a reply waiting is the caller's fact (`reply_available`);
this module only combines it with the phase.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EARLIEST_OPENING = time(4, 0)


class Phase(StrEnum):
    CLOSED = "closed"  # discharged, or the schedule has run out
    LOCKED_CHECKIN = "locked_checkin"  # the post-op check-in is not done yet
    WAITING = "waiting"  # check-in done, interval 1 has not opened yet
    OPEN = "open"  # an interval is open and its message is unused
    USED = "used"  # the open interval's message has been sent


class WindowStatus(StrEnum):
    USED = "used"  # the patient's message for it was sent
    MISSED = "missed"  # it ended without a message (see MissedReason)
    OPEN = "open"  # open now and the patient can write
    LOCKED = "locked"  # open now, but the post-op check-in is not done, so nothing can be sent
    UPCOMING = "upcoming"  # has not opened yet
    CUT_SHORT = "cut_short"  # the episode closed (discharge) before it ended or opened


class MissedReason(StrEnum):
    BEFORE_CHECK_IN = "closed_before_check_in"  # it ended before the check-in was done
    NO_MESSAGE = "no_message"  # the check-in was done and the patient did not write in it


@dataclass(frozen=True)
class Schedule:
    version: int
    offsets_days: tuple[int, ...]
    close_after_days: int
    opens_at_local: time
    timezone: str

    def __post_init__(self):
        o = self.offsets_days
        if not o or o[0] < 1 or any(b <= a for a, b in zip(o, o[1:], strict=False)):
            raise ValueError("offsets_days must be positive and strictly increasing")
        if self.close_after_days < 1:
            raise ValueError("close_after_days must be at least 1")
        if self.opens_at_local < EARLIEST_OPENING:
            raise ValueError("opens_at_local must be 04:00 or later")
        self.tz  # raises ValueError for an unknown zone

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc

    @classmethod
    def from_row(cls, row: dict) -> "Schedule":
        opens = row["opens_at_local"]
        return cls(
            version=row["version"],
            offsets_days=tuple(row["offsets_days"]),
            close_after_days=row["close_after_days"],
            opens_at_local=opens if isinstance(opens, time) else time.fromisoformat(opens),
            timezone=row["timezone"],
        )

    def snapshot(self) -> dict:
        """What an episode was created under, for the audit trail."""
        return {
            "version": self.version,
            "offsets_days": list(self.offsets_days),
            "close_after_days": self.close_after_days,
            "opens_at_local": self.opens_at_local.isoformat(),
            "timezone": self.timezone,
        }


@dataclass(frozen=True)
class Window:
    number: int  # 1-based interval number, as stamped on a message
    opens_at: datetime  # UTC
    closes_at: datetime  # UTC; the next interval's opening, or the end of the episode


def _local_instant(schedule: Schedule, day: date) -> datetime:
    return datetime.combine(day, schedule.opens_at_local, tzinfo=schedule.tz).astimezone(UTC)


def windows(surgery_date: date, schedule: Schedule) -> tuple[Window, ...]:
    opens = [
        _local_instant(schedule, surgery_date + timedelta(days=o)) for o in schedule.offsets_days
    ]
    end = episode_end(surgery_date, schedule)
    closes = [*opens[1:], end]
    return tuple(Window(n, o, c) for n, (o, c) in enumerate(zip(opens, closes, strict=True), 1))


def episode_end(surgery_date: date, schedule: Schedule) -> datetime:
    """The instant the last interval ends and the episode closes by itself."""
    last = schedule.offsets_days[-1] + schedule.close_after_days
    return _local_instant(schedule, surgery_date + timedelta(days=last))


@dataclass(frozen=True)
class WindowState:
    number: int
    opens_at: datetime  # UTC
    closes_at: datetime  # UTC
    status: WindowStatus
    missed_reason: MissedReason | None  # set only when status is MISSED


@dataclass(frozen=True)
class EpisodeState:
    phase: Phase
    # Whole days since the surgery date in the schedule's timezone (negative before it).
    post_op_day: int
    intervals_total: int
    interval_no: int | None  # the interval open right now, whatever the phase
    interval_opens_at: datetime | None
    interval_closes_at: datetime | None
    next_opens_at: datetime | None  # the next interval's opening, if there is one
    episode_closes_at: datetime  # the natural end (a discharge may come sooner)
    closed_at: datetime | None  # when it closed, if it has (effective time)
    close_reason: str | None  # 'discharged' | 'schedule_complete'
    missed: tuple[int, ...]  # intervals that ended without a message
    reply_available: bool
    # Every interval with its status, in order (the nine markers on the clinic's timeline).
    windows: tuple[WindowState, ...] = ()

    @property
    def closed(self) -> bool:
        return self.phase is Phase.CLOSED

    @property
    def can_send_scheduled(self) -> bool:
        return self.phase is Phase.OPEN

    @property
    def can_reply(self) -> bool:
        return self.reply_available and not self.closed


def episode_state(
    *,
    surgery_date: date,
    schedule: Schedule,
    now: datetime,
    checkin_completed_at: datetime | None,
    closed_at: datetime | None,
    close_reason: str | None,
    used_intervals: Collection[int],
    reply_available: bool,
) -> EpisodeState:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)

    wins = windows(surgery_date, schedule)
    end = wins[-1].closes_at

    if closed_at is not None:
        effective, reason = closed_at.astimezone(UTC), close_reason
    elif now >= end:
        effective, reason = end, "schedule_complete"
    else:
        effective, reason = None, None

    reference = effective or now
    missed = tuple(
        w.number for w in wins if w.closes_at <= reference and w.number not in used_intervals
    )

    current = upcoming = None
    if effective is None:
        current = next((w for w in wins if w.opens_at <= now < w.closes_at), None)
        upcoming = next((w for w in wins if w.opens_at > now), None)

    if effective is not None:
        phase = Phase.CLOSED
    elif checkin_completed_at is None:
        phase = Phase.LOCKED_CHECKIN
    elif current is None:
        phase = Phase.WAITING
    elif current.number in used_intervals:
        phase = Phase.USED
    else:
        phase = Phase.OPEN

    checkin_at = checkin_completed_at.astimezone(UTC) if checkin_completed_at else None

    def window_state(w: Window) -> WindowState:
        why = None
        if w.number in used_intervals:
            status = WindowStatus.USED
        elif w.closes_at <= reference:
            status = WindowStatus.MISSED
            # It ended before the check-in was done (or the check-in is still not done): the
            # patient could not have written. Otherwise they could, and did not.
            before = checkin_at is None or checkin_at >= w.closes_at
            why = MissedReason.BEFORE_CHECK_IN if before else MissedReason.NO_MESSAGE
        elif effective is not None:
            status = WindowStatus.CUT_SHORT
        elif current is not None and w.number == current.number:
            status = WindowStatus.OPEN if phase is Phase.OPEN else WindowStatus.LOCKED
        else:
            status = WindowStatus.UPCOMING
        return WindowState(w.number, w.opens_at, w.closes_at, status, why)

    return EpisodeState(
        phase=phase,
        post_op_day=(now.astimezone(schedule.tz).date() - surgery_date).days,
        intervals_total=len(wins),
        interval_no=current.number if current else None,
        interval_opens_at=current.opens_at if current else None,
        interval_closes_at=current.closes_at if current else None,
        next_opens_at=upcoming.opens_at if upcoming else None,
        episode_closes_at=end,
        closed_at=effective,
        close_reason=reason,
        missed=missed,
        reply_available=reply_available,
        windows=tuple(window_state(w) for w in wins),
    )
