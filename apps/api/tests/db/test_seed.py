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


def run_seed(env, v1_patient=True):
    pg = env.pg
    plans = seed.build_plan(NOW, v1_patient)
    schedules = seed.load_schedules(pg, {1, 2, 3})
    assert seed.validate_plan(plans, NOW, schedules) == []
    clinic_id = seed.ensure_demo_clinic(pg, env.user_id)
    for plan in plans:
        seed.seed_patient(
            pg, clinic_id=clinic_id, user_id=env.user_id, plan=plan, schedules=schedules
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
    by_patient = {r["patient_id"]: r for r in rows if r["kind"] == "main"}
    states = episodes.states_for(env.pg, rows, NOW)
    patients = {
        p["contact"]: p["id"]
        for p in env.pg.table("patients").select("id, contact").eq("clinic_id", clinic_id)
        .execute().data
    }
    assert len(plans) == 17 and len(rows) == 18  # one follow-up extension episode (patient 16)
    for plan in plans:
        episode = by_patient[patients[plan.contact]]
        state = states[episode["id"]]
        assert (state.phase, state.missed, state.can_reply) == (
            plan.expect.phase, plan.expect.missed, plan.expect.reply,
        ), plan.label
        assert episode["schedule_version"] == plan.schedule_version
        ext = seed.extension_episode(plan)
        if ext is not None:
            child = next(r for r in rows if r["extends_episode_id"] == episode["id"])
            child_state = states[child["id"]]
            assert (child_state.phase, child_state.missed) == (ext.expect.phase, ext.expect.missed)
            assert child["kind"] == "extension" and child["schedule_version"] == 3
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
    assert set(by_origin) == {
        "scheduled", "reply", "clinician", "extension_request", "extension_decision",
    }
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
        ("discharged", True, 1), ("schedule_complete", False, 4),  # 13, 15, 16, 17
    ]


def test_running_it_again_adds_nothing(env):
    run_seed(env)
    before = counts(env.conn)
    run_seed(env)
    assert counts(env.conn) == before


def test_the_seed_never_touches_the_schedules_or_which_one_new_patients_get(env):
    before = env.conn.execute("select * from message_schedules order by version").fetchall()
    run_seed(env, v1_patient=False)
    assert env.conn.execute("select * from message_schedules order by version").fetchall() == before
    assert counts(env.conn)["episodes"] == 17  # 16 patients and patient 16's extension
    versions = {r["schedule_version"] for r in env.conn.execute("select schedule_version from episodes")}
    # The confirmed schedule; v1 is only used by the optional patient 14, v3 by the extension.
    assert versions == {2, 3}
    assert episodes.default_schedule(env.pg).version == 2  # still what a new dashboard patient gets


def test_the_optional_v1_patient_keeps_v1_beside_everyone_on_v2(env):
    clinic_id, plans = run_seed(env, v1_patient=True)
    rows = env.conn.execute(
        "select p.full_name, e.schedule_version from episodes e join patients p on p.id = e.patient_id "
        "where e.kind = 'main'"
    ).fetchall()
    by_name = {r["full_name"]: r["schedule_version"] for r in rows}
    assert by_name.pop("Gerald Fox") == 1
    assert set(by_name.values()) == {2} and len(by_name) == 16
    assert episodes.default_schedule(env.pg).version == 2


def test_the_api_shows_the_seeded_patients(env):
    clinic_id, plans = run_seed(env)
    env.conn.execute(
        "insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'owner') "
        "on conflict do nothing", (clinic_id, env.user_id),
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer user:{env.user_id}"}
    listed = client.get(f"/clinics/{clinic_id}/patients", headers=headers).json()
    assert len(listed) == 17
    phases = {p["full_name"]: p["episode"]["state"]["phase"] for p in listed}
    for plan in plans:
        # The latest episode is shown: patient 16's is the follow-up extension.
        want = seed.extension_episode(plan).expect if seed.extension_episode(plan) else plan.expect
        assert phases[plan.full_name] == want.phase.value
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
        if r["kind"] == "main"
    }
    patients = {
        p["contact"]: p["id"] for p in env.pg.table("patients").select("id, contact").execute().data
    }
    for plan in plans:
        episode = rows[patients[plan.contact]]
        token = create_episode_token(episode_id=episode["id"], generation=1)
        state = client.get("/episode", params={"token": token}).json()
        # The first episode's link opens the NEWEST episode: patient 16's extension.
        ext = seed.extension_episode(plan)
        want = ext.expect if ext else plan.expect
        assert state["phase"] == want.phase.value, plan.label
        thread = client.get("/messages", params={"token": token})
        assert thread.status_code == (410 if want.phase is seed.Phase.CLOSED else 200)
        # Follow-up extension states: what the patient's screen shows.
        standing = (state["extension"] or {}).get("standing")
        expected = {
            "Trial Patient 13": "eligible", "Trial Patient 15": "pending",
            "Trial Patient 16": "accepted", "Trial Patient 17": "declined",
        }.get(plan.label)
        assert standing == expected, plan.label
        if plan.label == "Trial Patient 16":
            assert state["extension"]["extension_no"] == 1 and state["intervals_total"] == 6
            assert state["extension"]["decision_message"] == plan.extension.decision_message


def test_the_dev_clinic_can_be_seeded_too_without_touching_membership(env):
    # The dev clinic (migrations 2 and 7) exists; the account is already a member of it.
    env.conn.execute(
        "insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'owner')",
        (seed.SOURCE_CLINIC_ID, env.user_id),
    )
    members_before = counts_of(env.conn, "clinic_members")
    plans = seed.build_plan(NOW)
    schedules = seed.load_schedules(env.pg, {1, 2, 3})
    assert seed.validate_plan(plans, NOW, schedules) == []
    clinic_id = seed.ensure_dev_clinic(env.pg, env.user_id)
    assert clinic_id == seed.SOURCE_CLINIC_ID
    for plan in plans:
        seed.seed_patient(
            env.pg, clinic_id=clinic_id, user_id=env.user_id, plan=plan, schedules=schedules
        )
    assert counts_of(env.conn, "clinic_members") == members_before
    rows = audit.fetch_chain(env.pg, clinic_id)
    assert audit.verify_chain(rows, audit.trusted_public_keys())["ok"]
    assert reconcile.reconcile(env.pg, clinic_id=clinic_id).ok
    # Its own chain: the demo clinic (not seeded here) has no events.
    demo = env.conn.execute("select count(*) as n from clinics where name = %s", (seed.DEMO_CLINIC_NAME,))
    assert demo.fetchone()["n"] == 0


def test_seeding_the_dev_clinic_refuses_an_account_that_is_not_a_member(env):
    with pytest.raises(SystemExit, match="not a member"):
        seed.ensure_dev_clinic(env.pg, env.user_id)


def counts_of(conn, table):
    return conn.execute(f"select count(*) as n from {table}").fetchone()["n"]
