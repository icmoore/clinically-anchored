"""The scheduled-messaging engine, with a pinned clock. Local times are Toronto; the
2026 clocks go back on Sunday 1 November and the 2027 clocks go forward on Sunday 14 March."""

import random
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from clinically_anchored_api.core.schedule import (
    Phase,
    Schedule,
    episode_end,
    episode_state,
    windows,
)

TORONTO = ZoneInfo("America/Toronto")
V1 = Schedule(1, (2, 3, 5, 7, 14, 21, 28, 35, 42), 7, time(7, 0), "America/Toronto")
V2 = Schedule(2, (2, 5, 10, 17, 24, 31, 38, 45, 52), 7, time(7, 0), "America/Toronto")
SURGERY = date(2026, 10, 1)  # a Thursday


def local(y, mo, d, h=7, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=TORONTO).astimezone(UTC)


def state(now, *, surgery=SURGERY, schedule=V1, checkin=True, closed_at=None, reason=None,
          used=(), reply=False):
    return episode_state(
        surgery_date=surgery,
        schedule=schedule,
        now=now,
        checkin_completed_at=local(2026, 10, 2) if checkin else None,
        closed_at=closed_at,
        close_reason=reason,
        used_intervals=set(used),
        reply_available=reply,
    )


# --- the windows ----------------------------------------------------------------------


def test_v1_windows_open_at_0700_local_and_chain_without_gaps():
    w = windows(SURGERY, V1)
    assert [x.number for x in w] == list(range(1, 10))
    assert w[0].opens_at == local(2026, 10, 3)  # surgery + 2 days
    assert w[8].opens_at == local(2026, 11, 12)  # surgery + 42 days
    assert w[8].closes_at == local(2026, 11, 19)  # + 7 more days
    assert all(a.closes_at == b.opens_at for a, b in zip(w, w[1:], strict=False))
    assert episode_end(SURGERY, V1) == local(2026, 11, 19)


def test_weekends_are_ordinary_days():
    w = windows(SURGERY, V1)
    assert w[0].opens_at.astimezone(TORONTO).strftime("%A") == "Saturday"  # Oct 3
    assert w[1].opens_at.astimezone(TORONTO).strftime("%A") == "Sunday"  # Oct 4


def test_the_alternate_schedule_changes_only_data():
    w = windows(SURGERY, V2)
    assert w[1].opens_at == local(2026, 10, 6)  # +5
    assert w[8].opens_at == local(2026, 11, 22)  # +52
    assert episode_end(SURGERY, V2) == local(2026, 11, 29)  # +59


# --- daylight saving --------------------------------------------------------------------


def test_clocks_going_back_makes_one_interval_25_hours_and_the_opening_stays_0700():
    surgery = date(2026, 10, 29)  # interval 1 opens Oct 31, interval 2 opens Sunday Nov 1
    w = windows(surgery, V1)
    assert w[0].opens_at == datetime(2026, 10, 31, 11, 0, tzinfo=UTC)  # 07:00 EDT
    assert w[1].opens_at == datetime(2026, 11, 1, 12, 0, tzinfo=UTC)  # 07:00 EST
    assert w[0].closes_at - w[0].opens_at == timedelta(hours=25)
    assert w[1].opens_at.astimezone(TORONTO).hour == 7


def test_clocks_going_forward_makes_one_interval_23_hours():
    surgery = date(2027, 3, 11)  # interval 1 opens Mar 13, interval 2 opens Sunday Mar 14
    w = windows(surgery, V1)
    assert w[0].opens_at == datetime(2027, 3, 13, 12, 0, tzinfo=UTC)  # 07:00 EST
    assert w[1].opens_at == datetime(2027, 3, 14, 11, 0, tzinfo=UTC)  # 07:00 EDT
    assert w[0].closes_at - w[0].opens_at == timedelta(hours=23)


def test_a_long_interval_across_a_change_is_a_week_plus_or_minus_an_hour():
    w = windows(SURGERY, V1)  # 28 -> 35 crosses the Nov 1 change
    assert w[6].closes_at - w[6].opens_at == timedelta(days=7, hours=1)


def test_the_slot_flips_at_the_instant_of_opening_on_both_sides_of_a_change():
    surgery = date(2026, 10, 29)
    just_before = datetime(2026, 11, 1, 11, 59, 59, tzinfo=UTC)  # 06:59:59 EST
    at = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)  # 07:00:00 EST
    assert state(just_before, surgery=surgery).interval_no == 1
    assert state(at, surgery=surgery).interval_no == 2
    # 11:00Z would be 07:00 under the OLD offset; after the change it is 06:00 and not open yet.
    assert state(datetime(2026, 11, 1, 11, 0, tzinfo=UTC), surgery=surgery).interval_no == 1


def test_post_op_day_counts_calendar_days_in_toronto():
    assert state(local(2026, 10, 1, 23, 59)).post_op_day == 0
    assert state(local(2026, 10, 2, 0, 1)).post_op_day == 1
    assert state(local(2026, 9, 30, 12)).post_op_day == -1  # before surgery


# --- phases -----------------------------------------------------------------------------


def test_locked_until_the_check_in_whatever_the_clock_says():
    for now in (local(2026, 10, 2), local(2026, 10, 3), local(2026, 10, 20)):
        s = state(now, checkin=False)
        assert s.phase is Phase.LOCKED_CHECKIN
        assert not s.can_send_scheduled


