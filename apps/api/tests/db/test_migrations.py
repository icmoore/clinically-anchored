"""One real-Postgres test home for every migration.

Migrations 9, 10, 13-16, 17 and 18 have their own modules (see COVERED_BY); this file covers the
rest (1-8, 11, 12), and the last test fails if a migration is added without a test being named
here, so "a real-Postgres test for every migration" cannot silently lapse."""

import re
from pathlib import Path

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

# migration number -> the test module (in tests/db) that exercises it against a real Postgres
COVERED_BY = {
    1: "test_migrations.py", 2: "test_migrations.py", 3: "test_migrations.py",
    4: "test_migrations.py", 5: "test_migrations.py", 6: "test_migrations.py",
    7: "test_migrations.py", 8: "test_migrations.py",
    9: "test_messaging_v2_schema.py", 10: "test_thread_summaries.py",
    11: "test_migrations.py", 12: "test_migrations.py",
    13: "test_patient_briefs.py", 14: "test_care_provider_summaries.py",
    15: "test_care_provider_summaries.py", 16: "test_follow_up_extensions.py",
    17: "test_email_outbox.py", 18: "test_patient_accounts_schema.py",
}
DEV_CLINIC = "00000000-0000-0000-0000-0000000000c1"


def as_user(c, role, sub=""):
    c.execute(f"set role {role}")
    c.execute("select set_config('request.jwt.claim.sub', %s, false)", (sub,))


def n(c, sql, *params):
    return c.execute(sql, params).fetchone()["n"]


# --- 1: the initial schema -----------------------------------------------------------------


def test_1_staff_see_only_their_own_clinics_rows(db):
    c = db.conn
    w = build_world(c)
    clinician_msg(c, w, make_episode(c, w))
    as_user(c, "authenticated", w.user_a)
    try:
        assert n(c, "select count(*) as n from patients") == 2  # clinic A's two, never B's
        assert n(c, "select count(*) as n from patients where clinic_id = %s", w.clinic_b) == 0
        assert n(c, "select count(*) as n from messages") == 1
        assert n(c, "select count(*) as n from clinic_members") == 1  # only their own membership
        assert c.execute("select is_clinic_member(%s) as m", (w.clinic_a,)).fetchone()["m"] is True
        assert c.execute("select is_clinic_member(%s) as m", (w.clinic_b,)).fetchone()["m"] is False
    finally:
        c.execute("reset role")


def test_1_the_audit_log_cannot_be_changed_or_emptied_by_signed_in_users(db):
    c = db.conn
    w = build_world(c)
    c.execute("insert into audit_log (clinic_id, event_type, payload_hash, row_hash, signature, "
              "key_id) values (%s, 'x', 'p', 'r', 's', 'k')", (w.clinic_a,))
    for role in ("authenticated", "anon"):
        as_user(c, role, w.user_a)
        try:
            for sql in ("update audit_log set event_type = 'y'", "delete from audit_log",
                        "truncate audit_log"):
                with pytest.raises(errors.InsufficientPrivilege):
                    c.execute(sql)
        finally:
            c.execute("reset role")


# --- 2: the dev clinic -----------------------------------------------------------------------


def test_2_the_dev_clinic_exists_and_its_fixture_patient_is_gone_after_migration_9(db):
    c = db.conn
    assert c.execute("select name from clinics where id = %s", (DEV_CLINIC,)).fetchone()[
        "name"].endswith("(dev)")
    assert n(c, "select count(*) as n from patients where clinic_id = %s", DEV_CLINIC) == 0
    assert n(c, "select count(*) as n from procedures where clinic_id = %s", DEV_CLINIC) >= 16


# --- 3: the audit chain ------------------------------------------------------------------------


def append(c, clinic, prev, row_hash, key="k"):
    return c.execute(
        "select * from append_audit_event(%s, 'e', 'p', %s, %s, 's', %s, '{}'::jsonb, now())",
        (clinic, prev, row_hash, key),
    ).fetchone()


