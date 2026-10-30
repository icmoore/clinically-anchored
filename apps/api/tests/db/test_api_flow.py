"""The api end to end against the real migrations: a clinic adds a patient, the patient
checks in and writes in their intervals, the clinic replies, discharges, and an untouched
episode runs out by itself. The clock is pinned (the database function guards use the real
one, so every pinned date is safely in the past), every write is really audited, and at the
end the audit chain verifies and `reconcile` finds nothing unaudited."""

import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient

from clinically_anchored_api.core import audit, clock, reconcile
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.accounts_env import patient_session
from tests.db.conftest import build_world
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install

pytestmark = pytest.mark.db

# A Tuesday. New patients get schedule v2 (days 2, 5, 10, 17, ...; the episode closes on day 59),
# and interval 2 opens on Sunday 9 March, the day the clocks went forward.
SURGERY = "2025-03-04"
OPENS = {  # interval opening instants (UTC) for SURGERY under schedule v2
    1: datetime(2025, 3, 6, 12, 0, tzinfo=UTC),  # day 2, 07:00 EST
    2: datetime(2025, 3, 9, 11, 0, tzinfo=UTC),  # day 5, 07:00 EDT (the clocks changed at 02:00)
    3: datetime(2025, 3, 14, 11, 0, tzinfo=UTC),  # day 10
    4: datetime(2025, 3, 21, 11, 0, tzinfo=UTC),  # day 17
}
END = datetime(2025, 5, 2, 11, 0, tzinfo=UTC)  # day 59, 07:00 EDT


@pytest.fixture
def env(db, monkeypatch):
    conn = db.conn
    w = build_world(conn)
    conn.execute("delete from patients")  # the walk-throughs add their own, through the api
    conn.execute("update clinics set office_phone = '905-555-0100' where id = %s", (w.clinic_a,))
    pg = PgSupabase(conn)
    install(monkeypatch, pg, audit=False)  # real audit writes
    settings = get_settings()
    seed = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(seed).decode())
    monkeypatch.setattr(settings, "audit_key_id", "test-1")
    monkeypatch.setattr(settings, "web_base_url", "https://app.example.com")
    monkeypatch.setattr(settings, "patient_reads_per_minute", 0)  # throttling: test_ratelimit
    monkeypatch.setattr(settings, "patient_writes_per_minute", 0)

    now = [datetime(2025, 3, 5, 17, 0, tzinfo=UTC)]
    monkeypatch.setattr(clock, "now", lambda: now[0])

    class Env:
        pass

    e = Env()
    e.w, e.pg, e.now, e.client = w, pg, now, TestClient(app)
    e.auth = {"Authorization": f"Bearer user:{w.user_a}"}
    e.clinic = f"/clinics/{w.clinic_a}"
    return e


def at(env, instant):
    env.now[0] = instant


def add_patient(env, name="Pat"):
    r = env.client.post(
        f"{env.clinic}/patients", headers=env.auth,
        json={"full_name": name, "surgery_date": SURGERY, "procedure_id": env.w.proc_a},
    )
    assert r.status_code == 200, r.text
    return r.json()


def session_for_episode(env, episode_id):
    """The headers of a signed-in patient: an active account for the episode's patient."""
    patient = env.pg.conn.execute("select patient_id from episodes where id = %s", (episode_id,)
                                  ).fetchone()["patient_id"]
    return patient_session(env.pg, env.w, str(patient))


