"""Migration 21 against a real Postgres: the environment marker and what it guards.

app_settings holds one row ('production' or 'staging') that nothing inserts for you; the reset and
restore scripts and the demo-clinic trigger refuse on 'production'; the api's role can read the
row and never change it."""

import shutil
import subprocess

import pytest
from psycopg import errors

from tests.db.conftest import SCRIPTS, build_world

pytestmark = pytest.mark.db

RESET_SQL = SCRIPTS / "reset_trial_data.sql"
RESTORE_SQL = SCRIPTS / "restore_trial_data.sql"
RESET_SH = SCRIPTS / "reset_trial_data.sh"
RESTORE_SH = SCRIPTS / "restore_trial_data.sh"

needs_psql = pytest.mark.skipif(
    not (shutil.which("psql") and shutil.which("pg_dump")), reason="psql and pg_dump needed"
)


def set_env(conn, environment: str | None):
    conn.execute("delete from app_settings")
    if environment:
        conn.execute("insert into app_settings (environment) values (%s)", (environment,))


def psql(scratch, *args):
    return subprocess.run(
        ["psql", scratch.conninfo, "-X", "-v", "ON_ERROR_STOP=1", *args],
        capture_output=True, text=True,
    )


def patients(conn) -> int:
    return conn.execute("select count(*) as n from patients").fetchone()["n"]


# --- the table ---------------------------------------------------------------------------

def test_the_migration_inserts_no_row(db):
    assert db.conn.execute("select count(*) as n from app_settings").fetchone()["n"] == 0


def test_the_row_is_staging_or_production_and_there_is_only_one(db):
    c = db.conn
    with pytest.raises(errors.CheckViolation):
        c.execute("insert into app_settings (environment) values ('development')")
    c.execute("insert into app_settings (environment) values ('staging')")
    with pytest.raises(errors.UniqueViolation):
        c.execute("insert into app_settings (environment) values ('production')")
    with pytest.raises(errors.NotNullViolation):
        c.execute("insert into app_settings (singleton, environment) values (false, null)")


def test_the_api_role_can_read_the_row_and_never_change_it(db):
    c = db.conn
    set_env(c, "staging")
    c.execute("set role service_role")
    try:
        assert c.execute("select environment from app_settings").fetchone() == {
            "environment": "staging"
        }
        for sql in (
            "update app_settings set environment = 'production'",
            "delete from app_settings",
            "insert into app_settings (environment) values ('production')",
            "select assert_not_production('x')",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                c.execute(sql)
    finally:
        c.execute("reset role")


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_clinicians_and_patients_cannot_read_it(db, role):
    set_env(db.conn, "staging")
    db.conn.execute(f"set role {role}")
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            db.conn.execute("select environment from app_settings")
    finally:
        db.conn.execute("reset role")


# --- assert_not_production ---------------------------------------------------------------

@pytest.mark.parametrize("environment", [None, "staging"])
def test_assert_not_production_passes_unless_the_row_says_production(db, environment):
    set_env(db.conn, environment)
    db.conn.execute("select assert_not_production('a test')")


def test_assert_not_production_raises_on_production(db):
    set_env(db.conn, "production")
    with pytest.raises(errors.RaiseException, match="a test refused: this database is PRODUCTION"):
        db.conn.execute("select assert_not_production('a test')")


# --- demo clinics ------------------------------------------------------------------------

def test_a_demo_clinic_can_be_made_on_staging_and_where_no_row_is_set_yet(db):
    c = db.conn
    for environment in ("staging", None):
        set_env(c, environment)
        c.execute("insert into clinics (name, is_demo) values ('Demo', true)")
        w = build_world(c) if environment is None else None
        if w:
            c.execute("update clinics set is_demo = true where id = %s", (w.clinic_a,))
        c.execute("delete from clinics where name = 'Demo'")


def test_a_demo_clinic_is_refused_in_production_by_insert_and_by_update(db):
    c = db.conn
    set_env(c, "staging")
    w = build_world(c)
    set_env(c, "production")
    with pytest.raises(errors.RaiseException, match="demo clinic refused"):
        c.execute("insert into clinics (name, is_demo) values ('Demo', true)")
    with pytest.raises(errors.RaiseException, match="PRODUCTION"):
        c.execute("update clinics set is_demo = true where id = %s", (w.clinic_a,))
    # An ordinary clinic is untouched by the trigger, and so is an update that isn't about is_demo.
    c.execute("insert into clinics (name) values ('Real clinic')")
    c.execute("update clinics set name = 'Renamed' where id = %s", (w.clinic_a,))
    assert c.execute("select count(*) as n from clinics where is_demo").fetchone()["n"] == 0


def test_the_api_role_still_creates_ordinary_clinics_in_production(db):
    c = db.conn
    set_env(c, "production")
    c.execute("set role service_role")
    try:
        c.execute("insert into clinics (name) values ('Real clinic')")
    finally:
        c.execute("reset role")


# --- the reset and restore scripts -------------------------------------------------------

@pytest.mark.parametrize("sql", [RESET_SQL, RESTORE_SQL])
def test_the_sql_scripts_refuse_on_production_before_touching_anything(db, sql):
    c = db.conn
    set_env(c, "staging")
    build_world(c)
    set_env(c, "production")
    out = psql(db, "-v", "execute=true", "-v", "mode=commit", "-v", "tables=patients",
               "-v", "backup=/nonexistent", "-f", str(sql))
    assert out.returncode != 0 and "PRODUCTION" in out.stderr
    assert "will wipe" not in out.stderr
    assert patients(c) == 3


@pytest.mark.parametrize("environment", ["staging", None])
def test_the_reset_runs_on_staging_and_where_no_row_is_set_yet(db, environment):
    c = db.conn
    set_env(c, environment)
    build_world(c)
    out = psql(db, "-v", "execute=true", "-f", str(RESET_SQL))
    assert out.returncode == 0, out.stderr
    assert patients(c) == 0
    # The marker is kept, never wiped.
    assert c.execute("select count(*) as n from app_settings").fetchone()["n"] == (
        1 if environment else 0
    )


@needs_psql
@pytest.mark.parametrize("script", [RESET_SH, RESTORE_SH])
def test_the_shell_scripts_stop_early_on_production(db, script, tmp_path):
    c = db.conn
    set_env(c, "production")
    backup = tmp_path / "backup.sql"
    backup.write_text("-- empty\n")
    args = [str(script)] + ([str(backup)] if script == RESTORE_SH else [])
    env = {"PATH": "/usr/bin:/bin:" + __import__("os").environ["PATH"], "DATABASE_URL": db.conninfo}
    out = subprocess.run(args, env=env, capture_output=True, text=True)
    assert out.returncode == 1 and "PRODUCTION" in out.stderr
    assert "Target:" not in out.stdout  # stopped before it even named the target


@needs_psql
def test_the_reset_script_dry_run_shows_the_environment_and_changes_nothing(db):
    import os

    c = db.conn
    set_env(c, "staging")
    build_world(c)
    out = subprocess.run(
        [str(RESET_SH)], env={**os.environ, "DATABASE_URL": db.conninfo},
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "Environment: staging" in out.stdout and "DRY RUN" in out.stdout
    assert patients(c) == 3


@needs_psql
def test_the_shell_scripts_refuse_a_database_without_migration_21(db_pre21):
    import os

    out = subprocess.run(
        [str(RESET_SH)], env={**os.environ, "DATABASE_URL": db_pre21.conninfo},
        capture_output=True, text=True,
    )
    assert out.returncode == 1 and "migration 21" in out.stderr