def test_3_events_append_in_a_chain_and_a_fork_or_a_stale_head_is_refused(db):
    c = db.conn
    w = build_world(c)
    first = append(c, w.clinic_a, None, "h1")
    assert first["prev_hash"] is None and first["row_hash"] == "h1"
    second = append(c, w.clinic_a, "h1", "h2")
    assert second["prev_hash"] == "h1"
    with pytest.raises(Exception, match="audit_chain_conflict"):
        append(c, w.clinic_a, "h1", "h3")  # the head is h2 now: a fork
    with pytest.raises(Exception, match="audit_chain_conflict"):
        append(c, w.clinic_a, None, "h4")  # claiming to be first again
    assert append(c, w.clinic_b, None, "b1")["prev_hash"] is None  # each clinic has its own chain


def test_3_the_chain_cannot_be_forked_by_writing_rows_directly(db):
    c = db.conn
    w = build_world(c)
    append(c, w.clinic_a, None, "h1")
    with pytest.raises(errors.UniqueViolation):
        c.execute("insert into audit_log (clinic_id, event_type, payload_hash, prev_hash, "
                  "row_hash, signature, key_id) values (%s, 'e', 'p', null, 'x', 's', 'k')",
                  (w.clinic_a,))


# --- 4: check-in review ----------------------------------------------------------------------


