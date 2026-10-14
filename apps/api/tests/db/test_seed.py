"""The trial-patient seed against the real migrations: every patient lands in the state the plan
says, the api shows them, the audit chain verifies and `reconcile` finds nothing unaudited."""

import base64
from datetime import datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient

from clinically_anchored_api import seed_trial_patients as seed
from clinically_anchored_api.core import audit, clock, episodes, reconcile
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 14, 15, 30, tzinfo=seed.TORONTO)

# What the api writes in an event's readable metadata (plus the salt record_event adds and the
# seed's tag). If the api changes an event, the seed must follow: this is the drift guard.
API_METADATA = {
    "patient.created": {"ref_type", "ref_id", "actor"},
    "episode.created": {"ref_type", "ref_id", "patient_id", "schedule_version", "actor"},
    "episode.closed": {"ref_type", "ref_id", "patient_id", "schedule_version", "actor"},
    "check_in.submitted": {"ref_type", "ref_id", "episode_id", "actor"},
    "check_in.reviewed": {"ref_type", "ref_id", "actor"},
    "message.sent": {
        "ref_type", "ref_id", "actor", "episode_id", "origin", "interval_no", "category",
    },
    "message.read": {"ref_type", "ref_id", "actor"},
    "touchpoint.logged": {"ref_type", "ref_id", "actor"},
}


@pytest.fixture
def env(db, monkeypatch):
    conn = db.conn
    pg = PgSupabase(conn)
    install(monkeypatch, pg, audit=False)
    settings = get_settings()
    key = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(key).decode())
    monkeypatch.setattr(settings, "audit_key_id", "test-1")
    monkeypatch.setattr(clock, "now", lambda: NOW)
    user = conn.execute("insert into auth.users (email) values ('doc@example.com') returning id")
    user_id = str(user.fetchone()["id"])

    class Env:
        pass

    e = Env()
    e.pg, e.conn, e.user_id = pg, conn, user_id
    return e


def run_seed(env, alternate=True):
    pg = env.pg
    if alternate:
        seed.ensure_alternate_schedule(pg)
    plans = seed.build_plan(NOW, alternate)
    schedules = seed.load_schedules(pg, {p.schedule_version for p in plans})
    assert seed.validate_plan(plans, NOW, schedules) == []
    clinic_id = seed.ensure_demo_clinic(pg, env.user_id)
    for plan in plans:
        seed.seed_patient(
            pg, clinic_id=clinic_id, user_id=env.user_id, plan=plan,
            schedule=schedules[plan.schedule_version],
        )
    return clinic_id, plans


def counts(conn):
    return {
        t: conn.execute(f"select count(*) as n from {t}").fetchone()["n"]
        for t in ("patients", "episodes", "check_ins", "messages", "touchpoints", "audit_log")
    }


def test_every_patient_is_in_the_state_the_plan_says(env):
    clinic_id, plans = run_seed(env)
    rows = env.pg.table("episodes").select(episodes.COLUMNS).eq("clinic_id", clinic_id).execute().data
    by_patient = {r["patient_id"]: r for r in rows}
    states = episodes.states_for(env.pg, rows, NOW)
    patients = {
        p["contact"]: p["id"]
        for p in env.pg.table("patients").select("id, contact").eq("clinic_id", clinic_id)
        .execute().data
    }
    assert len(plans) == len(rows) == 14
    for plan in plans:
        episode = by_patient[patients[plan.contact]]
        state = states[episode["id"]]
        assert (state.phase, state.missed, state.can_reply) == (
            plan.expect.phase, plan.expect.missed, plan.expect.reply,
        ), plan.label
        assert episode["schedule_version"] == plan.schedule_version
    assert {s.phase for s in states.values()} == set(seed.Phase)


def test_audit_chain_verifies_and_reconcile_finds_nothing_unaudited(env):
    clinic_id, _ = run_seed(env)
    rows = audit.fetch_chain(env.pg, clinic_id)
    chain = audit.verify_chain(rows, audit.trusted_public_keys())
    assert chain["ok"], chain
    report = reconcile.reconcile(env.pg, clinic_id=clinic_id)
    assert report.ok, report.unaudited
    assert report.dangling == []
    kinds = {r["event_type"] for r in rows}
    assert set(API_METADATA) <= kinds
    assert all(r["metadata"]["actor"] == seed.SEED_ACTOR and r["metadata"]["seed"] == seed.SEED_TAG
               for r in rows)