def test_the_whole_story_and_the_audit_trail_that_comes_out_of_it(env):
    c, auth = env.client, env.auth

    # The clinic adds the patient: an episode is created with them.
    patient = add_patient(env)
    ep = patient["episode"]
    assert ep["state"]["phase"] == "locked_checkin" and ep["state"]["post_op_day"] == 1
    token = session_for_episode(env, ep["id"])

    # Locked: the patient sees why and who to call, and can't write.
    state = c.get("/episode", headers=token).json()
    assert state["phase"] == "locked_checkin" and state["office_phone"] == "905-555-0100"
    assert c.post("/messages", headers=token,
                  json={"body": "hi", "category": "admin"}).status_code == 403
    assert c.post("/messages", headers=token,
                  json={"body": "hi", "kind": "reply"}).status_code == 409

    # The check-in (the procedure is the clinic's; the day is worked out).
    ctx = c.get("/check-ins/context", headers=token).json()
    assert ctx["procedure"]["id"] == env.w.proc_a and ctx["post_op_day"] == 1
    r = c.post("/check-ins", headers=token, json={"answers": {"concerns_text": "all good"}})
    assert r.status_code == 200 and r.json()["post_op_day"] == 1
    assert c.post("/check-ins", headers=token, json={"answers": {}}).status_code == 409
    mine = c.get("/check-ins/mine", headers=token).json()  # read back to the patient
    assert mine["answers"] == {"concerns_text": "all good"} and mine["post_op_day"] == 1
    assert mine["procedure_name"] and "is_red_flag" not in mine
    state = c.get("/episode", headers=token).json()
    assert state["phase"] == "waiting" and state["next_opens_at"].startswith("2025-03-06T12:00")
    assert c.post("/messages", headers=token,
                  json={"body": "hi", "category": "admin"}).status_code == 409

    # Interval 1 opens at 07:00: one message, then used.
    at(env, OPENS[1])
    assert c.get("/episode", headers=token).json()["phase"] == "open"
    r = c.post("/messages", headers=token, json={"body": "Feeling fine", "category": "admin"})
    assert r.status_code == 200 and (r.json()["interval_no"], r.json()["origin"]) == (1, "scheduled")
    again = c.post("/messages", headers=token, json={"body": "More", "category": "admin"})
    assert again.status_code == 409
    state = c.get("/episode", headers=token).json()
    assert state["phase"] == "used" and state["next_opens_at"].startswith("2025-03-09T11:00")

    # A doctor's message lets the patient reply once, outside the interval.
    sent = c.post(f"{env.clinic}/patients/{patient['id']}/messages", headers=auth,
                  json={"body": "Glad to hear it"})
    assert sent.status_code == 200
    assert c.get("/episode", headers=token).json()["can_reply"] is True
    reply = c.post("/messages", headers=token, json={"body": "Thanks", "kind": "reply"})
    assert reply.status_code == 200 and reply.json()["reply_to_message_id"] == sent.json()["id"]
    assert c.post("/messages", headers=token,
                  json={"body": "And", "kind": "reply"}).status_code == 409

    # The clinic sees all three, with what each was.
    thread = c.get(f"{env.clinic}/patients/{patient['id']}/messages", headers=auth).json()
    assert [(m["sender"], m["origin"], m["interval_no"], m["category"]) for m in thread] == [
        ("patient", "scheduled", 1, "admin"),
        ("clinician", "clinician", None, None),
        ("patient", "reply", None, None),
    ]
    first = thread[0]
    assert c.post(f"{env.clinic}/messages/{first['id']}/read", headers=auth).status_code == 200

    # Interval 2 opens the morning the clocks go forward: 06:59:59 is still interval 1.
    at(env, datetime(2025, 3, 9, 10, 59, 59, tzinfo=UTC))
    state = c.get("/episode", headers=token).json()
    assert state["interval_no"] == 1 and state["phase"] == "used"
    at(env, OPENS[2])
    assert c.get("/episode", headers=token).json()["interval_no"] == 2
    assert c.post("/messages", headers=token,
                  json={"body": "Day five", "category": "medication"}).json()["interval_no"] == 2
    at(env, OPENS[3])
    assert c.get("/episode", headers=token).json()["interval_no"] == 3

    # Interval 3 goes unused; later the clinic sees it as missed.
    at(env, datetime(2025, 3, 24, 15, 0, tzinfo=UTC))  # interval 4 (from Mar 21) is open
    clinic_view = c.get(f"{env.clinic}/patients/{patient['id']}/episode", headers=auth).json()
    assert clinic_view["state"]["missed"] == [3] and clinic_view["state"]["interval_no"] == 4
    assert clinic_view["schedule_version"] == 2 and clinic_view["state"]["intervals_total"] == 9

    # Turning access off blocks the patient at once; a new session after re-activation works.
    patient_id = patient["id"]
    off = c.post(f"{env.clinic}/patients/{patient_id}/account/turn-off", headers=auth)
    assert off.status_code == 200 and off.json()["status"] == "off"
    assert c.get("/messages", headers=token).status_code == 403
    # Re-inviting lifts the ban; the patient then does the first sign-in again (done here in SQL).
    assert c.post(f"{env.clinic}/patients/{patient_id}/account/reinvite", headers=auth
                  ).status_code == 200
    env.pg.conn.execute("update patient_accounts set status = 'active', "
                        "identity_verified_at = now() where patient_id = %s", (patient_id,))
    token = session_for_episode(env, ep["id"])
    assert c.get("/messages", headers=token).status_code == 200

    # Discharge: the patient's routes close, the clinic can't write either.
    assert c.post(f"{env.clinic}/episodes/{ep['id']}/close", headers=auth).status_code == 200
    assert c.get("/messages", headers=token).status_code == 410
    assert c.post("/messages", headers=token,
                  json={"body": "x", "category": "admin"}).status_code == 410
    closed = c.get("/episode", headers=token).json()
    assert closed["phase"] == "closed" and closed["office_phone"] == "905-555-0100"
    assert closed["next_opens_at"] is None and closed["categories"] == []
    assert c.post(f"{env.clinic}/patients/{patient['id']}/messages", headers=auth,
                  json={"body": "hello?"}).status_code == 409
    assert c.post(f"{env.clinic}/episodes/{ep['id']}/close", headers=auth).status_code == 409

    # The audit trail: every write above is there, and the chain verifies.
    rows = audit.fetch_chain(env.pg, env.w.clinic_a)
    kinds = [r["event_type"] for r in rows]
    for expected in ("patient.created", "episode.created", "patient_account.invited",
                     "patient_account.access_turned_off", "check_in.submitted",
                     "message.sent", "message.read", "episode.closed"):
        assert expected in kinds, expected
    assert "link.issued" not in kinds and "episode.link_rotated" not in kinds  # retired
    assert kinds.count("message.sent") == 4  # 3 patient + 1 clinician, nothing from refusals
    chain = audit.verify_chain(rows, audit.trusted_public_keys())
    assert chain["ok"], chain
    sent_events = [r for r in rows if r["event_type"] == "message.sent"]
    assert {r["metadata"]["origin"] for r in sent_events} == {"scheduled", "clinician", "reply"}
    assert any(r["metadata"]["category"] == "medication" for r in sent_events)
    report = reconcile.reconcile(env.pg)
    assert report.ok, report.unaudited