def test_waiting_before_interval_1_shows_when_it_opens():
    s = state(local(2026, 10, 2, 12))
    assert s.phase is Phase.WAITING
    assert s.interval_no is None
    assert s.next_opens_at == local(2026, 10, 3)
    assert not s.can_send_scheduled


def test_open_slot_unused():
    s = state(local(2026, 10, 3, 7))  # the very instant interval 1 opens
    assert s.phase is Phase.OPEN and s.interval_no == 1 and s.can_send_scheduled
    assert s.interval_opens_at == local(2026, 10, 3)
    assert s.interval_closes_at == s.next_opens_at == local(2026, 10, 4)


def test_used_slot_reports_when_the_next_opens():
    s = state(local(2026, 10, 3, 15), used={1})
    assert s.phase is Phase.USED and not s.can_send_scheduled
    assert s.next_opens_at == local(2026, 10, 4)


def test_the_last_interval_has_no_next_but_the_episode_end():
    s = state(local(2026, 11, 14), used={9})  # interval 9 opened Nov 12
    assert s.phase is Phase.USED and s.interval_no == 9
    assert s.next_opens_at is None
    assert s.episode_closes_at == local(2026, 11, 19)


def test_unused_interval_does_not_carry_over():
    # Interval 1 unused, interval 2 open: one slot, not two.
    s = state(local(2026, 10, 4, 9))
    assert s.interval_no == 2 and s.missed == (1,)
    assert s.phase is Phase.OPEN


def test_a_late_check_in_does_not_give_back_passed_intervals():
    s = state(local(2026, 10, 15, 9), used=set())
    assert s.interval_no == 5 and s.phase is Phase.OPEN
    assert s.missed == (1, 2, 3, 4)


def test_used_intervals_are_not_missed():
    s = state(local(2026, 10, 15, 9), used={1, 3})
    assert s.missed == (2, 4)


def test_closes_by_itself_exactly_when_the_last_interval_ends():
    end = local(2026, 11, 19)
    before = state(end - timedelta(seconds=1), used={9})
    after = state(end, used={9})
    assert before.phase is Phase.USED
    assert after.phase is Phase.CLOSED and after.close_reason == "schedule_complete"
    assert after.closed_at == end
    assert after.interval_no is None and not after.can_send_scheduled


def test_a_discharge_closes_immediately_and_stops_counting_missed_intervals():
    discharged = local(2026, 10, 15, 9)
    s = state(local(2026, 10, 30), closed_at=discharged, reason="discharged")
    assert s.phase is Phase.CLOSED and s.close_reason == "discharged"
    assert s.closed_at == discharged
    assert s.missed == (1, 2, 3, 4)  # what had passed when they were discharged


def test_closed_wins_over_locked_and_blocks_replies():
    s = state(local(2026, 10, 15), checkin=False, closed_at=local(2026, 10, 14),
              reason="discharged", reply=True)
    assert s.phase is Phase.CLOSED
    assert s.reply_available and not s.can_reply


def test_a_reply_is_available_in_any_open_phase_independent_of_the_slot():
    for kwargs in ({"checkin": False}, {"used": {1}}, {}):
        s = state(local(2026, 10, 3, 9), reply=True, **kwargs)
        assert s.can_reply
    assert not state(local(2026, 10, 3, 9), reply=False).can_reply


# --- input checks -----------------------------------------------------------------------


def test_now_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        state(datetime(2026, 10, 3, 7))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"offsets_days": ()},
        {"offsets_days": (0, 2)},
        {"offsets_days": (3, 3)},
        {"offsets_days": (5, 2)},
        {"close_after_days": 0},
        {"opens_at_local": time(1, 30)},  # inside a daylight-saving change
        {"timezone": "Mars/Olympus"},
    ],
)
def test_bad_schedules_are_rejected(kwargs):
    base = dict(version=1, offsets_days=(2, 3), close_after_days=7, opens_at_local=time(7, 0),
                timezone="America/Toronto")
    with pytest.raises(ValueError):
        Schedule(**{**base, **kwargs})


def test_schedule_from_a_database_row():
    row = {"version": 1, "offsets_days": [2, 3, 5], "close_after_days": 7,
           "opens_at_local": "07:00:00", "timezone": "America/Toronto"}
    assert Schedule.from_row(row) == Schedule(1, (2, 3, 5), 7, time(7, 0), "America/Toronto")
    assert Schedule.from_row(row).snapshot()["opens_at_local"] == "07:00:00"


# --- properties over random instants -------------------------------------------------------


@pytest.mark.parametrize("schedule", [V1, V2])
@pytest.mark.parametrize("surgery", [date(2026, 10, 1), date(2026, 10, 29), date(2027, 3, 11),
                                     date(2027, 6, 1)])
def test_exactly_one_interval_is_open_at_every_instant_inside_the_episode(schedule, surgery):
    rng = random.Random(f"{schedule.version}{surgery}")
    w = windows(surgery, schedule)
    start, end = w[0].opens_at, w[-1].closes_at
    for _ in range(300):
        now = start + timedelta(seconds=rng.randrange(int((end - start).total_seconds())))
        s = state(now, surgery=surgery, schedule=schedule)
        assert s.interval_no is not None
        assert sum(x.opens_at <= now < x.closes_at for x in w) == 1
        assert s.phase is Phase.OPEN
    for before in (start - timedelta(seconds=1), start - timedelta(days=1)):
        s = state(before, surgery=surgery, schedule=schedule)
        assert s.phase is Phase.WAITING and s.next_opens_at == start
    assert state(end, surgery=surgery, schedule=schedule).phase is Phase.CLOSED