def test_seeded_events_carry_the_same_readable_metadata_as_the_apis(env):
    clinic_id, _ = run_seed(env)
    seen: dict[str, set] = {}
    for row in audit.fetch_chain(env.pg, clinic_id):
        kind = row["event_type"]
        if kind in API_METADATA:
            seen.setdefault(kind, set()).add(frozenset(row["metadata"]))
    for kind, expected in API_METADATA.items():
        assert seen[kind] == {frozenset(expected | {"salt", "seed"})}, kind


def test_messages_obey_the_databases_rules_and_replies_answer_the_clinic(env):
    run_seed(env)
    c = env.conn
    by_origin = {
        r["origin"]: r["n"]
        for r in c.execute("select origin, count(*) as n from messages group by origin").fetchall()
    }
    assert set(by_origin) == {"scheduled", "reply", "clinician"}
    # A reply always points at a clinic message of its own episode (the database trigger says so).
    orphans = c.execute(
        "select count(*) as n from messages r left join messages d on d.id = r.reply_to_message_id "
        "where r.origin = 'reply' and (d.id is null or d.origin <> 'clinician' "
        "or d.episode_id <> r.episode_id)"
    ).fetchone()["n"]
    assert orphans == 0
    # One check-in per episode, and a scheduled message never precedes it.
    early = c.execute(
        "select count(*) as n from messages m join check_ins ci on ci.episode_id = m.episode_id "
        "where m.origin = 'scheduled' and m.created_at <= ci.created_at"
    ).fetchone()["n"]
    assert early == 0


def test_the_seed_closes_what_it_says_it_closes(env):
    run_seed(env)
    rows = env.conn.execute(
        "select close_reason, closed_by is not null as by_user, count(*) as n from episodes "
        "where closed_at is not null group by 1, 2 order by 1"
    ).fetchall()
    assert [(r["close_reason"], r["by_user"], r["n"]) for r in rows] == [
        ("discharged", True, 1), ("schedule_complete", False, 1),
    ]


def test_running_it_again_adds_nothing(env):
    run_seed(env)
    before = counts(env.conn)
    run_seed(env)
    assert counts(env.conn) == before


def test_without_the_alternate_flag_no_second_schedule_is_added(env):
    run_seed(env, alternate=False)
    versions = env.conn.execute("select version from message_schedules order by 1").fetchall()
    assert [v["version"] for v in versions] == [1]  # new patients keep getting v1
    assert counts(env.conn)["episodes"] == 13


def test_the_api_shows_the_seeded_patients(env):
    clinic_id, plans = run_seed(env)
    env.conn.execute(
        "insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'owner') "
        "on conflict do nothing", (clinic_id, env.user_id),
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer user:{env.user_id}"}
    listed = client.get(f"/clinics/{clinic_id}/patients", headers=headers).json()
    assert len(listed) == 14
    phases = {p["full_name"]: p["episode"]["state"]["phase"] for p in listed}
    for plan in plans:
        assert phases[plan.full_name] == plan.expect.phase.value
    queue = client.get(f"/clinics/{clinic_id}/queue", headers=headers).json()
    names = [q["patient_name"] for q in queue]
    assert "Nadia Haddad" in names and "Amara Nwosu" in names  # awaiting a reply
    nadia = next(q for q in queue if q["patient_name"] == "Nadia Haddad")
    assert nadia["has_red_flag"] and nadia["unread_categories"] == ["symptom_or_wound"]


def test_a_patient_link_for_a_seeded_episode_opens_what_the_plan_says(env, monkeypatch):
    from clinically_anchored_api.core.security import create_episode_token

    monkeypatch.setattr(get_settings(), "patient_link_reads_per_minute", 0)
    clinic_id, plans = run_seed(env)
    client = TestClient(app)
    rows = {
        r["patient_id"]: r
        for r in env.pg.table("episodes").select(episodes.COLUMNS).execute().data
    }
    patients = {
        p["contact"]: p["id"] for p in env.pg.table("patients").select("id, contact").execute().data
    }
    for plan in plans:
        episode = rows[patients[plan.contact]]
        token = create_episode_token(episode_id=episode["id"], generation=1)
        state = client.get("/episode", params={"token": token}).json()
        assert state["phase"] == plan.expect.phase.value, plan.label
        thread = client.get("/messages", params={"token": token})
        assert thread.status_code == (410 if plan.expect.phase is seed.Phase.CLOSED else 200)