def test_4_a_check_in_can_be_marked_reviewed_and_the_queue_indexes_exist(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    row = c.execute("select reviewed_at, reviewed_by from check_ins").fetchone()
    assert row["reviewed_at"] is None and row["reviewed_by"] is None
    c.execute("update check_ins set reviewed_at = now(), reviewed_by = %s", (w.user_a,))
    assert n(c, "select count(*) as n from check_ins where reviewed_at is not null") == 1
    names = {r["indexname"] for r in c.execute("select indexname from pg_indexes").fetchall()}
    assert {"check_ins_unreviewed_idx", "messages_unread_patient_idx"} <= names
    with pytest.raises(errors.ForeignKeyViolation):
        c.execute("update check_ins set reviewed_by = gen_random_uuid()")


# --- 5: consents ------------------------------------------------------------------------------


def consent(c, w, kind="messaging", version="v1", patient=None):
    c.execute("insert into consents (clinic_id, patient_id, consent_type, consent_text_version) "
              "values (%s, %s, %s, %s)", (w.clinic_a, patient or w.patient_a, kind, version))


def test_5_one_live_grant_per_type_and_version_and_a_regrant_after_withdrawal(db):
    c = db.conn
    w = build_world(c)
    consent(c, w)
    with pytest.raises(errors.UniqueViolation):
        consent(c, w)
    consent(c, w, version="v2")  # a new wording is a new grant
    consent(c, w, patient=w.patient_a2)  # another patient's is separate
    c.execute("update consents set revoked_at = now() where consent_text_version = 'v1' "
              "and patient_id = %s", (w.patient_a,))
    consent(c, w)  # live again after the withdrawal: history keeps both rows
    assert n(c, "select count(*) as n from consents where patient_id = %s", w.patient_a) == 3


def test_5_a_consent_type_is_a_slug_and_a_version_is_short(db):
    c = db.conn
    w = build_world(c)
    for bad_type in ("Bad Type", "1abc", "", "x" * 65):
        with pytest.raises(errors.CheckViolation):
            consent(c, w, kind=bad_type)
    for bad_version in ("", "v" * 65):
        with pytest.raises(errors.CheckViolation):
            consent(c, w, version=bad_version)


def test_5_staff_read_their_clinics_consents_and_cannot_write_any(db):
    c = db.conn
    w = build_world(c)
    consent(c, w)
    as_user(c, "authenticated", w.user_a)
    try:
        assert n(c, "select count(*) as n from consents") == 1
        with pytest.raises(errors.InsufficientPrivilege):
            consent(c, w, version="v9")
    finally:
        c.execute("reset role")
    as_user(c, "authenticated", w.user_b)
    try:
        assert n(c, "select count(*) as n from consents") == 0
    finally:
        c.execute("reset role")


# --- 6: AI message drafts --------------------------------------------------------------------


def draft(c, w, source, **kw):
    cols = {"clinic_id": w.clinic_a, "patient_id": w.patient_a, "source_message_id": source,
            "draft_text": "Hello", "model_id": "m", "prompt_version": "p", "created_by": w.user_a,
            **kw}
    return str(c.execute(
        f"insert into message_drafts ({', '.join(cols)}) values "
        f"({', '.join(['%s'] * len(cols))}) returning id", list(cols.values())
    ).fetchone()["id"])


def drafts_world(c):
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    clinician_msg(c, w, e)
    src = patient_msg(c, w, e, origin="reply")
    return w, e, str(src["id"])


def test_6_a_draft_is_pending_until_one_decision_and_never_deleted(db):
    c = db.conn
    w, e, src = drafts_world(c)
    did = draft(c, w, src)
    with pytest.raises(errors.UniqueViolation):
        draft(c, w, src)  # one pending draft per message
    with pytest.raises(Exception, match="message_drafts_retained"):
        c.execute("delete from message_drafts where id = %s", (did,))
    with pytest.raises(Exception, match="message_drafts_original"):
        c.execute("update message_drafts set draft_text = 'changed' where id = %s", (did,))
    out = c.execute("select decide_message_draft(%s, %s, 'approved', null, %s) as r",
                    (w.clinic_a, did, w.user_a)).fetchone()["r"]
    assert out["draft"]["status"] == "approved" and out["message"]["body"] == "Hello"
    with pytest.raises(Exception, match="draft_not_pending"):
        c.execute("select decide_message_draft(%s, %s, 'rejected', null, %s)",
                  (w.clinic_a, did, w.user_a))
    with pytest.raises(Exception, match="message_drafts_decided"):
        c.execute("update message_drafts set status = 'pending' where id = %s", (did,))
    # one message was sent for it, and it is the draft's
    assert n(c, "select count(*) as n from messages where body = 'Hello' and sender = 'clinician'"
             ) == 1


def test_6_a_draft_row_must_be_in_a_consistent_state(db):
    c = db.conn
    w, e, src = drafts_world(c)
    did = draft(c, w, src)
    for sql in (
        "update message_drafts set status = 'approved' where id = %s",  # nothing recorded
        "update message_drafts set final_text = 'x' where id = %s",  # pending with a final text
    ):
        with pytest.raises(errors.CheckViolation):
            c.execute(sql, (did,))
    with pytest.raises(errors.CheckViolation):
        draft(c, w, src, draft_text="   ", status="pending")  # empty text
    with pytest.raises(Exception, match="draft_edit_invalid"):
        c.execute("select decide_message_draft(%s, %s, 'edited', 'Hello', %s)",
                  (w.clinic_a, did, w.user_a))  # an edit must differ
    with pytest.raises(Exception, match="draft_bad_decision"):
        c.execute("select decide_message_draft(%s, %s, 'maybe', null, %s)",
                  (w.clinic_a, did, w.user_a))
    other_clinic = c.execute("select decide_message_draft(%s, %s, 'rejected', null, %s) as r",
                             (w.clinic_a, did, w.user_a)).fetchone()["r"]
    assert other_clinic["draft"]["status"] == "rejected" and other_clinic["message"] is None


def test_6_a_draft_cannot_be_decided_through_another_clinic(db):
    c = db.conn
    w, e, src = drafts_world(c)
    did = draft(c, w, src)
    with pytest.raises(Exception, match="draft_not_found"):
        c.execute("select decide_message_draft(%s, %s, 'rejected', null, %s)",
                  (w.clinic_b, did, w.user_b))


def test_6_staff_read_drafts_but_cannot_write_them(db):
    c = db.conn
    w, e, src = drafts_world(c)
    draft(c, w, src)
    as_user(c, "authenticated", w.user_a)
    try:
        assert n(c, "select count(*) as n from message_drafts") == 1
        with pytest.raises(errors.InsufficientPrivilege):
            draft(c, w, src)
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("select decide_message_draft(%s, gen_random_uuid(), 'rejected', null, %s)",
                      (w.clinic_a, w.user_a))
    finally:
        c.execute("reset role")


# --- 7: the procedure list and its categories -----------------------------------------------


def test_7_procedures_have_a_category_and_the_surgeons_list_is_the_active_one(db):
    c = db.conn
    cats = {r["category"] for r in c.execute("select category from procedures").fetchall()}
    assert cats <= {"abdominal_general", "abdominal_bowel_resection", "anorectal", "other"}
    old = c.execute("select active from procedures where id::text like '00000000-0000-0000-0000-0000000000d%%'"
                    ).fetchall()
    assert len(old) == 3 and not any(r["active"] for r in old)  # the placeholders, retired
    active = {r["name"] for r in c.execute(
        "select name from procedures where clinic_id = %s and active", (DEV_CLINIC,)).fetchall()}
    assert {"Appendectomy", "Right hemicolectomy", "Hemorrhoid surgery", "Other"} <= active
    assert len(active) == 16
    with pytest.raises(errors.NotNullViolation):
        c.execute("insert into procedures (clinic_id, name) values (%s, 'No category')",
                  (DEV_CLINIC,))
    with pytest.raises(errors.CheckViolation):
        c.execute("insert into procedures (clinic_id, name, category) values (%s, 'x', 'bogus')",
                  (DEV_CLINIC,))


# --- 8: the contact log ----------------------------------------------------------------------


def test_8_the_contact_log_takes_kinds_and_sources_and_staff_only_read_it(db):
    c = db.conn
    w = build_world(c)

    def log(kind="call", source="manual", **kw):
        cols = {"clinic_id": w.clinic_a, "patient_id": w.patient_a, "kind": kind,
                "source": source, **kw}
        c.execute(f"insert into touchpoints ({', '.join(cols)}) values "
                  f"({', '.join(['%s'] * len(cols))})", list(cols.values()))

    log()
    log(kind="message", source="auto", duration_minutes=None)
    for bad in (dict(kind="fax"), dict(source="robot"), dict(duration_minutes=0)):
        with pytest.raises(errors.CheckViolation):
            log(**bad)
    as_user(c, "authenticated", w.user_a)
    try:
        assert n(c, "select count(*) as n from touchpoints") == 2
        with pytest.raises(errors.InsufficientPrivilege):
            log()  # staff write through the api, not the table (migration 9 closed it)
    finally:
        c.execute("reset role")
    as_user(c, "authenticated", w.user_b)
    try:
        assert n(c, "select count(*) as n from touchpoints") == 0
    finally:
        c.execute("reset role")


# --- 11: schedule v2 -----------------------------------------------------------------------


def test_11_v2_is_the_confirmed_schedule_and_an_episodes_version_is_frozen(db):
    c = db.conn
    v2 = c.execute("select * from message_schedules where version = 2").fetchone()
    assert v2["offsets_days"] == [2, 5, 10, 17, 24, 31, 38, 45, 52]
    assert v2["close_after_days"] == 7 and v2["timezone"] == "America/Toronto" and v2["active"]
    w = build_world(c)
    make_episode(c, w, version=2)
    with raises_text("message_schedules_in_use"):
        c.execute("update message_schedules set offsets_days = '{2,3}' where version = 2")
    c.execute("update message_schedules set active = false where version = 2")  # allowed
    c.execute("update message_schedules set active = true where version = 2")
    c.execute("update message_schedules set offsets_days = '{2,3}' where version = 1")  # unused


def raises_text(text):
    return pytest.raises(errors.RaiseException, match=text)


# --- 12: the reply guard under pg_dump's empty search_path -----------------------------------


def test_12_the_reply_guard_works_with_an_empty_search_path(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    clinic_msg = clinician_msg(c, w, e)
    c.execute("select set_config('search_path', '', false)")  # what a pg_dump restore starts with
    try:
        c.execute(
            "insert into public.messages (clinic_id, patient_id, episode_id, sender, body, origin, "
            "reply_to_message_id) values (%s, %s, %s, 'patient', 'ok', 'reply', %s)",
            (w.clinic_a, w.patient_a, e, clinic_msg["id"]),
        )
        with raises_text("reply_target_invalid"):
            c.execute(
                "insert into public.messages (clinic_id, patient_id, episode_id, sender, body, "
                "origin, reply_to_message_id) values (%s, %s, %s, 'patient', 'bad', 'reply', "
                "gen_random_uuid())", (w.clinic_a, w.patient_a, e),
            )
    finally:
        c.execute("select set_config('search_path', '\"$user\", public', false)")


# --- every migration has a test --------------------------------------------------------------


def test_every_migration_has_a_named_real_postgres_test():
    numbers = {int(re.match(r"(\d+)_", f.name).group(1)) for f in migration_files(999)}
    assert numbers == set(COVERED_BY), (
        "Add the new migration to COVERED_BY with the test module that exercises it: "
        f"missing {sorted(numbers - set(COVERED_BY))}, stale {sorted(set(COVERED_BY) - numbers)}"
    )
    here = Path(__file__).parent
    for number, module in COVERED_BY.items():
        assert (here / module).is_file(), (number, module)
