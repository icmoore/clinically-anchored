"""The patient's check-in: link verification, red-flag evaluation, and what is sent to the
database (the post-op day comes from the surgery date; the procedure from the episode).
The write itself is the database function `complete_check_in` (tests/db)."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.main import app
from tests.fakes import (
    CLINIC_A,
    EPISODE_A,
    PATIENT_A,
    PROC_A,
    FakeSupabase,
    install,
    pin_clock,
    token_for,
)

FEVER = {"fever": {"took_temperature": True, "temperature_c": 39.0}}


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSupabase()
    fake.add_episode()
    pin_clock(monkeypatch)
    fake.audit = install(monkeypatch, fake)

    def complete(params):
        row = {
            "id": "c1", "clinic_id": params["p_clinic_id"], "patient_id": PATIENT_A,
            "episode_id": params["p_episode_id"], "procedure_id": PROC_A,
            "post_op_day": params["p_post_op_day"], "answers": params["p_answers"],
            "is_red_flag": params["p_is_red_flag"], "created_at": "2026-10-03T11:30:00+00:00",
        }
        return {"check_in": row, "episode": {}}

    fake.rpc_handlers["complete_check_in"] = complete
    return fake


@pytest.fixture
def client(fake):
    return TestClient(app)


def tok(fake):
    return {"token": token_for(fake.tables["episodes"][0])}


def test_submit_flags_red_flag_and_audits_with_episode_and_missed_intervals(client, fake):
    r = client.post("/check-ins", params=tok(fake), json={"answers": FEVER})
    assert r.status_code == 200
    body = r.json()
    assert (body["clinic_id"], body["patient_id"], body["episode_id"]) == (
        CLINIC_A, PATIENT_A, EPISODE_A,
    )
    assert body["is_red_flag"] is True
    (event,) = fake.audit
    assert event["event_type"] == "check_in.submitted"
    assert event["metadata"]["actor"] == {"type": "patient", "id": PATIENT_A}
    assert event["metadata"]["episode_id"] == EPISODE_A
    assert event["payload"]["missed_intervals"] == []  # NOW is inside interval 1: nothing passed
    assert "answers" not in str(event["metadata"])


def test_no_red_flag(client, fake):
    r = client.post(
        "/check-ins", params=tok(fake),
        json={"answers": {"fever": {"took_temperature": True, "temperature_c": 37.0}}},
    )
    assert r.status_code == 200 and r.json()["is_red_flag"] is False


def test_the_post_op_day_is_worked_out_not_taken_from_the_patient(client, fake):
    # NOW is 2026-10-03 in Toronto; surgery 2026-10-01 -> day 2, whatever the client claims.
    r = client.post("/check-ins", params=tok(fake), json={"answers": {}, "post_op_day": 99})
    assert r.status_code == 200 and r.json()["post_op_day"] == 2
    (_, params), = fake.rpc_calls
    assert params["p_post_op_day"] == 2
    assert params["p_clinic_id"] == CLINIC_A and params["p_episode_id"] == EPISODE_A


def test_a_late_check_in_records_the_intervals_that_already_passed(client, fake, monkeypatch):
    from datetime import UTC, datetime

    pin_clock(monkeypatch, datetime(2026, 10, 15, 14, 0, tzinfo=UTC))  # interval 5 is open
    client.post("/check-ins", params=tok(fake), json={"answers": {}})
    assert fake.audit[0]["payload"]["missed_intervals"] == [1, 2, 3, 4]


def test_a_second_check_in_is_refused(client, fake):
    def already(_params):
        raise RuntimeError('{"message": "check_in_already_done"}')

    fake.rpc_handlers["complete_check_in"] = already
    r = client.post("/check-ins", params=tok(fake), json={"answers": {}})
    assert r.status_code == 409
    assert fake.audit == []


def test_a_discharge_between_check_and_write_is_a_410(client, fake):
    def closed(_params):
        raise RuntimeError("episode_closed")

    fake.rpc_handlers["complete_check_in"] = closed
    assert client.post("/check-ins", params=tok(fake), json={"answers": {}}).status_code == 410


def test_a_failed_audit_is_reported_not_hidden(client, fake, monkeypatch):
    from clinically_anchored_api.api import check_ins as module
    from clinically_anchored_api.core.audit import AuditWriteError

    def boom(_db, **_kw):
        raise AuditWriteError("down")

    monkeypatch.setattr(module, "record_event", boom)
    r = client.post("/check-ins", params=tok(fake), json={"answers": {}})
    assert r.status_code == 500 and "not audited" in r.json()["detail"]


def test_bad_token_is_rejected(client):
    r = client.post("/check-ins", params={"token": "nope"}, json={"answers": {}})
    assert r.status_code == 401
    assert client.get("/check-ins/context", params={"token": "nope"}).status_code == 401


def test_context_gives_the_procedure_the_clinic_chose(client, fake):
    r = client.get("/check-ins/context", params=tok(fake))
    assert r.status_code == 200
    body = r.json()
    assert body["clinic_id"] == CLINIC_A
    assert body["procedure"] == {
        "id": PROC_A, "name": "Colectomy", "category": "abdominal_bowel_resection",
    }
    assert body["post_op_day"] == 2 and body["check_in_done"] is False


def test_context_says_when_the_check_in_is_already_done(client, fake):
    fake.tables["episodes"][0]["checkin_completed_at"] = "2026-10-02T12:00:00+00:00"
    assert client.get("/check-ins/context", params=tok(fake)).json()["check_in_done"] is True


def test_context_is_before_surgery_negative(client, fake, monkeypatch):
    from datetime import UTC, datetime

    pin_clock(monkeypatch, datetime(2026, 9, 29, 16, 0, tzinfo=UTC))
    assert client.get("/check-ins/context", params=tok(fake)).json()["post_op_day"] == -2


def test_nothing_is_reachable_on_a_closed_episode(client, fake):
    fake.tables["episodes"][0].update(
        closed_at="2026-10-02T13:00:00+00:00", close_reason="discharged", closed_by="u"
    )
    assert client.get("/check-ins/context", params=tok(fake)).status_code == 410
    assert client.post("/check-ins", params=tok(fake), json={"answers": {}}).status_code == 410


# --- the patient reads their own check-in back ---------------------------------------------


def _stored_check_in(fake, **extra):
    fake.tables["check_ins"].append({
        "id": "c1", "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "episode_id": EPISODE_A,
        "procedure_id": PROC_A, "post_op_day": 3, "is_red_flag": True,
        "reviewed_at": "2026-10-04T00:00:00+00:00", "reviewed_by": "someone",
        "answers": {"symptoms": ["pain"], "concerns_text": "It stings at night."},
        "created_at": "2026-10-03T11:30:00+00:00", **extra,
    })


def test_my_check_in_is_null_until_they_have_checked_in(client, fake):
    r = client.get("/check-ins/mine", params=tok(fake))
    assert r.status_code == 200 and r.json() is None


def test_my_check_in_returns_what_they_entered_and_nothing_the_clinic_derived(client, fake):
    _stored_check_in(fake)
    body = client.get("/check-ins/mine", params=tok(fake)).json()
    assert body == {
        "submitted_at": "2026-10-03T11:30:00+00:00", "post_op_day": 3,
        "procedure_name": "Colectomy",
        "answers": {"symptoms": ["pain"], "concerns_text": "It stings at night."},
    }
    assert "is_red_flag" not in body and "reviewed_at" not in body


def test_my_check_in_is_only_this_episodes(client, fake):
    _stored_check_in(fake, episode_id="someone-elses", patient_id="p2", id="c2")
    assert client.get("/check-ins/mine", params=tok(fake)).json() is None


def test_my_check_in_needs_a_valid_open_link(client, fake):
    assert client.get("/check-ins/mine", params={"token": "nope"}).status_code == 401
    fake.tables["episodes"][0].update(
        closed_at="2026-10-02T13:00:00+00:00", close_reason="discharged", closed_by="u"
    )
    assert client.get("/check-ins/mine", params=tok(fake)).status_code == 410
