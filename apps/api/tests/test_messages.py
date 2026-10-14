"""Message endpoints: auth and tenant scoping, what the api asks the database to write, and
how the database's refusals are reported. Which interval is open comes from the engine
(test_schedule.py); the write rules themselves are tested against Postgres in tests/db."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import messages as messages_module
from clinically_anchored_api.main import app
from tests.fakes import (
    AUTH,
    CLINIC_A,
    CLINIC_B,
    EPISODE_A,
    PATIENT_A,
    PATIENT_B,
    USER,
    FakeSupabase,
    install,
    message_row,
    pin_clock,
    token_for,
)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSupabase()
    fake.add_episode(checkin_completed_at="2026-10-02T12:00:00+00:00")
    pin_clock(monkeypatch)  # Saturday 3 Oct 07:30 Toronto: interval 1 is open
    fake.audit = install(monkeypatch, fake)
    monkeypatch.setattr(messages_module, "log_message_sent", lambda *_a, **_k: None)

    def store(row_extra, params):
        row = message_row(
            clinic_id=params["p_clinic_id"], episode_id=params["p_episode_id"],
            body=params["p_body"], **row_extra,
        )
        fake.tables["messages"].append(row)
        return row

    fake.rpc_handlers["send_clinician_message"] = lambda p: store(
        {"sender": "clinician", "origin": "clinician"}, p
    )
    fake.rpc_handlers["send_patient_message"] = lambda p: store(
        {"sender": "patient", "origin": p["p_origin"], "interval_no": p["p_interval_no"],
         "category": p["p_category"]}, p
    )
    return fake


@pytest.fixture
def client(fake):
    return TestClient(app)


def _thread(clinic, patient):
    return f"/clinics/{clinic}/patients/{patient}/messages"


def tok(fake):
    return {"token": token_for(fake.tables["episodes"][0])}


def fail_with(fake, rpc, text):
    def boom(_params):
        raise RuntimeError(text)

    fake.rpc_handlers[rpc] = boom


# --- clinician side ------------------------------------------------------------------


def test_requires_bearer_token(client):
    assert client.get(_thread(CLINIC_A, PATIENT_A)).status_code == 401


def test_rejects_invalid_jwt(client):
    r = client.get(_thread(CLINIC_A, PATIENT_A), headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_rejects_non_member_of_clinic(client):
    assert client.get(_thread(CLINIC_B, PATIENT_B), headers=AUTH).status_code == 403


def test_patient_from_another_clinic_is_404(client):
    assert client.get(_thread(CLINIC_A, PATIENT_B), headers=AUTH).status_code == 404
    r = client.post(_thread(CLINIC_A, PATIENT_B), headers=AUTH, json={"body": "hi"})
    assert r.status_code == 404


def test_send_goes_through_the_database_function_into_the_open_episode(client, fake):
    r = client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": "How is the pain?"})
    assert r.status_code == 200
    assert (r.json()["sender"], r.json()["origin"]) == ("clinician", "clinician")
    assert fake.rpc_calls == [
        ("send_clinician_message",
         {"p_clinic_id": CLINIC_A, "p_episode_id": EPISODE_A, "p_body": "How is the pain?"})
    ]
    thread = client.get(_thread(CLINIC_A, PATIENT_A), headers=AUTH).json()
    assert [m["body"] for m in thread] == ["How is the pain?"]


def test_send_audits_without_text_in_metadata_but_with_origin_and_episode(client, fake):
    client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": "secret words"})
    (call,) = fake.audit
    assert call["event_type"] == "message.sent"
    assert call["payload"]["body"] == "secret words"  # hashed by record_event, never stored
    assert call["payload"]["episode_id"] == EPISODE_A
    assert "secret words" not in str(call["metadata"])
    assert call["metadata"]["actor"] == {"type": "member", "id": USER, "role": "clinician"}
    assert (call["metadata"]["origin"], call["metadata"]["episode_id"]) == ("clinician", EPISODE_A)


def test_send_ignores_client_supplied_sender_and_episode(client, fake):
    client.post(
        _thread(CLINIC_A, PATIENT_A), headers=AUTH,
        json={"body": "x", "sender": "patient", "episode_id": "other", "origin": "reply"},
    )
    assert fake.rpc_calls[0][1]["p_episode_id"] == EPISODE_A
    assert fake.tables["messages"][0]["sender"] == "clinician"


@pytest.mark.parametrize("body", ["", "   ", "x" * 5001])
def test_send_rejects_empty_or_oversized_body(client, body):
    r = client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": body})
    assert r.status_code == 422


def test_no_message_can_go_to_a_patient_whose_episode_is_closed(client, fake):
    fake.tables["episodes"][0].update(
        closed_at="2026-10-02T13:00:00+00:00", close_reason="discharged", closed_by=USER
    )
    r = client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": "hello?"})
    assert r.status_code == 409 and "no open care period" in r.json()["detail"]
    assert fake.rpc_calls == []


def test_no_message_can_go_to_a_patient_without_an_episode(client, fake):
    fake.tables["episodes"].clear()
    r = client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": "x"})
    assert r.status_code == 409


def test_a_discharge_between_the_check_and_the_write_is_a_409(client, fake):
    fail_with(fake, "send_clinician_message", "episode_closed")
    r = client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": "x"})
    assert r.status_code == 409
    assert fake.audit == []


def test_a_failed_audit_is_a_500_not_silence(client, monkeypatch):
    def boom(_db, **_kw):
        raise messages_module.AuditWriteError("down")

    monkeypatch.setattr(messages_module, "record_event", boom)
    r = client.post(_thread(CLINIC_A, PATIENT_A), headers=AUTH, json={"body": "x"})
    assert r.status_code == 500 and "not audited" in r.json()["detail"]


def test_mark_read_only_touches_patient_messages_and_is_idempotent(client, fake):
    p = fake.add_message(id="p1", sender="patient")
    c = fake.add_message(id="c1", sender="clinician")
    first = client.post(f"/clinics/{CLINIC_A}/messages/{p['id']}/read", headers=AUTH).json()
    assert first["read_at"] is not None
    again = client.post(f"/clinics/{CLINIC_A}/messages/{p['id']}/read", headers=AUTH).json()
    assert again["read_at"] == first["read_at"]
    own = client.post(f"/clinics/{CLINIC_A}/messages/{c['id']}/read", headers=AUTH).json()
    assert own["read_at"] is None


def test_mark_read_audits_only_the_first_read(client, fake):
    fake.add_message(id="p1", sender="patient", body="private words")
    url = f"/clinics/{CLINIC_A}/messages/p1/read"
    first = client.post(url, headers=AUTH).json()
    client.post(url, headers=AUTH)  # repeat read: no second event
    (call,) = fake.audit
    assert call["event_type"] == "message.read" and call["clinic_id"] == CLINIC_A
    assert call["payload"]["read_by"] == USER and call["payload"]["read_at"] == first["read_at"]
    assert (call["metadata"]["ref_type"], call["metadata"]["ref_id"]) == ("message", "p1")
    assert call["metadata"]["actor"] == {"type": "member", "id": USER, "role": "clinician"}
    assert "private words" not in str(call["metadata"])


def test_mark_read_does_not_audit_clinician_messages(client, fake):
    fake.add_message(id="c1", sender="clinician")
    client.post(f"/clinics/{CLINIC_A}/messages/c1/read", headers=AUTH)
    assert fake.audit == []


def test_mark_read_reports_500_when_audit_write_fails(client, fake, monkeypatch):
    def boom(_db, **_kw):
        raise messages_module.AuditWriteError("down")

    monkeypatch.setattr(messages_module, "record_event", boom)
    fake.add_message(id="p1", sender="patient")
    r = client.post(f"/clinics/{CLINIC_A}/messages/p1/read", headers=AUTH)
    assert r.status_code == 500 and "not audited" in r.json()["detail"]


def test_mark_read_other_clinic_message_is_404(client, fake):
    fake.add_message(id="pb", sender="patient", clinic=CLINIC_B, patient=PATIENT_B)
    assert client.post(f"/clinics/{CLINIC_A}/messages/pb/read", headers=AUTH).status_code == 404


# --- patient side: the episode link --------------------------------------------------------


def send(client, fake, **body):
    return client.post("/messages", params=tok(fake), json={"body": "Pain is worse", **body})


def test_patient_endpoints_reject_bad_token(client):
    assert client.get("/messages", params={"token": "garbage"}).status_code == 401
    r = client.post("/messages", params={"token": "garbage"}, json={"body": "hi"})
    assert r.status_code == 401


def test_a_scheduled_message_is_stamped_with_the_interval_the_server_works_out(client, fake):
    r = send(client, fake, category="symptom_or_wound", interval_no=7, sender="clinician",
             patient_id=PATIENT_B, episode_id="x")
    assert r.status_code == 200
    # Identity, sender and interval come from the link and the clock, never the body.
    (_, params), = fake.rpc_calls
    assert params == {
        "p_clinic_id": CLINIC_A, "p_episode_id": EPISODE_A, "p_body": "Pain is worse",
        "p_origin": "scheduled", "p_interval_no": 1, "p_category": "symptom_or_wound",
    }
    assert r.json()["sender"] == "patient"


def test_the_audit_event_names_patient_origin_interval_and_category(client, fake):
    send(client, fake, category="medication")
    (call,) = fake.audit
    assert call["event_type"] == "message.sent"
    assert call["metadata"]["actor"] == {"type": "patient", "id": PATIENT_A}
    assert (call["metadata"]["origin"], call["metadata"]["interval_no"],
            call["metadata"]["category"]) == ("scheduled", 1, "medication")
    assert call["payload"]["category"] == "medication" and call["payload"]["sender"] == "patient"


@pytest.mark.parametrize("category", [None, "", "billing", "SYMPTOM_OR_WOUND"])
def test_a_scheduled_message_needs_one_of_the_offered_categories(client, fake, category):
    r = send(client, fake, category=category)
    assert r.status_code == 422
    assert fake.rpc_calls == []


def test_locked_until_the_check_in(client, fake):
    fake.tables["episodes"][0]["checkin_completed_at"] = None
    r = send(client, fake, category="admin")
    assert r.status_code == 403 and "check-in" in r.json()["detail"]
    assert fake.rpc_calls == []


def test_before_the_first_interval_opens(client, fake, monkeypatch):
    pin_clock(monkeypatch, datetime(2026, 10, 2, 20, 0, tzinfo=UTC))  # day before interval 1
    r = send(client, fake, category="admin")
    assert r.status_code == 409 and "not opened" in r.json()["detail"]
    assert fake.rpc_calls == []


def test_a_used_interval_is_refused_until_the_next_opens(client, fake, monkeypatch):
    fake.add_message(sender="patient", interval_no=1)
    r = send(client, fake, category="admin")
    assert r.status_code == 409 and "already sent" in r.json()["detail"]
    pin_clock(monkeypatch, datetime(2026, 10, 4, 11, 0, tzinfo=UTC))  # interval 2 opens
    assert send(client, fake, category="admin").status_code == 200
    assert fake.rpc_calls[-1][1]["p_interval_no"] == 2


def test_a_reply_needs_a_doctor_message_waiting_and_carries_no_category(client, fake):
    r = send(client, fake, kind="reply")
    assert r.status_code == 409 and "reply to" in r.json()["detail"]
    fake.add_message(id="d1", sender="clinician")
    r = send(client, fake, kind="reply", category="medication")
    assert r.status_code == 200
    assert fake.rpc_calls[-1][1] | {"p_body": ""} == {
        "p_clinic_id": CLINIC_A, "p_episode_id": EPISODE_A, "p_body": "",
        "p_origin": "reply", "p_interval_no": None, "p_category": None,
    }


def test_a_reply_works_even_when_the_interval_is_used_or_the_check_in_not_done(client, fake):
    fake.tables["episodes"][0]["checkin_completed_at"] = None
    fake.add_message(sender="clinician")
    assert send(client, fake, kind="reply").status_code == 200


def test_a_reply_already_given_is_refused(client, fake):
    d = fake.add_message(id="d1", sender="clinician")
    fake.add_message(sender="patient", origin="reply", reply_to_message_id=d["id"])
    assert send(client, fake, kind="reply").status_code == 409


@pytest.mark.parametrize(
    ("db_error", "status"),
    [("episode_closed", 410), ("check_in_required", 403), ("interval_already_used", 409),
     ("no_reply_available", 409), ("message_body_invalid", 422)],
)
def test_the_databases_refusals_are_reported_with_their_own_status(client, fake, db_error, status):
    fake.add_message(sender="clinician")  # so a reply passes the pre-check too
    fail_with(fake, "send_patient_message", f'{{"message": "{db_error}"}}')
    kind = "reply" if db_error == "no_reply_available" else "scheduled"
    r = send(client, fake, kind=kind, category="admin")
    assert r.status_code == status
    assert fake.audit == []


def test_an_unexpected_database_error_is_not_swallowed(client, fake):
    fail_with(fake, "send_patient_message", "connection reset")
    with pytest.raises(RuntimeError):
        send(client, fake, category="admin")


def test_the_patient_sees_only_this_episodes_thread_and_clinician_sees_it_too(client, fake):
    send(client, fake, category="admin", body="hello doctor")
    mine = client.get("/messages", params=tok(fake)).json()
    assert [(m["sender"], m["body"], m["category"]) for m in mine] == [
        ("patient", "hello doctor", "admin")
    ]
    thread = client.get(_thread(CLINIC_A, PATIENT_A), headers=AUTH).json()
    assert [m["body"] for m in thread] == ["hello doctor"]
    read = client.post(f"/clinics/{CLINIC_A}/messages/{thread[0]['id']}/read", headers=AUTH)
    assert read.json()["read_at"] is not None
