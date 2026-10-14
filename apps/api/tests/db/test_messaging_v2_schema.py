"""Migration 9 against a real Postgres: what the database itself guarantees about
episodes, schedules, scheduled messages, replies and discharge. (Which interval is open
is the api's engine; none of that is tested here.)"""

from datetime import UTC, datetime

import psycopg
import pytest
from psycopg import errors

from tests.db.conftest import (
    build_world,
    check_in,
    clinician_msg,
    make_episode,
    migration_files,
    patient_msg,
)

pytestmark = pytest.mark.db


def raises_text(text: str):
    """A function raised the named exception code."""
    return pytest.raises(errors.RaiseException, match=text)


def as_member(conn, user: str, role: str = "authenticated"):
    conn.execute(f"set role {role}")
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (user,))


def as_owner(conn):
    conn.execute("reset role")


# --- applying the migration ---------------------------------------------------------


def test_migrations_apply_from_scratch_and_drop_the_dev_fixture_patient(db):
    assert db.conn.execute("select count(*) as n from patients").fetchone()["n"] == 0
    assert db.conn.execute("select count(*) as n from clinics").fetchone()["n"] == 1  # dev clinic


def test_migration_refuses_to_run_over_existing_patient_data(db_pre9):
    build_world(db_pre9.conn)
    migration = [f for f in migration_files(9) if f.name.startswith("00000000000009")][0]
    with raises_text("messaging_v2_needs_empty_patient_data"):
        db_pre9.conn.execute(migration.read_text())
    # ...and it applied nothing: the transaction rolled back.
    assert db_pre9.conn.execute("select to_regclass('public.episodes') as t").fetchone()["t"] is None


# --- schedules ----------------------------------------------------------------------


def test_v1_schedule_is_seeded(db):
    row = db.conn.execute("select * from message_schedules where version = 1").fetchone()
    assert row["offsets_days"] == [2, 3, 5, 7, 14, 21, 28, 35, 42]
    assert row["close_after_days"] == 7
    assert str(row["opens_at_local"]) == "07:00:00"
    assert row["timezone"] == "America/Toronto"


@pytest.mark.parametrize("offsets", ["{3,3}", "{5,2}", "{0,2}", "{}", "{2,NULL,5}"])
def test_bad_offsets_are_rejected(db, offsets):
    with pytest.raises(errors.CheckViolation):
        db.conn.execute(
            "insert into message_schedules (version, offsets_days, close_after_days, "
            "opens_at_local, timezone) values (9, %s, 7, '07:00', 'America/Toronto')",
            (offsets,),
        )


def test_opening_time_before_dst_changeover_hours_is_rejected(db):
    with pytest.raises(errors.CheckViolation):
        db.conn.execute(
            "insert into message_schedules (version, offsets_days, close_after_days, "
            "opens_at_local, timezone) values (9, '{2}', 7, '01:30', 'America/Toronto')"
        )


def test_unknown_timezone_is_rejected(db):
    with pytest.raises(psycopg.Error):
        db.conn.execute(
            "insert into message_schedules (version, offsets_days, close_after_days, "
            "opens_at_local, timezone) values (9, '{2}', 7, '07:00', 'Mars/Olympus')"
        )


def test_second_schedule_version_can_be_added_and_v1_edited_until_used(db):
    c = db.conn
    c.execute(
        "insert into message_schedules (version, offsets_days, close_after_days, opens_at_local, "
        "timezone) values (2, '{2,5,10,17,24,31,38,45,52}', 7, '07:00', 'America/Toronto')"
    )
    c.execute("update message_schedules set offsets_days = '{2,4}' where version = 1")  # unused
    w = build_world(c)
    make_episode(c, w, version=1)
    with raises_text("message_schedules_in_use"):
        c.execute("update message_schedules set offsets_days = '{2,3}' where version = 1")
    with raises_text("message_schedules_in_use"):
        c.execute("delete from message_schedules where version = 1")
    c.execute("update message_schedules set note = 'ok' where version = 2")  # unused, still editable


