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
from tests.db.accounts_env import FakeGoTrue, patient_session
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
    # patient accounts (migration 18): the seed writes them as api/patient_accounts.py does
    "patient_account.invited": {"ref_type", "ref_id", "patient_id", "actor"},
    "patient_account.activated": {"ref_type", "ref_id", "patient_id", "actor"},
    "patient_account.identity_locked": {"ref_type", "ref_id", "patient_id", "actor"},
    "patient_account.access_turned_off": {"ref_type", "ref_id", "patient_id", "actor"},
    "consent.granted": {"ref_type", "ref_id", "patient_id", "actor"},
}


@pytest.fixture
def env(db, monkeypatch):
    conn = db.conn
    pg = PgSupabase(conn)
    pg.gt = pg.auth = FakeGoTrue(conn)  # the Auth users the seed makes for patient accounts
    install(monkeypatch, pg, audit=False)
    settings = get_settings()
    key = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(key).decode())
    monkeypatch.setattr(settings, "audit_key_id", "test-1")
    monkeypatch.setattr(clock, "now", lambda: NOW)
    conn.execute("update message_schedules set active = true where version = 3")  # switch it on
    user = conn.execute("insert into auth.users (email) values ('doc@example.com') returning id")
    user_id = str(user.fetchone()["id"])

    class Env:
        pass

    e = Env()
    e.pg, e.conn, e.user_id = pg, conn, user_id
    e.w = None
    return e


class _World:
    """The one thing patient_session needs of a world: a staff user to record as the inviter."""

    def __init__(self, user_id):
        self.user_a = user_id


def run_seed(env, v1_patient=True):
    pg = env.pg
    plans = seed.build_plan(NOW, v1_patient)
    schedules = seed.load_schedules(pg, {1, 2, 3})
    assert seed.validate_plan(plans, NOW, schedules) == []
    clinic_id = seed.ensure_demo_clinic(pg, env.user_id)
    # The real demo clinic is a demo clinic (SQL only): its mail goes to the demo inbox.
    env.conn.execute("update clinics set is_demo = true where id = %s", (clinic_id,))
    for plan in plans:
        seed.seed_patient(
            pg, clinic_id=clinic_id, user_id=env.user_id, plan=plan, schedules=schedules
        )
    return clinic_id, plans