def test_an_episode_that_runs_out_closes_itself_once_and_a_new_surgery_is_then_allowed(env):
    c, auth = env.client, env.auth
    patient = add_patient(env, "Pat Run Out")
    ep = patient["episode"]
    token = session_for_episode(env, ep["id"])
    c.post("/check-ins", headers=token, json={"answers": {}})

    at(env, END - timedelta(seconds=1))  # one second before 07:00 EDT on day 59
    assert c.get("/messages", headers=token).status_code == 200
    at(env, END)  # surgery + 59 days at 07:00
    assert c.get("/messages", headers=token).status_code == 410
    assert c.get("/messages", headers=token).status_code == 410

    row = env.pg.conn.execute("select * from episodes where id = %s", (ep["id"],)).fetchone()
    assert row["close_reason"] == "schedule_complete" and row["closed_by"] is None
    assert row["closed_at"] == END  # the instant it ended
    closes = [r for r in audit.fetch_chain(env.pg, env.w.clinic_a) if r["event_type"] == "episode.closed"]
    assert len(closes) == 1 and closes[0]["metadata"]["actor"] == {"type": "system"}

    # A new surgery for the same patient: the database allows it now.
    at(env, datetime(2025, 5, 10, 15, 0, tzinfo=UTC))
    r = c.post(f"{env.clinic}/patients/{patient['id']}/episodes", headers=auth,
               json={"surgery_date": "2025-05-08", "procedure_id": env.w.proc_a})
    assert r.status_code == 200 and r.json()["state"]["phase"] == "locked_checkin"
    assert reconcile.reconcile(env.pg).ok


def test_a_late_check_in_leaves_the_passed_intervals_missed_in_the_audit_event(env):
    patient = add_patient(env, "Pat Late")
    token = session_for_episode(env, patient["episode"]["id"])
    at(env, datetime(2025, 3, 12, 15, 0, tzinfo=UTC))  # interval 1 passed, 2 is open
    r = env.client.post("/check-ins", headers=token, json={"answers": {}})
    assert r.status_code == 200
    state = env.client.get("/episode", headers=token).json()
    assert state["phase"] == "open" and state["interval_no"] == 2  # this one is still usable
    event = [r for r in audit.fetch_chain(env.pg, env.w.clinic_a)
             if r["event_type"] == "check_in.submitted"][0]
    assert event["metadata"]["episode_id"] == patient["episode"]["id"]
    clinic_view = env.client.get(
        f"{env.clinic}/patients/{patient['id']}/episode", headers=env.auth
    ).json()
    assert clinic_view["state"]["missed"] == [1]


