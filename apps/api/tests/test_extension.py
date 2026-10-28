"""Follow-up extensions in the pure engine, with a pinned clock: eligibility, the request window,
the extension's windows (including across Toronto clock changes), its end, asking again, and a
discharge during an extension. Local times are Toronto; the 2026 clocks go back on Sunday
1 November and the 2027 clocks go forward on Sunday 14 March. The numbers (30 days, six windows)
are placeholders pending the surgeon; they come from the schedule rows used here."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from clinically_anchored_api.core.extension import (
    Standing,
    decision_entry_body,
    extension_status,
    request_deadline,
)
from clinically_anchored_api.core.schedule import (
    Phase,
    Schedule,
    WindowStatus,
    episode_end,
    episode_state,
    windows,
)

TORONTO = ZoneInfo("America/Toronto")
V2 = Schedule(2, (2, 5, 10, 17, 24, 31, 38, 45, 52), 7, time(7, 0), "America/Toronto")
EXT = Schedule(3, (7, 14, 21, 28, 35), 7, time(7, 0), "America/Toronto", "extension", 30)
SURGERY = date(2026, 8, 1)


def local(y, mo, d, h=7, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=TORONTO).astimezone(UTC)


def local_after(y, mo, d, days, h=7) -> datetime:
    """07:00 Toronto time `days` calendar days after the given date."""
    later = date(y, mo, d) + timedelta(days=days)
    return local(later.year, later.month, later.day, h)


MAIN_END = episode_end(SURGERY, V2)  # day 59: 2026-09-29 07:00 Toronto


def main_state(now, *, closed_at=None, reason=None):
    return episode_state(
        surgery_date=SURGERY, schedule=V2, now=now, checkin_completed_at=local(2026, 8, 3),
        closed_at=closed_at, close_reason=reason, used_intervals=set(), reply_available=False,
    )


def ext_state(started_at, now, *, used=(), closed_at=None, reason=None, reply=False):
    return episode_state(
        surgery_date=SURGERY, schedule=EXT, now=now, checkin_completed_at=started_at,
        closed_at=closed_at, close_reason=reason, used_intervals=set(used),
        reply_available=reply, started_at=started_at,
    )


# --- who may ask --------------------------------------------------------------------------


def test_the_main_period_ends_on_day_59_at_0700():
    assert MAIN_END == local(2026, 9, 29)


def test_nobody_can_ask_while_the_term_is_still_running():
    now = MAIN_END - timedelta(minutes=1)
    status = extension_status(state=main_state(now), request=None, extension=EXT, now=now)
    assert status.standing is Standing.NOT_APPLICABLE and not status.can_request
    assert not status.ended_mode_open


def test_a_discharged_episode_cannot_ask():
    now = MAIN_END + timedelta(days=3)
    state = main_state(now, closed_at=local(2026, 9, 1), reason="discharged")
    status = extension_status(state=state, request=None, extension=EXT, now=now)
    assert status.standing is Standing.NOT_APPLICABLE and not status.ended_mode_open


def test_an_episode_that_ended_by_schedule_may_ask_from_the_first_minute():
    status = extension_status(
        state=main_state(MAIN_END), request=None, extension=EXT, now=MAIN_END
    )
    assert status.standing is Standing.ELIGIBLE and status.can_request and status.ended_mode_open
    assert status.request_by == local(2026, 10, 29)  # 30 days later, 07:00


def test_the_request_window_is_30_days_and_closes_exactly_then():
    by = local(2026, 10, 29)
    before = extension_status(
        state=main_state(by - timedelta(seconds=1)), request=None, extension=EXT,
        now=by - timedelta(seconds=1),
    )
    after = extension_status(state=main_state(by), request=None, extension=EXT, now=by)
    assert before.standing is Standing.ELIGIBLE
    assert after.standing is Standing.LAPSED and not after.can_request and not after.ended_mode_open


def test_the_request_window_follows_the_data_not_the_code():
    shorter = Schedule(4, (7,), 7, time(7, 0), "America/Toronto", "extension", 10)
    now = MAIN_END + timedelta(days=11)
    assert extension_status(
        state=main_state(now), request=None, extension=shorter, now=now
    ).standing is Standing.LAPSED
    assert extension_status(
        state=main_state(now), request=None, extension=EXT, now=now
    ).standing is Standing.ELIGIBLE


def test_the_deadline_keeps_the_same_local_time_across_a_clock_change():
    # Ends 2026-09-29 07:00 EDT; 30 days later is 2026-10-29, still EDT: 07:00 local again.
    assert request_deadline(MAIN_END, EXT) == local(2026, 10, 29)
    # An episode ending just before the clocks go back: the deadline is 07:00 EST, 25 h later
    # than "24 h x 30" would be.
    closes = local(2026, 10, 10)
    assert request_deadline(closes, EXT) == local(2026, 11, 9)
    assert request_deadline(closes, EXT) - closes == timedelta(days=30, hours=1)


def test_no_extension_schedule_means_the_feature_is_off():
    status = extension_status(
        state=main_state(MAIN_END), request=None, extension=None, now=MAIN_END
    )
    assert status.standing is Standing.NOT_APPLICABLE


def test_a_pending_request_keeps_the_link_in_ended_mode_however_long_it_takes():
    now = MAIN_END + timedelta(days=200)
    status = extension_status(
        state=main_state(now), request={"status": "pending"}, extension=EXT, now=now
    )
    assert status.standing is Standing.PENDING and status.ended_mode_open
    assert not status.can_request  # one request at a time, and one per care period


def test_a_decline_is_final_and_its_outcome_stays_until_the_later_of_the_two_30_days():
    declined_on_day_10 = MAIN_END + timedelta(days=10)
    request = {"status": "declined", "decided_at": declined_on_day_10.isoformat()}

    def at(days):
        now = MAIN_END + timedelta(days=days)
        return extension_status(state=main_state(now), request=request, extension=EXT, now=now)

    assert at(15).standing is Standing.DECLINED and not at(15).can_request
    assert at(39).standing is Standing.DECLINED  # inside day 40 (10 + 30)
    assert at(41).standing is Standing.LAPSED and not at(41).ended_mode_open
    # A decision made after the request window still gets its 30 days of being readable.
    late = {"status": "declined", "decided_at": (MAIN_END + timedelta(days=45)).isoformat()}
    now = MAIN_END + timedelta(days=60)
    assert extension_status(
        state=main_state(now), request=late, extension=EXT, now=now
    ).standing is Standing.DECLINED


def test_a_decision_entry_says_what_was_decided_and_carries_the_clinicians_words():
    assert decision_entry_body("declined", None) == "Extension request declined."
    assert decision_entry_body("accepted", "Happy to.") == "Extension request accepted. Happy to."


# --- the extension's windows ---------------------------------------------------------------


def test_window_1_opens_at_acceptance_and_windows_2_to_6_at_0700_on_days_7_to_35():
    started = local(2026, 10, 5, 14, 20)
    w = windows(SURGERY, EXT, started)
    assert [x.number for x in w] == [1, 2, 3, 4, 5, 6]
    assert w[0].opens_at == started
    assert [x.opens_at for x in w[1:]] == [local_after(2026, 10, 5, d) for d in (7, 14, 21, 28, 35)]
    assert [x.closes_at for x in w[:-1]] == [x.opens_at for x in w[1:]]  # no gaps
    assert w[-1].closes_at == local_after(2026, 10, 5, 42)  # the extension ends on day 42


def test_acceptance_before_0700_still_starts_at_the_moment_of_acceptance():
    started = local(2026, 10, 5, 5, 30)
    w = windows(SURGERY, EXT, started)
    assert w[0].opens_at == started and w[1].opens_at == local(2026, 10, 12)


def test_days_count_from_the_acceptance_date_not_from_24_hour_blocks():
    # Accepted at 23:30; window 2 still opens at 07:00 on the 7th day after that DATE.
    started = local(2026, 10, 5, 23, 30)
    assert windows(SURGERY, EXT, started)[1].opens_at == local(2026, 10, 12)


def test_an_extension_across_the_fall_back_keeps_0700_and_one_window_is_an_hour_longer():
    started = local(2026, 10, 28, 10, 0)  # clocks go back on 1 November
    w = windows(SURGERY, EXT, started)
    assert w[1].opens_at == local(2026, 11, 4)  # 07:00 EST, 12:00 UTC
    assert w[1].opens_at.hour == 12
    # Window 1: 10:00 EDT to 07:00 EST a week later is 6 days 21 h on the wall, plus the extra hour.
    assert w[0].closes_at - w[0].opens_at == timedelta(days=6, hours=22)
    assert w[-1].closes_at == local(2026, 12, 9)  # day 42 after 28 October, 07:00 EST


def test_an_extension_across_the_spring_forward_keeps_0700_and_one_window_is_an_hour_shorter():
    started = local(2027, 3, 10, 9, 0)  # clocks go forward on 14 March
    w = windows(SURGERY, EXT, started)
    assert w[1].opens_at == local(2027, 3, 17)  # 07:00 EDT, 11:00 UTC
    assert w[1].opens_at.hour == 11
    assert w[0].closes_at - w[0].opens_at == timedelta(days=6, hours=21)
    # The week that contains the change is 23 hours shorter... windows after it are 7 x 24 h.
    assert w[2].closes_at - w[2].opens_at == timedelta(days=7)


def test_the_window_that_spans_the_change_is_the_odd_one():
    started = local(2026, 10, 28, 7, 0)
    lengths = [x.closes_at - x.opens_at for x in windows(SURGERY, EXT, started)]
    assert lengths == [
        timedelta(days=7, hours=1), *[timedelta(days=7)] * 5
    ]


def test_an_extension_schedule_needs_its_start():
    with pytest.raises(ValueError):
        windows(SURGERY, EXT)
    with pytest.raises(ValueError):
        episode_end(SURGERY, EXT)


def test_schedule_validation_keeps_the_kinds_apart():
    with pytest.raises(ValueError):
        Schedule(5, (7,), 7, time(7, 0), "America/Toronto", "extension")  # needs the window
    with pytest.raises(ValueError):
        Schedule(5, (7,), 7, time(7, 0), "America/Toronto", "main", 30)  # main has none
    with pytest.raises(ValueError):
        Schedule(5, (7,), 7, time(7, 0), "America/Toronto", "other", None)


# --- an extension running ----------------------------------------------------------------------


def test_the_first_window_is_open_the_moment_it_is_accepted():
    started = local(2026, 10, 5, 14, 20)
    state = ext_state(started, started)
    assert state.phase is Phase.OPEN and state.interval_no == 1 and state.kind == "extension"
    assert state.intervals_total == 6 and state.can_send_scheduled
    assert state.windows[0].status is WindowStatus.OPEN


def test_one_message_per_window_and_unused_windows_do_not_carry_over():
    started = local(2026, 10, 5, 14, 20)
    used = ext_state(started, started + timedelta(days=1), used={1})
    assert used.phase is Phase.USED and not used.can_send_scheduled
    day8 = ext_state(started, local(2026, 10, 13), used=())  # window 1 went unused
    assert day8.phase is Phase.OPEN and day8.interval_no == 2 and day8.missed == (1,)
    assert day8.windows[0].status is WindowStatus.MISSED


def test_a_clinician_message_grants_one_reply_exactly_as_in_the_main_period():
    started = local(2026, 10, 5, 14, 20)
    state = ext_state(started, started + timedelta(days=2), used={1}, reply=True)
    assert state.phase is Phase.USED and state.can_reply and not state.can_send_scheduled


def test_the_status_badge_numbers_come_from_the_state():
    started = local(2026, 10, 5, 14, 20)
    state = ext_state(started, local(2026, 10, 13))
    assert (state.interval_no, state.intervals_total) == (2, 6)  # "Extension window 2 of 6"


def test_the_extension_ends_by_schedule_on_day_42_and_the_episode_closes():
    started = local(2026, 10, 5, 14, 20)
    end = local(2026, 11, 16)  # 42 days after 5 October, 07:00 EST
    just_before = ext_state(started, end - timedelta(seconds=1))
    assert not just_before.closed and just_before.interval_no == 6
    after = ext_state(started, end)
    assert after.closed and after.close_reason == "schedule_complete" and after.closed_at == end
    assert not after.can_send_scheduled and not after.can_reply


def test_a_discharge_during_an_extension_ends_it_and_cannot_be_asked_about():
    started = local(2026, 10, 5, 14, 20)
    discharged = local(2026, 10, 20, 9, 0)
    now = local(2026, 10, 25)
    state = ext_state(started, now, closed_at=discharged, reason="discharged")
    assert state.closed and state.close_reason == "discharged"
    assert all(
        w.status in (WindowStatus.CUT_SHORT, WindowStatus.MISSED) for w in state.windows
    )
    status = extension_status(state=state, request=None, extension=EXT, now=now)
    assert status.standing is Standing.NOT_APPLICABLE and not status.ended_mode_open


def test_after_an_extension_ends_the_patient_may_ask_again_with_a_new_30_days():
    started = local(2026, 10, 5, 14, 20)
    end = local(2026, 11, 16)
    now = end + timedelta(days=1)
    state = ext_state(started, now)
    assert state.closed and state.close_reason == "schedule_complete"
    status = extension_status(state=state, request=None, extension=EXT, now=now)
    assert status.standing is Standing.ELIGIBLE
    assert status.request_by == local(2026, 12, 16)  # 30 days after the extension ended
    # The first request does not count against the second: that was about the other episode.
    lapsed = extension_status(
        state=ext_state(started, end + timedelta(days=31)), request=None, extension=EXT,
        now=end + timedelta(days=31),
    )
    assert lapsed.standing is Standing.LAPSED