def counts(conn):
    return {
        t: conn.execute(f"select count(*) as n from {t}").fetchone()["n"]
        for t in ("patients", "episodes", "check_ins", "messages", "touchpoints", "audit_log",
                  "patient_accounts", "consents", "email_outbox")
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
    page = client.get(f"/clinics/{clinic_id}/patients?status=all&limit=50", headers=headers).json()
    listed = page["items"]
    assert len(listed) == page["total"] == 17
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


def test_a_signed_in_seeded_patient_sees_what_the_plan_says(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_reads_per_minute", 0)
    clinic_id, plans = run_seed(env)
    client = TestClient(app)
    patients = {
        p["contact"]: p["id"] for p in env.pg.table("patients").select("id, contact").execute().data
    }
    # Whatever state their account was seeded in, let each patient in to see their screens.
    env.conn.execute("update patient_accounts set status = 'active', "
                     "activated_at = coalesce(activated_at, now()), "
                     "identity_verified_at = coalesce(identity_verified_at, now())")
    for user in env.pg.gt.users.values():
        user["banned"] = False
    for plan in plans:
        headers = patient_session(env.pg, _World(env.user_id), patients[plan.contact])
        state = client.get("/episode", headers=headers).json()
        # A patient's care period is their NEWEST episode: patient 16's extension.
        ext = seed.extension_episode(plan)
        want = ext.expect if ext else plan.expect
        assert state["phase"] == want.phase.value, plan.label
        thread = client.get("/messages", headers=headers)
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
        "insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'owner') "
        "on conflict do nothing",
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


# --- patient accounts ----------------------------------------------------------------------


def accounts(env):
    return {
        r["contact"]: r for r in env.conn.execute(
            "select p.contact, p.email, a.status, a.* from patients p "
            "join patient_accounts a on a.patient_id = p.id"
        ).fetchall()
    }


def test_the_seed_gives_patients_an_email_and_accounts_in_every_state(env):
    clinic_id, plans = run_seed(env)
    every = {r["contact"]: r["email"] for r in env.conn.execute(
        "select contact, email from patients").fetchall()}
    assert len(every) == 17 and all(e.endswith("@example.com") for e in every.values())
    assert every["trial-patient-5@example.com"] == "trial-patient-5.demo@example.com"

    acc = accounts(env)
    states = {c.split("-")[2].split("@")[0]: a["status"] for c, a in acc.items()}
    assert states == {"1": "active", "2": "active", "3": "active", "10": "off", "11": "invited",
                      "12": "locked"}
    # Not invited: everyone else has an email and no account.
    assert len(acc) == 6
    active = acc["trial-patient-1@example.com"]
    assert active["identity_verified_at"] and active["activated_at"] and active["last_sign_in_at"]
    locked = acc["trial-patient-12@example.com"]
    assert locked["identity_failed_attempts"] == 3 and locked["locked_at"]
    off = acc["trial-patient-10@example.com"]
    assert off["turned_off_at"] and str(off["turned_off_by"]) == env.user_id
    assert off["turned_off_at"] > off["activated_at"]
    # Active and turned-off accounts consented, to the current wording; the others did not.
    consented = {r["patient_id"]: r["n"] for r in env.conn.execute(
        "select patient_id, count(*) as n from consents group by patient_id").fetchall()}
    assert {str(a["patient_id"]) for a in acc.values() if a["status"] in ("active", "off")} == {
        str(k) for k in consented}
    assert set(consented.values()) == {2}
    assert {r["consent_text_version"] for r in env.conn.execute(
        "select consent_text_version from consents")} == {"2026-10-v1"}
    # Auth: a patient user for each account, banned only where access is off.
    for contact, a in acc.items():
        user = env.pg.gt.users[str(a["user_id"])]
        assert user["app_metadata"] == {"account_type": "patient"}
        assert user["banned"] is (a["status"] == "off")


def test_the_invited_patients_invite_is_in_the_demo_inbox_and_nothing_else_is_mailed(env):
    clinic_id, _ = run_seed(env)
    rows = env.conn.execute("select template, status, recipient from email_outbox").fetchall()
    assert [(r["template"], r["status"], r["recipient"]) for r in rows] == [
        ("invite", "demo", "trial-patient-11.demo@example.com")]


def test_a_non_demo_clinic_refuses_the_invite_to_a_reserved_domain(env):
    run_seed(env)
    env.conn.execute("update clinics set is_demo = false")
    seed._mail_invite(env.pg, clinic_id=str(env.conn.execute("select id from clinics limit 1"
                      ).fetchone()["id"]), patient_id=None, email="x@example.com")
    assert env.conn.execute("select status from email_outbox order by created_at desc limit 1"
                            ).fetchone()["status"] == "refused"
    n = env.conn.execute("select count(*) as n from email_outbox").fetchone()["n"]
    seed._mail_invite(env.pg, clinic_id=str(env.conn.execute("select id from clinics limit 1"
                      ).fetchone()["id"]), patient_id=None, email="real@gmail.com")
    assert env.conn.execute("select count(*) as n from email_outbox").fetchone()["n"] == n


def test_accounts_audit_and_reconcile_cleanly_and_the_chain_verifies(env):
    clinic_id, _ = run_seed(env)
    kinds = [r["event_type"] for r in audit.fetch_chain(env.pg, clinic_id)]
    assert kinds.count("patient_account.invited") == 6
    assert kinds.count("patient_account.activated") == 4  # three active, one turned off
    assert kinds.count("patient_account.identity_locked") == 1
    assert kinds.count("patient_account.access_turned_off") == 1
    assert kinds.count("consent.granted") == 8
    report = reconcile.reconcile(env.pg, clinic_id=clinic_id)
    assert report.ok and report.dangling == [], (report.unaudited, report.dangling)
    assert report.checked["patient_account.invited"] == 6


def test_rerunning_the_seed_gives_an_account_to_a_patient_seeded_before_accounts(env):
    clinic_id, _ = run_seed(env)
    before = counts(env.conn)
    # As a database seeded before migration 18 would be: patients, no emails, no accounts.
    env.conn.execute("delete from consents")
    env.conn.execute("delete from patient_sign_in_links")
    env.conn.execute("delete from patient_accounts")
    env.conn.execute("update patients set email = null")
    run_seed(env)
    after = counts(env.conn)
    assert after["patients"] == before["patients"] and after["episodes"] == before["episodes"]
    assert after["patient_accounts"] == 6 and after["consents"] == 8
    assert env.conn.execute("select count(*) as n from patients where email is null"
                            ).fetchone()["n"] == 0


def dev_env(env):
    """The shared dev clinic, with the clinician a member (the seed never adds that itself)."""
    env.conn.execute(
        "insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'owner') "
        "on conflict do nothing",
        (seed.SOURCE_CLINIC_ID, env.user_id),
    )
    return seed.ensure_dev_clinic(env.pg, env.user_id)


def seed_dev(env, active_email):
    clinic_id = dev_env(env)
    now = NOW
    plans = seed.build_plan(now, False, active_email)
    schedules = seed.load_schedules(env.pg, {1, 2, 3})
    seed.preflight(env.pg, plans, clinic_id)  # as main() does, before anything is written
    for plan in plans:
        seed.seed_patient(env.pg, clinic_id=clinic_id, user_id=env.user_id, plan=plan,
                          schedules=schedules, email_tag="dev")
    return clinic_id, plans


def test_active_email_adds_one_active_patient_under_the_real_address_in_the_dev_clinic(env):
    clinic_id, plans = seed_dev(env, "someone.real@gmail.com")
    assert len(plans) == 17 and plans[-1].label == "Trial Patient 18"  # 16, and the new one
    patient = env.conn.execute(
        "select p.id, p.email, a.status, a.user_id from patients p "
        "join patient_accounts a on a.patient_id = p.id where p.email = 'someone.real@gmail.com'"
    ).fetchone()
    assert patient["status"] == "active"
    user = env.pg.gt.users[str(patient["user_id"])]
    assert user["email"] == "someone.real@gmail.com" and not user["banned"]
    # Nobody else got a real address, and nothing was mailed to it (the seed sends no invite).
    others = env.conn.execute("select email from patients where email <> 'someone.real@gmail.com'"
                              ).fetchall()
    assert all(r["email"].endswith("@example.com") for r in others)
    assert env.conn.execute("select count(*) as n from email_outbox where recipient = "
                            "'someone.real@gmail.com'").fetchone()["n"] == 0
    # The dev clinic's own patients use their own addresses, not the demo clinic's.
    assert env.conn.execute("select count(*) as n from patients where email like '%.dev@example.com'"
                            ).fetchone()["n"] == 16
    report = reconcile.reconcile(env.pg, clinic_id=clinic_id)
    assert report.ok and report.dangling == []


def test_that_patient_can_really_use_the_api_once_signed_in(env):
    clinic_id, _ = seed_dev(env, "someone.real@gmail.com")
    gt = env.pg.gt
    uid = next(u for u, d in gt.users.items() if d["email"] == "someone.real@gmail.com")
    code = gt.admin.generate_link({"type": "magiclink", "email": "someone.real@gmail.com"}
                                  ).properties.email_otp
    token = gt.verify_otp({"email": "someone.real@gmail.com", "token": code, "type": "email"}
                          ).session.access_token
    client = TestClient(app)
    me = client.get("/patient/account", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["status"] == "active"
    assert uid


def test_the_seed_refuses_a_real_address_that_is_staff(env):
    with pytest.raises(SystemExit, match="clinic member"):
        seed_dev(env, "doc@example.com")  # the clinician's own Auth user
    # Refused before anything was written: no half-seeded patients.
    assert env.conn.execute("select count(*) as n from patients").fetchone()["n"] == 0


def test_the_seed_refuses_a_real_address_that_is_already_another_patients_live_account(env):
    seed_dev(env, "someone.real@gmail.com")
    other = env.conn.execute("select id from patients where email like 'trial-patient-1.dev%'"
                             ).fetchone()["id"]
    with pytest.raises(SystemExit, match="another patient"):
        seed._auth_user_for(env.pg, "someone.real@gmail.com", str(other))
    # ...but the same patient may be seeded again under it (a re-run).
    mine = env.conn.execute("select id from patients where email = 'someone.real@gmail.com'"
                            ).fetchone()["id"]
    assert seed._auth_user_for(env.pg, "someone.real@gmail.com", str(mine))


@pytest.mark.parametrize("args,message", [
    (["--active-email", "a@gmail.com"], "--clinic dev"),
    (["--clinic", "dev", "--active-email", "a@example.com"], "real address"),
    (["--clinic", "dev", "--active-email", "nope"], "real address"),
    (["--clinic", "dev", "--email", "me@gmail.com", "--active-email", "Me@Gmail.com"],
     "own address"),
])
def test_active_email_is_refused_in_the_wrong_places(args, message, capsys):
    with pytest.raises(SystemExit):
        seed.main(["--dry-run", *args])
    assert message in capsys.readouterr().err
