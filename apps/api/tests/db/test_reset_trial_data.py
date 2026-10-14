"""supabase/scripts/reset_trial_data.{sql,sh} against a real Postgres: it wipes exactly the
listed tables (including the ones the database tries to protect), keeps the rest, works
both before and after migration 9, and its safety rails hold."""

import os
import shutil
import subprocess

import pytest
from psycopg import errors

from tests.db.conftest import (
    MIGRATIONS,
    SCRIPTS,
    build_world,
    check_in,
    clinician_msg,
    make_episode,
    patient_msg,
)

pytestmark = pytest.mark.db

SQL = SCRIPTS / "reset_trial_data.sql"
SH = SCRIPTS / "reset_trial_data.sh"
WIPED = ["message_drafts", "touchpoints", "consents", "messages", "check_ins", "patients",
         "audit_log"]
KEPT = ["clinics", "clinic_members", "procedures"]  # plus message_schedules from migration 9

needs_psql = pytest.mark.skipif(
    not (shutil.which("psql") and shutil.which("pg_dump")), reason="psql and pg_dump needed"
)


def count(conn, table: str) -> int:
    return conn.execute(f"select count(*) as n from {table}").fetchone()["n"]


def psql(scratch, *args, execute: bool | None = None):
    cmd = ["psql", scratch.conninfo, "-X", "-v", "ON_ERROR_STOP=1"]
    if execute is not None:
        cmd += ["-v", f"execute={'true' if execute else 'false'}"]
    return subprocess.run([*cmd, *args], capture_output=True, text=True)


def audit_row(conn, clinic: str):
    conn.execute(
        "insert into audit_log (clinic_id, event_type, payload_hash, prev_hash, row_hash, "
        "signature, key_id) values (%s, 'patient.created', 'p', null, 'r', 's', 'dev-1')",
        (clinic,),
    )


def populate_pre9(conn):
    """Everything the reset has to get past, in the schema production has today."""
    w = build_world(conn)
    conn.execute(
        "insert into check_ins (clinic_id, patient_id) values (%s, %s)", (w.clinic_a, w.patient_a)
    )
    src = conn.execute(
        "insert into messages (clinic_id, patient_id, sender, body) values (%s, %s, 'patient', 'hi') "
        "returning id",
        (w.clinic_a, w.patient_a),
    ).fetchone()["id"]
    conn.execute(
        "insert into message_drafts (clinic_id, patient_id, source_message_id, draft_text, model_id, "
        "prompt_version, created_by) values (%s, %s, %s, 'd', 'm', 'v', %s)",
        (w.clinic_a, w.patient_a, src, w.user_a),
    )
    conn.execute(
        "insert into touchpoints (clinic_id, patient_id, kind, source) values (%s, %s, 'call', 'manual')",
        (w.clinic_a, w.patient_a),
    )
    conn.execute(
        "insert into consents (clinic_id, patient_id, consent_type, consent_text_version) "
        "values (%s, %s, 'messaging', 'v1')",
        (w.clinic_a, w.patient_a),
    )
    audit_row(conn, w.clinic_a)
    return w


def test_the_protections_that_make_a_plain_delete_fail_are_real(db_pre9):
    populate_pre9(db_pre9.conn)
    with pytest.raises(Exception, match="message_drafts_retained"):
        db_pre9.conn.execute("delete from message_drafts")
    with pytest.raises(errors.ForeignKeyViolation):
        db_pre9.conn.execute("delete from patients")


@needs_psql
def test_dry_run_changes_nothing_and_says_so(db_pre9):
    populate_pre9(db_pre9.conn)
    before = {t: count(db_pre9.conn, t) for t in WIPED + KEPT}
    out = psql(db_pre9, "-f", str(SQL))
    assert out.returncode == 0, out.stderr
    assert "DRY RUN" in out.stdout
    assert "will wipe" in out.stderr  # NOTICE lines
    assert {t: count(db_pre9.conn, t) for t in WIPED + KEPT} == before


@needs_psql
def test_execute_wipes_the_listed_tables_and_keeps_the_rest_before_migration_9(db_pre9):
    c = db_pre9.conn
    w = populate_pre9(c)
    users = count(c, "auth.users")
    kept = {t: count(c, t) for t in KEPT}
    old_max = c.execute("select max(id) as m from audit_log").fetchone()["m"]

    out = psql(db_pre9, "-f", str(SQL), execute=True)
    assert out.returncode == 0, out.stderr
    assert "COMMITTED" in out.stdout
    assert all(count(c, t) == 0 for t in WIPED)
    assert {t: count(c, t) for t in KEPT} == kept
    assert count(c, "auth.users") == users

    # Audit ids are not reused, and the chain starts again from nothing.
    audit_row(c, w.clinic_a)
    assert c.execute("select min(id) as m from audit_log").fetchone()["m"] > old_max

    # The production order: wipe, then apply migration 9. It now goes through.
    c.execute(next(MIGRATIONS.glob("00000000000009_*.sql")).read_text())
    assert count(c, "episodes") == 0 and count(c, "message_schedules") == 1


@needs_psql
def test_execute_after_migration_9_wipes_episodes_too_and_frees_the_schedule(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    clinician_msg(c, w, e)
    patient_msg(c, w, e, interval=1)
    audit_row(c, w.clinic_a)
    with pytest.raises(Exception, match="message_schedules_in_use"):
        c.execute("update message_schedules set note = 'x' where version = 1")

    out = psql(db, "-f", str(SQL), execute=True)
    assert out.returncode == 0, out.stderr
    assert all(count(c, t) == 0 for t in WIPED + ["episodes"])
    assert count(c, "clinics") >= 2 and count(c, "procedures") >= 2 and count(c, "clinic_members") == 2
    c.execute("update message_schedules set note = 'editable again' where version = 1")


@needs_psql
def test_a_table_missing_from_the_list_makes_the_reset_fail_and_roll_back(db):
    c = db.conn
    w = build_world(c)
    c.execute("create table future_feature (patient_id uuid references patients (id))")
    c.execute("insert into future_feature values (%s)", (w.patient_a,))
    out = psql(db, "-f", str(SQL), execute=True)
    assert out.returncode != 0
    assert count(c, "patients") == 3 and count(c, "future_feature") == 1


@needs_psql
def test_the_shell_script_refuses_in_ci_without_a_url_and_without_a_terminal(db):
    c = db.conn
    build_world(c)
    base = {**os.environ, "DATABASE_URL": db.conninfo}
    for var in ("CI", "GITHUB_ACTIONS"):
        env = {**base, var: "true"}
        out = subprocess.run([str(SH), "--execute"], env=env, capture_output=True, text=True)
        assert out.returncode == 1 and "never run in CI" in out.stderr

    no_url = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "CI", "GITHUB_ACTIONS")}
    out = subprocess.run([str(SH)], env=no_url, capture_output=True, text=True)
    assert out.returncode == 2 and "DATABASE_URL" in out.stderr

    clean = {k: v for k, v in base.items() if k not in ("CI", "GITHUB_ACTIONS")}
    out = subprocess.run(
        [str(SH), "--execute"], env=clean, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert out.returncode == 1 and "interactive terminal" in out.stderr
    assert count(c, "patients") == 3

    out = subprocess.run([str(SH)], env=clean, capture_output=True, text=True)  # plain dry run
    assert out.returncode == 0 and "DRY RUN" in out.stdout
    assert count(c, "patients") == 3