def test_signed_in_users_can_read_but_not_write_schedules(db):
    w = build_world(db.conn)
    as_member(db.conn, w.user_a)
    assert db.conn.execute("select count(*) as n from message_schedules").fetchone()["n"] == 1
    with pytest.raises(errors.InsufficientPrivilege):
        db.conn.execute("update message_schedules set note = 'x'")
    as_owner(db.conn)


# --- episodes -----------------------------------------------------------------------


def test_an_episode_cannot_point_at_another_clinics_patient_or_procedure(db):
    w = build_world(db.conn)
    with pytest.raises(errors.ForeignKeyViolation):
        make_episode(db.conn, w, patient=w.patient_b)  # clinic A, patient of B
    with pytest.raises(errors.ForeignKeyViolation):
        make_episode(db.conn, w, procedure=w.proc_b)


def test_one_open_episode_per_patient(db):
    w = build_world(db.conn)
    first = make_episode(db.conn, w)
    with pytest.raises(errors.UniqueViolation):
        make_episode(db.conn, w)
    db.conn.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_a, first, w.user_a))
    make_episode(db.conn, w)  # a new surgery for the same patient is fine once the first closed


def test_close_columns_must_agree(db):
    w = build_world(db.conn)
    e = make_episode(db.conn, w)
    with pytest.raises(errors.CheckViolation):  # discharge needs who
        db.conn.execute(
            "update episodes set closed_at = now(), close_reason = 'discharged' where id = %s", (e,)
        )
    with pytest.raises(errors.CheckViolation):  # natural close has nobody
        db.conn.execute(
            "update episodes set closed_at = now(), close_reason = 'schedule_complete', "
            "closed_by = %s where id = %s",
            (w.user_a, e),
        )


def test_episodes_are_never_deleted(db):
    w = build_world(db.conn)
    e = make_episode(db.conn, w)
    with raises_text("episodes_retained"):
        db.conn.execute("delete from episodes where id = %s", (e,))


def test_surgery_date_can_be_corrected_only_before_check_in(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    c.execute("update episodes set surgery_date = '2026-10-02' where id = %s", (e,))
    c.execute("update episodes set procedure_id = %s where id = %s", (w.proc_a, e))
    check_in(c, w, e)
    with raises_text("episodes_surgery_locked"):
        c.execute("update episodes set surgery_date = '2026-10-03' where id = %s", (e,))


def test_link_generation_only_goes_up_and_a_closed_episode_is_frozen(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    c.execute("update episodes set link_generation = link_generation + 1 where id = %s", (e,))
    with raises_text("episodes_link_generation"):
        c.execute("update episodes set link_generation = 1 where id = %s", (e,))
    c.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_a, e, w.user_a))
    with raises_text("episodes_closed"):
        c.execute("update episodes set link_generation = 9 where id = %s", (e,))


def test_clinic_members_read_only_their_own_clinics_episodes_and_cannot_write(db):
    c = db.conn
    w = build_world(c)
    make_episode(c, w)
    as_member(c, w.user_a)
    assert c.execute("select count(*) as n from episodes").fetchone()["n"] == 1
    with pytest.raises(errors.InsufficientPrivilege):
        c.execute("update episodes set link_generation = 5")
    as_member(c, w.user_b)
    assert c.execute("select count(*) as n from episodes").fetchone()["n"] == 0
    as_owner(c)


# --- check-in ------------------------------------------------------------------------


