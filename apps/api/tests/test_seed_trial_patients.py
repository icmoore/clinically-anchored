"""The trial-patient seed plan is pure data; check it stays writable whenever
it's run (it is built relative to the run time)."""

from datetime import datetime

import pytest

from clinically_anchored_api.core.rules import evaluate_red_flags
from clinically_anchored_api.seed_trial_patients import TORONTO, build_plan, validate_plan


@pytest.mark.parametrize(
    "local",
    [
        datetime(2026, 10, 6, 0, 5),  # just after midnight
        datetime(2026, 10, 6, 13, 0),
        datetime(2026, 10, 6, 23, 59),
        datetime(2026, 11, 1, 9, 0),  # DST ends overnight
        datetime(2027, 3, 14, 12, 0),  # DST starts overnight
    ],
)
def test_plan_is_valid_at_any_time_of_day(local):
    now = local.replace(tzinfo=TORONTO)
    plans = build_plan(now)
    assert validate_plan(plans, now) == []


def test_plan_shape():
    now = datetime(2026, 10, 6, 13, 0, tzinfo=TORONTO)
    plans = build_plan(now)
    assert len(plans) == 10
    assert [p.label for p in plans] == [f"Trial Patient {i}" for i in range(1, 11)]
    assert len({p.contact for p in plans}) == 10
    assert len({p.full_name for p in plans}) == 10
    for plan in plans:
        assert 5 <= len(plan.messages) <= 30
        assert plan.contact.endswith("@example.com")
    # Only Trial Patients 3 and 9 are left awaiting a reply, on their latest message.
    awaiting = {"Trial Patient 3", "Trial Patient 9"}
    for plan in plans:
        last = plan.messages[-1]
        if plan.label in awaiting:
            assert last.sender == "patient" and last.read_at is None
        else:
            assert last.sender == "clinician"
            assert all(m.read_at is not None for m in plan.messages if m.sender == "patient")
    # Red flags come from the real rule engine, on the check-ins meant to have them.
    flagged = [
        (plan.label, c.post_op_day)
        for plan in plans
        for c in plan.check_ins
        if evaluate_red_flags(c.answers)[0]
    ]
    assert flagged == [
        ("Trial Patient 2", 7),
        ("Trial Patient 3", 6),
        ("Trial Patient 7", 5),
        ("Trial Patient 8", 12),
    ]


def test_reads_happen_before_the_reply():
    now = datetime(2026, 10, 6, 13, 0, tzinfo=TORONTO)
    for plan in build_plan(now):
        for i, m in enumerate(plan.messages):
            if m.read_at is None:
                continue
            reply = next(x for x in plan.messages[i + 1 :] if x.sender == "clinician")
            assert m.at < m.read_at <= reply.at
