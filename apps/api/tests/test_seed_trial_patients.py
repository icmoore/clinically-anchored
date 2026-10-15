"""The trial-patient seed plan is pure data checked against the real schedule engine; check it
stays writable whenever it is run (it is built relative to the run time)."""

from datetime import datetime, timedelta

import pytest

from clinically_anchored_api.core.rules import evaluate_red_flags
from clinically_anchored_api.core.schedule import Phase
from clinically_anchored_api.seed_trial_patients import (
    DEFAULT_SCHEDULES,
    TORONTO,
    build_plan,
    interval_at,
    simulate,
    validate_plan,
)

NOON = datetime(2026, 10, 14, 13, 0, tzinfo=TORONTO)


@pytest.mark.parametrize(
    "local",
    [
        datetime(2026, 10, 14, 0, 5),  # just after midnight
        datetime(2026, 10, 14, 6, 59),  # a minute before windows open
        datetime(2026, 10, 14, 7, 1),  # a minute after
        datetime(2026, 10, 14, 13, 0),
        datetime(2026, 10, 14, 23, 59),
        datetime(2026, 11, 1, 9, 0),  # clocks go back overnight
        datetime(2027, 3, 14, 12, 0),  # clocks go forward overnight
    ],
)
@pytest.mark.parametrize("v1_patient", [False, True])
def test_plan_is_valid_at_any_time_of_day(local, v1_patient):
    now = local.replace(tzinfo=TORONTO)
    assert validate_plan(build_plan(now, v1_patient), now) == []


def test_plan_is_valid_on_every_day_for_a_season():
    start = datetime(2026, 9, 1, 15, 0)
    for day in range(0, 200, 3):
        now = (start + timedelta(days=day)).replace(tzinfo=TORONTO)
        assert validate_plan(build_plan(now, True), now) == [], now


def test_plan_shape():
    plans = build_plan(NOON)
    assert [p.label for p in plans] == [f"Trial Patient {i}" for i in range(1, 14)]
    assert len({p.contact for p in plans}) == 13
    assert len({p.full_name for p in plans}) == 13
    assert all(p.contact.endswith("@example.com") for p in plans)
    # Everyone is on the confirmed schedule (v2); only the optional patient 14 keeps v1.
    assert {p.schedule_version for p in plans} == {2}
    extra = build_plan(NOON, True)
    assert [p.label for p in extra][-1] == "Trial Patient 14"
    assert extra[-1].schedule_version == 1


def test_every_engine_state_is_on_screen():
    plans = build_plan(NOON, True)
    states = {p.label: simulate(p, DEFAULT_SCHEDULES[p.schedule_version], NOON)[0] for p in plans}
    assert {s.phase for s in states.values()} == set(Phase)
    closed = {s.close_reason for s in states.values() if s.closed}
    assert closed == {"discharged", "schedule_complete"}
    assert any(s.missed for s in states.values())  # a late check-in
    assert any(s.can_reply for s in states.values())  # a reply open
    locked = states["Trial Patient 11"]
    assert locked.phase is Phase.LOCKED_CHECKIN and locked.can_reply  # the clinic wrote first
    assert states["Trial Patient 12"].phase is Phase.WAITING
    assert states["Trial Patient 8"].missed == (1, 2, 6)  # checked in on day 14; window 6 unused


def test_a_late_check_in_is_really_late():
    plan = next(p for p in build_plan(NOON) if p.label == "Trial Patient 8")
    schedule = DEFAULT_SCHEDULES[2]
    assert interval_at(plan, schedule, plan.check_in.at) == 3  # window 3 (days 10-17) was open
    assert (plan.check_in.at.astimezone(TORONTO).date() - plan.surgery_date).days == 14


def test_each_patient_is_in_the_state_the_plan_says_on_the_day_it_runs():
    for plan in build_plan(NOON):
        state, problems = simulate(plan, DEFAULT_SCHEDULES[plan.schedule_version], NOON)
        assert problems == []
        assert state.phase is plan.expect.phase, plan.label


def test_red_flags_come_from_the_real_rule_engine_on_the_intended_check_ins():
    flagged = [
        p.label
        for p in build_plan(NOON)
        if p.check_in and evaluate_red_flags(p.check_in.answers)[0]
    ]
    assert flagged == [
        "Trial Patient 2", "Trial Patient 3", "Trial Patient 7", "Trial Patient 8",
    ]


def test_two_patients_are_left_awaiting_a_reply_on_their_latest_message():
    awaiting = {"Trial Patient 3", "Trial Patient 9"}
    for plan in build_plan(NOON):
        unread = [m for m in plan.messages if m.sender == "patient" and m.read_at is None]
        assert bool(unread) == (plan.label in awaiting), plan.label
        if plan.label in awaiting:
            assert plan.messages[-1].sender == "patient" and not plan.messages[-1].reply


def test_reads_happen_before_the_reply():
    for plan in build_plan(NOON):
        for i, m in enumerate(plan.messages):
            if m.read_at is None or plan.closed is not None:
                continue
            reply = next((x for x in plan.messages[i + 1 :] if x.sender == "clinician"), None)
            assert m.at < m.read_at and (reply is None or m.read_at <= reply.at)


def test_the_plan_refuses_to_run_against_a_changed_schedule_that_breaks_it():
    from clinically_anchored_api.core.schedule import Schedule

    plans = build_plan(NOON)
    opens = DEFAULT_SCHEDULES[2].opens_at_local
    shifted = {
        1: DEFAULT_SCHEDULES[1],
        2: Schedule(2, (3, 6, 11, 18, 25, 32, 39, 46, 53), 7, opens, "America/Toronto"),
    }
    problems = validate_plan(plans, NOON, shifted)
    assert problems and all(": " in p for p in problems)


def test_a_broken_plan_is_caught_by_the_engine():
    plans = build_plan(NOON)
    p4 = next(p for p in plans if p.label == "Trial Patient 4")
    # Two scheduled messages in one window.
    dup = next(m for m in p4.messages if m.category)
    p4.messages.append(type(dup)("patient", dup.at + timedelta(minutes=30), "again",
                                 category="admin"))
    p4.messages.sort(key=lambda m: m.at)
    assert any("two scheduled messages in window" in x for x in validate_plan(plans, NOON))


def test_a_v1_patient_is_computed_with_v1_and_everyone_else_with_v2():
    plans = {p.label: p for p in build_plan(NOON, True)}
    legacy = plans["Trial Patient 14"]
    old, _ = simulate(legacy, DEFAULT_SCHEDULES[1], NOON)
    new, _ = simulate(legacy, DEFAULT_SCHEDULES[2], NOON)
    assert old.phase is Phase.OPEN and old.interval_no == 4  # v1: window 4 (days 7-14) is open
    assert new.interval_no == 3  # the same dates under v2 would be window 3: not what v1 says
    assert old.episode_closes_at != new.episode_closes_at  # day 49 vs day 59