def test_complete_check_in_stores_it_and_unlocks_once(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    out = check_in(c, w, e)
    assert out["episode"]["checkin_completed_at"] == out["check_in"]["created_at"]
    assert out["check_in"]["procedure_id"] == w.proc_a  # the clinic's choice, not the patient's
    assert out["check_in"]["patient_id"] == w.patient_a
    with raises_text("check_in_already_done"):
        check_in(c, w, e)
    assert c.execute("select count(*) as n from check_ins").fetchone()["n"] == 1


def test_only_one_check_in_row_per_episode_even_if_inserted_directly(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    with pytest.raises(errors.UniqueViolation):
        c.execute(
            "insert into check_ins (clinic_id, patient_id, episode_id) values (%s, %s, %s)",
            (w.clinic_a, w.patient_a, e),
        )


def test_check_in_on_a_closed_or_other_clinics_episode_is_refused(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    with raises_text("episode_not_found"):
        check_in(c, w, e, clinic=w.clinic_b)
    c.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_a, e, w.user_a))
    with raises_text("episode_closed"):
        check_in(c, w, e)


# --- scheduled messages --------------------------------------------------------------


def test_scheduled_message_needs_the_check_in_first(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    with raises_text("check_in_required"):
        patient_msg(c, w, e)
    check_in(c, w, e)
    m = patient_msg(c, w, e, interval=3, category="symptom_or_wound")
    assert (m["origin"], m["interval_no"], m["category"], m["sender"]) == (
        "scheduled", 3, "symptom_or_wound", "patient",
    )
    assert str(m["patient_id"]) == w.patient_a and str(m["clinic_id"]) == w.clinic_a


def test_one_message_per_interval_but_another_interval_is_fine(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    patient_msg(c, w, e, interval=2)
    with raises_text("interval_already_used"):
        patient_msg(c, w, e, interval=2)
    patient_msg(c, w, e, interval=3)


def test_the_unique_index_backs_the_function_if_it_is_bypassed(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    patient_msg(c, w, e, interval=1)
    with pytest.raises(errors.UniqueViolation):
        c.execute(
            "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin, "
            "interval_no, category) values (%s, %s, %s, 'patient', 'dup', 'scheduled', 1, 'admin')",
            (w.clinic_a, w.patient_a, e),
        )


def test_category_is_required_and_must_be_a_key(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    with raises_text("message_bad_origin"):
        c.execute("select * from send_patient_message(%s, %s, 'scheduled', 1, null, 'x')", (w.clinic_a, e))
    with pytest.raises(errors.CheckViolation):
        patient_msg(c, w, e, category="Not A Key")


def test_blank_or_oversized_bodies_are_refused(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    with raises_text("message_body_invalid"):
        patient_msg(c, w, e, body="   ")
    with raises_text("message_body_invalid"):
        patient_msg(c, w, e, body="x" * 5001)
    with raises_text("message_body_invalid"):
        clinician_msg(c, w, e, body="")


# --- replies --------------------------------------------------------------------------


def test_a_doctor_message_allows_exactly_one_reply_even_before_the_check_in(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    with raises_text("no_reply_available"):
        patient_msg(c, w, e, origin="reply")
    d = clinician_msg(c, w, e)
    r = patient_msg(c, w, e, origin="reply")  # no check-in yet: allowed
    assert (r["origin"], r["interval_no"], r["category"]) == ("reply", None, None)
    assert r["reply_to_message_id"] == d["id"]
    with raises_text("no_reply_available"):
        patient_msg(c, w, e, origin="reply")


def test_a_reply_does_not_use_the_interval_slot(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    clinician_msg(c, w, e)
    patient_msg(c, w, e, origin="reply")
    patient_msg(c, w, e, interval=1)  # still available


def test_the_reply_answers_the_latest_doctor_message_and_older_ones_are_superseded(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    first = clinician_msg(c, w, e, body="first")
    second = clinician_msg(c, w, e, body="second")
    r = patient_msg(c, w, e, origin="reply")
    assert r["reply_to_message_id"] == second["id"] != first["id"]
    with raises_text("no_reply_available"):
        patient_msg(c, w, e, origin="reply")
    third = clinician_msg(c, w, e, body="third")  # a new doctor message re-opens a reply
    assert patient_msg(c, w, e, origin="reply")["reply_to_message_id"] == third["id"]


def test_a_reply_must_target_a_clinician_message_in_the_same_episode(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    own = patient_msg(c, w, e, interval=1)
    with raises_text("reply_target_invalid"):
        c.execute(
            "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin, "
            "reply_to_message_id) values (%s, %s, %s, 'patient', 'x', 'reply', %s)",
            (w.clinic_a, w.patient_a, e, own["id"]),
        )


# --- discharge and closed episodes ------------------------------------------------------


def test_nothing_can_be_written_to_a_closed_episode(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    clinician_msg(c, w, e)
    c.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_a, e, w.user_a))
    for call in (
        lambda: patient_msg(c, w, e, interval=1),
        lambda: patient_msg(c, w, e, origin="reply"),
        lambda: clinician_msg(c, w, e),
    ):
        with raises_text("episode_closed"):
            call()


def test_close_episode_is_idempotent_and_reports_who_changed_it(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    first = c.execute(
        "select close_episode(%s, %s, 'discharged', %s) as r", (w.clinic_a, e, w.user_a)
    ).fetchone()["r"]
    assert first["changed"] is True
    assert first["episode"]["close_reason"] == "discharged"
    assert first["episode"]["closed_by"] == w.user_a
    second = c.execute(
        "select close_episode(%s, %s, 'schedule_complete', null) as r", (w.clinic_a, e)
    ).fetchone()["r"]
    assert second["changed"] is False
    assert second["episode"]["close_reason"] == "discharged"  # the first close stands


def test_natural_close_records_the_effective_time_and_not_the_future(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    r = c.execute(
        "select close_episode(%s, %s, 'schedule_complete', null, "
        "'2026-01-01T12:00:00Z'::timestamptz) as r",
        (w.clinic_a, e),
    ).fetchone()["r"]
    closed_at = datetime.fromisoformat(r["episode"]["closed_at"])
    assert closed_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert r["episode"]["closed_by"] is None
    e2 = make_episode(c, w, patient=w.patient_a2)
    with raises_text("episode_close_in_future"):
        c.execute(
            "select close_episode(%s, %s, 'schedule_complete', null, now() + interval '1 hour')",
            (w.clinic_a, e2),
        )


def test_close_episode_argument_checks(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    with raises_text("episode_bad_reason"):
        c.execute("select close_episode(%s, %s, 'because', null)", (w.clinic_a, e))
    with raises_text("episode_bad_closer"):
        c.execute("select close_episode(%s, %s, 'discharged', null)", (w.clinic_a, e))
    with raises_text("episode_bad_closer"):
        c.execute("select close_episode(%s, %s, 'schedule_complete', %s)", (w.clinic_a, e, w.user_a))
    with raises_text("episode_not_found"):
        c.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_b, e, w.user_b))


# --- the message table's own constraints -------------------------------------------------


def test_message_shape_is_enforced_for_each_origin(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    bad = [
        # a "clinician" message from a patient
        "'patient', 'x', 'clinician', null, null, null",
        # a scheduled message with no interval
        "'patient', 'x', 'scheduled', null, 'admin', null",
        # a clinician message carrying an interval and category
        "'clinician', 'x', 'clinician', 1, 'admin', null",
        # system messages are no longer possible
        "'system', 'x', 'clinician', null, null, null",
    ]
    for values in bad:
        with pytest.raises(errors.CheckViolation):
            c.execute(
                "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin, "
                f"interval_no, category, reply_to_message_id) values (%s, %s, %s, {values})",
                (w.clinic_a, w.patient_a, e),
            )


def test_a_message_cannot_name_a_different_patient_or_clinic_than_its_episode(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    with pytest.raises(errors.ForeignKeyViolation):
        c.execute(
            "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin) "
            "values (%s, %s, %s, 'clinician', 'x', 'clinician')",
            (w.clinic_a, w.patient_a2, e),
        )
    with pytest.raises(errors.ForeignKeyViolation):
        c.execute(
            "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin) "
            "values (%s, %s, %s, 'clinician', 'x', 'clinician')",
            (w.clinic_b, w.patient_a, e),
        )


def test_only_read_at_can_change_on_a_message(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    m = patient_msg(c, w, e, interval=4, category="admin")
    c.execute("update messages set read_at = now() where id = %s", (m["id"],))
    for sql in (
        "update messages set body = 'edited' where id = %s",
        "update messages set interval_no = 5 where id = %s",
        "update messages set category = 'medication' where id = %s",
    ):
        with raises_text("messages_immutable"):
            c.execute(sql, (m["id"],))


# --- AI drafts -------------------------------------------------------------------------


def _draft(c, w, e, source):
    return c.execute(
        "insert into message_drafts (clinic_id, patient_id, source_message_id, draft_text, "
        "model_id, prompt_version, created_by) values (%s, %s, %s, 'draft', 'm', 'draft_reply_v3', %s) "
        "returning id",
        (w.clinic_a, w.patient_a, source, w.user_a),
    ).fetchone()["id"]


def test_approving_a_draft_sends_a_clinician_message_into_the_episode(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    src = patient_msg(c, w, e, interval=1)
    d = _draft(c, w, e, src["id"])
    out = c.execute(
        "select decide_message_draft(%s, %s, 'approved', null, %s) as r", (w.clinic_a, d, w.user_a)
    ).fetchone()["r"]
    sent = out["message"]
    assert (sent["sender"], sent["origin"], sent["episode_id"]) == ("clinician", "clinician", e)
    # ...which grants the patient a reply, like any doctor message.
    assert str(patient_msg(c, w, e, origin="reply")["reply_to_message_id"]) == sent["id"]


def test_a_draft_cannot_be_sent_into_a_closed_episode_but_can_be_rejected(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    src = patient_msg(c, w, e, interval=1)
    d1 = _draft(c, w, e, src["id"])
    c.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_a, e, w.user_a))
    with raises_text("episode_closed"):
        c.execute("select decide_message_draft(%s, %s, 'approved', null, %s)", (w.clinic_a, d1, w.user_a))
    out = c.execute(
        "select decide_message_draft(%s, %s, 'rejected', null, %s) as r", (w.clinic_a, d1, w.user_a)
    ).fetchone()["r"]
    assert out["draft"]["status"] == "rejected" and out["message"] is None


# --- privileges ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin) "
        "values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'clinician', 'x', 'clinician')",
        "insert into patients (clinic_id, full_name) values (gen_random_uuid(), 'x')",
        "insert into touchpoints (clinic_id, patient_id, kind, source) "
        "values (gen_random_uuid(), gen_random_uuid(), 'call', 'manual')",
        "update patients set full_name = 'x'",
        "delete from patients",
        "truncate audit_log",
    ],
)
def test_clinic_members_can_no_longer_write_these_tables_directly(db, sql):
    w = build_world(db.conn)
    as_member(db.conn, w.user_a)
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            db.conn.execute(sql)
    finally:
        as_owner(db.conn)


def test_clinic_members_can_still_read_their_threads_and_cannot_call_the_write_functions(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    clinician_msg(c, w, e)
    as_member(c, w.user_a)
    try:
        assert c.execute("select count(*) as n from messages").fetchone()["n"] == 1
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("select * from send_clinician_message(%s, %s, 'x')", (w.clinic_a, e))
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_a, e, w.user_a))
        as_member(c, w.user_b)
        assert c.execute("select count(*) as n from messages").fetchone()["n"] == 0
    finally:
        as_owner(c)


def test_the_service_role_can_call_the_write_functions(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    c.execute("set role service_role")
    try:
        c.execute("select * from send_clinician_message(%s, %s, 'x')", (w.clinic_a, e))
    finally:
        as_owner(c)
