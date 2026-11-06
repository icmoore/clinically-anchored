"""Migration 20 against a real Postgres: the append-only acceptance record, who can read it, and the
new 'withdrawn' reason for closing an episode."""

import pytest
from psycopg import errors

from tests.db.conftest import build_world, make_episode

pytestmark = pytest.mark.db

H = {"terms_sha256": "a" * 64, "privacy_sha256": "b" * 64, "consent_sha256": "c" * 64}


def accept(c, w, episode, *, action="accepted", patient=None, **over):
    row = {
        "clinic_id": w.clinic_a, "patient_id": patient or w.patient_a, "episode_id": episode,
        "action": action, "terms_version": "1.0", "privacy_version": "1.0",
        "consent_version": "ai_assisted_replies:1.0;messaging:1.0", **H, **over,
    }
    cols = ", ".join(["id", *row])
    marks = ", ".join(["gen_random_uuid()", *["%s"] * len(row)])
    return str(c.execute(f"insert into patient_acceptances ({cols}) values ({marks}) returning id",
                         list(row.values())).fetchone()["id"])


def test_a_row_is_kept_and_nothing_can_change_or_remove_it(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    row = accept(c, w, e)
    with pytest.raises(errors.RaiseException, match="patient_acceptances_append_only"):
        c.execute("update patient_acceptances set action = 'declined' where id = %s", (row,))
    with pytest.raises(errors.RaiseException, match="patient_acceptances_append_only"):
        c.execute("delete from patient_acceptances where id = %s", (row,))
    # A newer answer is a new row; the older one stays.
    accept(c, w, e, action="withdrawn")
    n = c.execute("select count(*) as n from patient_acceptances").fetchone()["n"]
    assert n == 2


def test_the_reset_script_can_still_empty_it(db):
    c = db.conn
    w = build_world(c)
    accept(c, w, make_episode(c, w))
    c.execute("truncate table public.patient_acceptances")
    assert c.execute("select count(*) as n from patient_acceptances").fetchone()["n"] == 0


@pytest.mark.parametrize("over", [
    {"action": "agreed"},
    {"terms_sha256": "xyz"},
    {"privacy_sha256": "A" * 64},  # upper case
    {"consent_sha256": "c" * 63},
    {"terms_version": ""},
    {"consent_version": "x" * 201},
])
def test_a_row_must_be_well_formed(db, over):
    c = db.conn
    w = build_world(c)
    with pytest.raises(errors.CheckViolation):
        accept(c, w, make_episode(c, w), **over)


def test_the_episode_must_be_the_patients_own(db):
    c = db.conn
    w = build_world(c)
    other = make_episode(c, w, patient=w.patient_a2)
    with pytest.raises(errors.ForeignKeyViolation):
        accept(c, w, other)  # patient A, patient A2's episode


def test_only_the_clinics_staff_can_read_and_nobody_signed_in_can_write(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    accept(c, w, e)
    for sub, expected in ((w.user_a, 1), (w.user_b, 0)):
        c.execute("set role authenticated")
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (sub,))
        try:
            n = c.execute("select count(*) as n from patient_acceptances").fetchone()["n"]
            assert n == expected
            with pytest.raises(errors.InsufficientPrivilege):
                accept(c, w, e)
        finally:
            c.execute("reset role")
    c.execute("set role anon")
    try:
        with pytest.raises((errors.InsufficientPrivilege, errors.UndefinedTable)):
            c.execute("select count(*) from patient_acceptances")
    finally:
        c.execute("reset role")


def test_an_episode_can_be_closed_as_withdrawn_by_the_patient(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    patient_user = str(c.execute("insert into auth.users (email) values ('p@x.org') returning id"
                                 ).fetchone()["id"])
    with pytest.raises(errors.RaiseException, match="episode_bad_closer"):
        c.execute("select close_episode(%s, %s, 'withdrawn', null)", (w.clinic_a, e))
    out = c.execute("select close_episode(%s, %s, 'withdrawn', %s) as r",
                    (w.clinic_a, e, patient_user)).fetchone()["r"]
    assert out["changed"] is True
    ep = out["episode"]
    assert ep["close_reason"] == "withdrawn" and ep["closed_by"] == patient_user
    again = c.execute("select close_episode(%s, %s, 'withdrawn', %s) as r",
                      (w.clinic_a, e, patient_user)).fetchone()["r"]
    assert again["changed"] is False


def test_the_other_close_reasons_work_as_before_and_others_are_refused(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    with pytest.raises(errors.RaiseException, match="episode_bad_reason"):
        c.execute("select close_episode(%s, %s, 'gone', %s)", (w.clinic_a, e, w.user_a))
    with pytest.raises(errors.RaiseException, match="episode_bad_closer"):
        c.execute("select close_episode(%s, %s, 'discharged', null)", (w.clinic_a, e))
    out = c.execute("select close_episode(%s, %s, 'discharged', %s) as r",
                    (w.clinic_a, e, w.user_a)).fetchone()["r"]
    assert out["episode"]["close_reason"] == "discharged"
