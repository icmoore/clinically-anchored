"""The api end to end against the real migrations: a clinic adds a patient, the patient
checks in and writes in their intervals, the clinic replies, discharges, and an untouched
episode runs out by itself. The clock is pinned (the database function guards use the real
one, so every pinned date is safely in the past), every write is really audited, and at the
end the audit chain verifies and `reconcile` finds nothing unaudited."""

import base64
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient

from clinically_anchored_api.core import audit, clock, reconcile
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.conftest import build_world
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install

pytestmark = pytest.mark.db

SURGERY = "2025-03-04"  # a Tuesday; the clocks go forward on Sunday 9 March, inside interval 3
TORONTO_0700 = {  # interval opening instants (UTC) for SURGERY under schedule v1
    1: datetime(2025, 3, 6, 12, 0, tzinfo=UTC),  # 07:00 EST
    2: datetime(2025, 3, 7, 12, 0, tzinfo=UTC),
    3: datetime(2025, 3, 9, 11, 0, tzinfo=UTC),  # 07:00 EDT, the day the clocks went forward
}


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
    monkeypatch.setattr(settings, "patient_link_reads_per_minute", 0)  # throttling: test_ratelimit
    monkeypatch.setattr(settings, "patient_link_writes_per_minute", 0)

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


def link_token(env, episode_id):
    r = env.client.post(f"{env.clinic}/episodes/{episode_id}/link", headers=env.auth)
    assert r.status_code == 200, r.text
    return r.json()["url"].split("token=")[1]


def tp(token):
    return {"token": token}