def test_the_correction_window_closes_at_the_check_in(env):
    patient = add_patient(env, "Pat Typo")
    ep = patient["episode"]["id"]
    r = env.client.patch(f"{env.clinic}/episodes/{ep}", headers=env.auth,
                         json={"surgery_date": "2025-03-05"})
    assert r.status_code == 200 and r.json()["surgery_date"] == "2025-03-05"
    token = session_for_episode(env, ep)
    env.client.post("/check-ins", headers=token, json={"answers": {}})
    r = env.client.patch(f"{env.clinic}/episodes/{ep}", headers=env.auth,
                         json={"surgery_date": "2025-03-06"})
    assert r.status_code == 409 and "start a new one" in r.json()["detail"]


def test_a_delegate_cannot_discharge_and_another_clinic_cannot_see_the_episode(env):
    patient = add_patient(env, "Pat Roles")
    ep = patient["episode"]["id"]
    env.pg.conn.execute("update clinic_members set role = 'delegate' where user_id = %s", (env.w.user_a,))
    assert env.client.post(f"{env.clinic}/episodes/{ep}/close", headers=env.auth).status_code == 403
    other = {"Authorization": f"Bearer user:{env.w.user_b}"}
    r = env.client.post(f"/clinics/{env.w.clinic_b}/episodes/{ep}/link", headers=other)
    assert r.status_code == 404


def test_new_patients_get_v2_and_an_older_episode_keeps_v1(env):
    c, auth = env.client, env.auth
    conn = env.pg.conn
    # An episode made before v2 existed: recorded schedule v1.
    old_patient = conn.execute(
        "insert into patients (clinic_id, full_name) values (%s, 'Old Pat') returning id",
        (env.w.clinic_a,),
    ).fetchone()["id"]
    conn.execute(
        "insert into episodes (clinic_id, patient_id, procedure_id, surgery_date, schedule_version, "
        "created_by) values (%s, %s, %s, %s, 1, %s)",
        (env.w.clinic_a, old_patient, env.w.proc_a, SURGERY, env.w.user_a),
    )
    new = add_patient(env, "New Pat")
    assert new["episode"]["schedule_version"] == 2

    at(env, datetime(2025, 3, 12, 15, 0, tzinfo=UTC))  # day 8
    listed = {p["full_name"]: p["episode"] for p in
              c.get(f"{env.clinic}/patients?status=all", headers=auth).json()["items"]}
    old, now_ = listed["Old Pat"], listed["New Pat"]
    assert (old["schedule_version"], now_["schedule_version"]) == (1, 2)
    assert old["state"]["interval_no"] == 4  # v1: window 4 (days 7-14) is open on day 8
    assert now_["state"]["interval_no"] == 2  # v2: window 2 (days 5-10)
    assert old["state"]["episode_closes_at"].startswith("2025-04-22")  # v1: day 49
    assert now_["state"]["episode_closes_at"].startswith("2025-05-02")  # v2: day 59


def test_retiring_the_default_version_changes_only_what_new_patients_get(env):
    conn = env.pg.conn
    first = add_patient(env, "Pat One")
    conn.execute("update message_schedules set active = false where version = 2")  # retire v2
    second = add_patient(env, "Pat Two")
    conn.execute(
        "insert into message_schedules (version, offsets_days, close_after_days, opens_at_local, "
        "timezone, active) values (7, '{2,4}', 7, '07:00', 'America/Toronto', false)"
    )  # staged, not yet the default
    third = add_patient(env, "Pat Three")
    conn.execute("update message_schedules set active = true where version = 7")
    fourth = add_patient(env, "Pat Four")
    assert [p["episode"]["schedule_version"] for p in (first, second, third, fourth)] == [2, 1, 1, 7]
    # Pat One still computes with v2 after v2 was retired: the version is theirs.
    now = env.client.get(f"{env.clinic}/patients/{first['id']}/episode", headers=env.auth).json()
    assert now["schedule_version"] == 2 and now["state"]["intervals_total"] == 9
