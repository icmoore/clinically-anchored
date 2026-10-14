"""Real-Postgres tests for the migrations and for supabase/scripts/reset_trial_data.*.

Skipped unless TEST_DATABASE_URL is set to a Postgres server on THIS machine (a localhost
or unix-socket URL; the fixture refuses anything else, so these tests can never touch the
shared Supabase project). Each test gets its own scratch database cloned from a template
that has the migrations applied on top of a stub of Supabase's roles and auth schema.

    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres uv run pytest -m db

The reset-script tests also need `psql` and `pg_dump` on PATH.
"""

import os
import re
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict, make_conninfo  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
MIGRATIONS = REPO / "supabase" / "migrations"
SCRIPTS = REPO / "supabase" / "scripts"
STUB = Path(__file__).with_name("supabase_stub.sql")


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/db/" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(pytest.mark.db)


def _admin_info() -> dict:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    info = conninfo_to_dict(url)
    host = info.get("host", "")
    if not (host in ("localhost", "127.0.0.1", "::1") or host.startswith("/")):
        raise RuntimeError(
            f"TEST_DATABASE_URL must point at a local server, not {host!r}: these tests create "
            "and drop databases."
        )
    return info


def _connect(info: dict, dbname: str | None = None) -> "psycopg.Connection":
    conn = psycopg.connect(
        make_conninfo(**{**info, **({"dbname": dbname} if dbname else {})}),
        autocommit=True,
        row_factory=dict_row,
    )
    return conn


def migration_files(upto: int) -> list[Path]:
    files = sorted(MIGRATIONS.glob("*.sql"))
    return [f for f in files if int(re.match(r"(\d+)_", f.name).group(1)) <= upto]


def _build_template(info: dict, upto: int) -> str:
    name = f"ca_tpl_{upto}_{uuid.uuid4().hex[:8]}"
    with _connect(info) as admin:
        admin.execute(f'create database "{name}"')
    with _connect(info, name) as conn:
        conn.execute(STUB.read_text())
        has_pgcrypto = conn.execute(
            "select count(*) as n from pg_available_extensions where name = 'pgcrypto'"
        ).fetchone()["n"]
        for f in migration_files(upto):
            text = f.read_text()
            if not has_pgcrypto:
                # Embedded Postgres builds may lack contrib; gen_random_uuid() is built in
                # since Postgres 13, so migration 1's `create extension` is all that is lost.
                text = text.replace('create extension if not exists "pgcrypto";', "")
            conn.execute(text)
    return name


@pytest.fixture(scope="session")
def admin_info():
    return _admin_info()


@pytest.fixture(scope="session")
def template_all(admin_info):
    name = _build_template(admin_info, 999)
    yield name
    with _connect(admin_info) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture(scope="session")
def template_pre9(admin_info):
    """Schema as it is in production today: migrations 1-8."""
    name = _build_template(admin_info, 8)
    yield name
    with _connect(admin_info) as admin:
        admin.execute(f'drop database if exists "{name}" with (force)')


@dataclass
class Scratch:
    conn: "psycopg.Connection"
    info: dict
    name: str

    @property
    def conninfo(self) -> str:
        """libpq keyword string for psql / pg_dump subprocesses."""
        return make_conninfo(**{**self.info, "dbname": self.name})


def _scratch(admin_info, template):
    name = f"ca_t_{uuid.uuid4().hex[:10]}"
    with _connect(admin_info) as admin:
        admin.execute(f'create database "{name}" template "{template}"')
    conn = _connect(admin_info, name)
    try:
        yield Scratch(conn=conn, info=admin_info, name=name)
    finally:
        conn.close()
        with _connect(admin_info) as admin:
            admin.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture
def db(admin_info, template_all):
    """A scratch database with every migration applied."""
    yield from _scratch(admin_info, template_all)


@pytest.fixture
def db_pre9(admin_info, template_pre9):
    """A scratch database as production is today (migrations 1-8)."""
    yield from _scratch(admin_info, template_pre9)


@dataclass
class World:
    clinic_a: str
    clinic_b: str
    user_a: str  # member of clinic A
    user_b: str  # member of clinic B
    proc_a: str
    proc_b: str
    patient_a: str
    patient_a2: str
    patient_b: str


def _one(conn, query, params=()):
    return conn.execute(query, params).fetchone()


def build_world(conn) -> World:
    """Two clinics, a member each, a procedure each, patients (2 in A, 1 in B).
    Works on both the pre-9 and the current schema (no episodes needed)."""
    ids = {}
    for key in ("a", "b"):
        ids[f"clinic_{key}"] = _one(
            conn, "insert into clinics (name) values (%s) returning id", (f"Clinic {key}",)
        )["id"]
        ids[f"user_{key}"] = _one(
            conn, "insert into auth.users (email) values (%s) returning id", (f"{key}@example.com",)
        )["id"]
        conn.execute(
            "insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'clinician')",
            (ids[f"clinic_{key}"], ids[f"user_{key}"]),
        )
        ids[f"proc_{key}"] = _one(
            conn,
            "insert into procedures (clinic_id, name, category) values (%s, 'Proc', 'other') "
            "returning id",
            (ids[f"clinic_{key}"],),
        )["id"]
    for key, clinic, name in (
        ("patient_a", "clinic_a", "Pat A"),
        ("patient_a2", "clinic_a", "Pat A2"),
        ("patient_b", "clinic_b", "Pat B"),
    ):
        ids[key] = _one(
            conn,
            "insert into patients (clinic_id, full_name) values (%s, %s) returning id",
            (ids[clinic], name),
        )["id"]
    return World(**{k: str(v) for k, v in ids.items()})


def make_episode(
    conn, w: World, *, patient=None, clinic=None, procedure=None, user=None,
    surgery_date: date = date(2026, 10, 1), version: int = 1,
) -> str:
    return str(
        _one(
            conn,
            "insert into episodes (clinic_id, patient_id, procedure_id, surgery_date, "
            "schedule_version, created_by) values (%s, %s, %s, %s, %s, %s) returning id",
            (
                clinic or w.clinic_a, patient or w.patient_a, procedure or w.proc_a,
                surgery_date, version, user or w.user_a,
            ),
        )["id"]
    )


def check_in(conn, w: World, episode: str, clinic=None) -> dict:
    return _one(
        conn,
        "select complete_check_in(%s, %s, 3, '{}'::jsonb, false) as r",
        (clinic or w.clinic_a, episode),
    )["r"]


def patient_msg(conn, w: World, episode: str, origin="scheduled", interval=1, category="medication",
                body="hello", clinic=None) -> dict:
    return _one(
        conn,
        "select * from send_patient_message(%s, %s, %s, %s, %s, %s)",
        (clinic or w.clinic_a, episode, origin, interval if origin == "scheduled" else None,
         category if origin == "scheduled" else None, body),
    )


def clinician_msg(conn, w: World, episode: str, body="hi", clinic=None) -> dict:
    return _one(
        conn,
        "select * from send_clinician_message(%s, %s, %s)",
        (clinic or w.clinic_a, episode, body),
    )