def test_the_whole_story_and_the_audit_trail_that_comes_out_of_it(env):
    c, auth = env.client, env.auth

    # The clinic adds the patient: an episode is created with them.
    patient = add_patient(env)
    ep = patient["episode"]
    assert ep["state"]["phase"] == "locked_checkin" and ep["state"]["post_op_day"] == 1
    token = link_token(env, ep["id"])

    # Locked: the patient sees why and who to call, and can't write.
    state = c.get("/episode", params=tp(token)).json()
    assert state["phase"] == "locked_checkin" and state["office_phone"] == "905-555-0100"
    assert c.post("/messages", params=tp(token),
                  json={"body": "hi", "category": "admin"}).status_code == 403
    assert c.post("/messages", params=tp(token),
                  json={"body": "hi", "kind": "reply"}).status_code == 409

    # The check-in (the procedure is the clinic's; the day is worked out).
    ctx = c.get("/check-ins/context", params=tp(token)).json()
    assert ctx["procedure"]["id"] == env.w.proc_a and ctx["post_op_day"] == 1
    r = c.post("/check-ins", params=tp(token), json={"answers": {"concerns_text": "all good"}})
    assert r.status_code == 200 and r.json()["post_op_day"] == 1
    assert c.post("/check-ins", params=tp(token), json={"answers": {}}).status_code == 409
    state = c.get("/episode", params=tp(token)).json()
    assert state["phase"] == "waiting" and state["next_opens_at"].startswith("2025-03-06T12:00")
    assert c.post("/messages", params=tp(token),
                  json={"body": "hi", "category": "admin"}).status_code == 409

    # Interval 1 opens at 07:00: one message, then used.
    at(env, TORONTO_0700[1])
    assert c.get("/episode", params=tp(token)).json()["phase"] == "open"
    r = c.post("/messages", params=tp(token), json={"body": "Feeling fine", "category": "admin"})
    assert r.status_code == 200 and (r.json()["interval_no"], r.json()["origin"]) == (1, "scheduled")
    again = c.post("/messages", params=tp(token), json={"body": "More", "category": "admin"})
    assert again.status_code == 409
    state = c.get("/episode", params=tp(token)).json()
    assert state["phase"] == "used" and state["next_opens_at"].startswith("2025-03-07T12:00")

    # A doctor's message lets the patient reply once, outside the interval.
    sent = c.post(f"{env.clinic}/patients/{patient['id']}/messages", headers=auth,
                  json={"body": "Glad to hear it"})
    assert sent.status_code == 200
    assert c.get("/episode", params=tp(token)).json()["can_reply"] is True
    reply = c.post("/messages", params=tp(token), json={"body": "Thanks", "kind": "reply"})
    assert reply.status_code == 200 and reply.json()["reply_to_message_id"] == sent.json()["id"]
    assert c.post("/messages", params=tp(token),
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

    # Interval 2 opens; interval 3 opens the morning the clocks go forward.
    at(env, TORONTO_0700[2])
    assert c.post("/messages", params=tp(token),
                  json={"body": "Day two", "category": "medication"}).json()["interval_no"] == 2
    at(env, datetime(2025, 3, 9, 10, 59, 59, tzinfo=UTC))  # 06:59:59 EST: not yet 07:00
    assert c.get("/episode", params=tp(token)).json()["interval_no"] == 2
    at(env, TORONTO_0700[3])
    assert c.get("/episode", params=tp(token)).json()["interval_no"] == 3

    # Interval 3 goes unused; later the clinic sees it as missed.
    at(env, datetime(2025, 3, 12, 15, 0, tzinfo=UTC))  # interval 4 (Mar 11) is open
    clinic_view = c.get(f"{env.clinic}/patients/{patient['id']}/episode", headers=auth).json()
    assert clinic_view["state"]["missed"] == [3] and clinic_view["state"]["interval_no"] == 4

    # Resetting the link kills the old URL at once.
    new = c.post(f"{env.clinic}/episodes/{ep['id']}/link/reset", headers=auth).json()
    assert c.get("/messages", params=tp(token)).status_code == 401
    token = new["url"].split("token=")[1]
    assert c.get("/messages", params=tp(token)).status_code == 200

    # Discharge: the link stops working, the clinic can't write either.
    assert c.post(f"{env.clinic}/episodes/{ep['id']}/close", headers=auth).status_code == 200
    assert c.get("/messages", params=tp(token)).status_code == 410
    assert c.post("/messages", params=tp(token),
                  json={"body": "x", "category": "admin"}).status_code == 410
    closed = c.get("/episode", params=tp(token)).json()
    assert closed["phase"] == "closed" and closed["office_phone"] == "905-555-0100"
    assert closed["next_opens_at"] is None and closed["categories"] == []
    assert c.post(f"{env.clinic}/patients/{patient['id']}/messages", headers=auth,
                  json={"body": "hello?"}).status_code == 409
    assert c.post(f"{env.clinic}/episodes/{ep['id']}/close", headers=auth).status_code == 409

    # The audit trail: every write above is there, and the chain verifies.
    rows = audit.fetch_chain(env.pg, env.w.clinic_a)
    kinds = [r["event_type"] for r in rows]
    for expected in ("patient.created", "episode.created", "link.issued", "check_in.submitted",
                     "message.sent", "message.read", "episode.link_rotated", "episode.closed"):
        assert expected in kinds, expected
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
    token = link_token(env, ep["id"])
    c.post("/check-ins", params=tp(token), json={"answers": {}})

    at(env, datetime(2025, 4, 22, 10, 59, 59, tzinfo=UTC))  # one second before 07:00 EDT
    assert c.get("/messages", params=tp(token)).status_code == 200
    at(env, datetime(2025, 4, 22, 11, 0, tzinfo=UTC))  # surgery + 49 days at 07:00
    assert c.get("/messages", params=tp(token)).status_code == 410
    assert c.get("/messages", params=tp(token)).status_code == 410

    row = env.pg.conn.execute("select * from episodes where id = %s", (ep["id"],)).fetchone()
    assert row["close_reason"] == "schedule_complete" and row["closed_by"] is None
    assert row["closed_at"] == datetime(2025, 4, 22, 11, 0, tzinfo=UTC)  # the instant it ended
    closes = [r for r in audit.fetch_chain(env.pg, env.w.clinic_a) if r["event_type"] == "episode.closed"]
    assert len(closes) == 1 and closes[0]["metadata"]["actor"] == {"type": "system"}

    # A new surgery for the same patient: the database allows it now.
    at(env, datetime(2025, 5, 1, 15, 0, tzinfo=UTC))
    r = c.post(f"{env.clinic}/patients/{patient['id']}/episodes", headers=auth,
               json={"surgery_date": "2025-04-29", "procedure_id": env.w.proc_a})
    assert r.status_code == 200 and r.json()["state"]["phase"] == "locked_checkin"
    assert reconcile.reconcile(env.pg).ok


def test_a_late_check_in_leaves_the_passed_intervals_missed_in_the_audit_event(env):
    patient = add_patient(env, "Pat Late")
    token = link_token(env, patient["episode"]["id"])
    at(env, datetime(2025, 3, 12, 15, 0, tzinfo=UTC))  # intervals 1-3 passed, 4 is open
    r = env.client.post("/check-ins", params=tp(token), json={"answers": {}})
    assert r.status_code == 200
    state = env.client.get("/episode", params=tp(token)).json()
    assert state["phase"] == "open" and state["interval_no"] == 4  # this one is still usable
    event = [r for r in audit.fetch_chain(env.pg, env.w.clinic_a)
             if r["event_type"] == "check_in.submitted"][0]
    assert event["metadata"]["episode_id"] == patient["episode"]["id"]
    clinic_view = env.client.get(
        f"{env.clinic}/patients/{patient['id']}/episode", headers=env.auth
    ).json()
    assert clinic_view["state"]["missed"] == [1, 2, 3]


def test_the_correction_window_closes_at_the_check_in(env):
    patient = add_patient(env, "Pat Typo")
    ep = patient["episode"]["id"]
    r = env.client.patch(f"{env.clinic}/episodes/{ep}", headers=env.auth,
                         json={"surgery_date": "2025-03-05"})
    assert r.status_code == 200 and r.json()["surgery_date"] == "2025-03-05"
    token = link_token(env, ep)
    env.client.post("/check-ins", params=tp(token), json={"answers": {}})
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
