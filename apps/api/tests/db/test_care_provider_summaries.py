"""Migration 14 against a real Postgres: saved care provider summaries are append-only, same-clinic
and same-episode, and members can read their clinic's but not write."""

import json

import pytest
from psycopg import errors

from tests.db.conftest import build_world, make_episode

pytestmark = pytest.mark.db

COVERS = json.dumps({"messages": 1, "check_ins": 0, "touchpoints": 0})


def save(conn, clinic, patient, episode, user):
    return conn.execute(
        "insert into care_provider_summaries (id, clinic_id, patient_id, episode_id, "
        "summary_text, covers, truncated, model_id, prompt_version, generated_by, generated_at) "
        "values (gen_random_uuid(), %s, %s, %s, 'A short paragraph.', %s, false, 'm', "
        "'care_provider_summary_v1', %s, now()) returning id",
        (clinic, patient, episode, COVERS, user),
    ).fetchone()["id"]


def test_a_saved_summary_is_never_changed_or_deleted(db):
    w = build_world(db.conn)
    e = make_episode(db.conn, w)
    sid = save(db.conn, w.clinic_a, w.patient_a, e, w.user_a)
    with pytest.raises(Exception, match="care_provider_summaries_retained"):
        db.conn.execute("update care_provider_summaries set summary_text = 'x' where id = %s", (sid,))
    with pytest.raises(Exception, match="care_provider_summaries_retained"):
        db.conn.execute("delete from care_provider_summaries where id = %s", (sid,))


def test_a_summary_cannot_name_another_clinics_patient_or_another_patients_episode(db):
    w = build_world(db.conn)
    e = make_episode(db.conn, w)
    with pytest.raises(errors.ForeignKeyViolation):
        save(db.conn, w.clinic_a, w.patient_b, e, w.user_a)
    with pytest.raises(errors.ForeignKeyViolation):
        save(db.conn, w.clinic_a, w.patient_a2, e, w.user_a)  # the episode is patient A's


def test_members_read_their_clinics_summaries_but_cannot_write(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    save(c, w.clinic_a, w.patient_a, e, w.user_a)
    c.execute("set role authenticated")
    try:
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_a,))
        assert c.execute("select count(*) as n from care_provider_summaries").fetchone()["n"] == 1
        with pytest.raises(errors.InsufficientPrivilege):
            save(c, w.clinic_a, w.patient_a, e, w.user_a)
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_b,))
        assert c.execute("select count(*) as n from care_provider_summaries").fetchone()["n"] == 0
    finally:
        c.execute("reset role")


# --- migration 15: the physician's edits ----------------------------------------------------------


def save_edit(conn, clinic, patient, summary, user, text="edited"):
    return conn.execute(
        "insert into care_provider_summary_edits (id, clinic_id, patient_id, summary_id, "
        "edited_text, edited_by, edited_at) values (gen_random_uuid(), %s, %s, %s, %s, %s, now()) "
        "returning id",
        (clinic, patient, summary, text, user),
    ).fetchone()["id"]


def test_a_saved_edit_is_never_changed_or_deleted_and_the_ai_text_stays(db):
    w = build_world(db.conn)
    e = make_episode(db.conn, w)
    sid = save(db.conn, w.clinic_a, w.patient_a, e, w.user_a)
    eid = save_edit(db.conn, w.clinic_a, w.patient_a, sid, w.user_a)
    save_edit(db.conn, w.clinic_a, w.patient_a, sid, w.user_a, "edited again")  # both are kept
    with pytest.raises(Exception, match="care_provider_summary_edits_retained"):
        db.conn.execute("update care_provider_summary_edits set edited_text = 'x' where id = %s", (eid,))
    with pytest.raises(Exception, match="care_provider_summary_edits_retained"):
        db.conn.execute("delete from care_provider_summary_edits where id = %s", (eid,))
    ai_text = db.conn.execute(
        "select summary_text from care_provider_summaries where id = %s", (sid,)
    ).fetchone()["summary_text"]
    assert ai_text == "A short paragraph."


def test_an_edit_cannot_name_another_patients_summary(db):
    w = build_world(db.conn)
    e = make_episode(db.conn, w)
    sid = save(db.conn, w.clinic_a, w.patient_a, e, w.user_a)
    with pytest.raises(errors.ForeignKeyViolation):
        save_edit(db.conn, w.clinic_a, w.patient_a2, sid, w.user_a)  # the summary is patient A's
    with pytest.raises(errors.ForeignKeyViolation):
        save_edit(db.conn, w.clinic_a, w.patient_b, sid, w.user_a)


def test_members_read_their_clinics_edits_but_cannot_write(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    sid = save(c, w.clinic_a, w.patient_a, e, w.user_a)
    save_edit(c, w.clinic_a, w.patient_a, sid, w.user_a)
    c.execute("set role authenticated")
    try:
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_a,))
        assert c.execute("select count(*) as n from care_provider_summary_edits").fetchone()["n"] == 1
        with pytest.raises(errors.InsufficientPrivilege):
            save_edit(c, w.clinic_a, w.patient_a, sid, w.user_a)
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_b,))
        assert c.execute("select count(*) as n from care_provider_summary_edits").fetchone()["n"] == 0
    finally:
        c.execute("reset role")
