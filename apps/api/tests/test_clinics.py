"""/me and the patient list/create: auth, tenant scoping, audit. Creating a patient creates
their episode too (the clinic enters the surgery date and procedure)."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.main import app
from tests.fakes import (
    AUTH,
    CLINIC_A,
    CLINIC_B,
    PROC_A,
    USER,
    FakeSupabase,
    install,
    pin_clock,
)

A, B = CLINIC_A, CLINIC_B
NEW = {"full_name": "  Test Person ", "contact": " 555-0100 ", "surgery_date": "2026-10-01",
       "procedure_id": PROC_A}


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase(role="owner")
    f.tables["patients"] = [
        {"id": "p1", "clinic_id": A, "full_name": "Zed", "contact": None,
         "created_at": "2026-01-01T00:00:00Z"},
        {"id": "p0", "clinic_id": A, "full_name": "Amy", "contact": "amy@example.com",
         "created_at": "2026-01-01T00:00:00Z"},
        {"id": "pb", "clinic_id": B, "full_name": "Other", "contact": None,
         "created_at": "2026-01-01T00:00:00Z"},
    ]
    pin_clock(monkeypatch)
    f.audits = install(monkeypatch, f)
    return f


@pytest.fixture
def client(fake):
    return TestClient(app)


def test_me_lists_only_my_clinics(client):
    body = client.get("/me", headers=AUTH).json()
    assert body["email"] == "u@example.com"
    assert body["clinics"] == [{"id": A, "name": "A", "role": "owner"}]


def test_me_requires_auth(client):
    assert client.get("/me").status_code == 401


def test_patient_endpoints_are_tenant_scoped(client):
    assert client.get(f"/clinics/{B}/patients", headers=AUTH).status_code == 403
    assert client.post(f"/clinics/{B}/patients", headers=AUTH, json=NEW).status_code == 403


def test_list_patients_sorted_by_name_and_clinic_only(client):
    names = [p["full_name"] for p in client.get(f"/clinics/{A}/patients", headers=AUTH).json()]
    assert names == ["Amy", "Zed"]


def test_list_patients_carries_each_patients_latest_episode_and_where_it_stands(client, fake):
    fake.add_episode(id="e-old", patient="p0", surgery_date="2026-08-01",
                     created_at="2026-08-01T00:00:00+00:00",
                     closed_at="2026-09-20T12:00:00+00:00", close_reason="schedule_complete")
    fake.add_episode(id="e-new", patient="p0", surgery_date="2026-10-01",
                     checkin_completed_at="2026-10-02T12:00:00+00:00",
                     created_at="2026-10-01T00:00:00+00:00")
    by_name = {p["full_name"]: p for p in client.get(f"/clinics/{A}/patients", headers=AUTH).json()}
    assert by_name["Zed"]["episode"] is None
    ep = by_name["Amy"]["episode"]
    assert ep["id"] == "e-new" and ep["timezone"] == "America/Toronto"
    assert (ep["state"]["phase"], ep["state"]["interval_no"], ep["state"]["post_op_day"]) == (
        "open", 1, 2,
    )


def test_create_patient_also_creates_the_episode_and_audits_both(client, fake):
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert r.status_code == 200
    body = r.json()
    assert (body["full_name"], body["contact"]) == ("Test Person", "555-0100")
    (episode,) = fake.tables["episodes"]
    assert (episode["patient_id"], episode["surgery_date"], episode["procedure_id"],
            episode["schedule_version"], episode["created_by"]) == (
        body["id"], "2026-10-01", PROC_A, 1, USER,
    )
    assert body["episode"]["id"] == episode["id"]
    assert body["episode"]["state"]["phase"] == "locked_checkin"

    patient_event, episode_event = fake.audits
    assert patient_event["event_type"] == "patient.created"
    assert "Test Person" not in str(patient_event["metadata"])
    assert "555-0100" not in str(patient_event["metadata"])
    assert patient_event["metadata"]["actor"]["role"] == "owner"
    assert episode_event["event_type"] == "episode.created"
    assert episode_event["metadata"]["ref_id"] == episode["id"]
    assert episode_event["metadata"]["schedule_version"] == 1
    snapshot = episode_event["payload"]["schedule"]  # what the episode was created under
    assert snapshot["offsets_days"] == [2, 3, 5, 7, 14, 21, 28, 35, 42]
    assert snapshot["opens_at_local"] == "07:00:00" and snapshot["timezone"] == "America/Toronto"


def test_a_new_patient_gets_the_newest_schedule_version(client, fake):
    fake.tables["message_schedules"].append(
        {"version": 2, "offsets_days": [2, 5, 10, 17, 24, 31, 38, 45, 52], "close_after_days": 7,
         "opens_at_local": "07:00:00", "timezone": "America/Toronto"}
    )
    client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert fake.tables["episodes"][0]["schedule_version"] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"full_name": ""},
        {"surgery_date": "not-a-date"},
        {"surgery_date": "2030-01-01"},  # more than a year away
        {"surgery_date": None},
        {"procedure_id": None},
    ],
)
def test_create_patient_validates_input(client, fake, change):
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json={**NEW, **change})
    assert r.status_code == 422
    assert len(fake.tables["patients"]) == 3 and fake.audits == []


def test_a_procedure_of_another_clinic_or_an_inactive_one_is_rejected_before_anything_is_written(
    client, fake
):
    fake.tables["procedures"].append(
        {"id": "pr-b", "clinic_id": B, "name": "x", "category": "other", "active": True}
    )
    fake.tables["procedures"].append(
        {"id": "pr-off", "clinic_id": A, "name": "x", "category": "other", "active": False}
    )
    for proc in ("pr-b", "pr-off", "nope"):
        r = client.post(f"/clinics/{A}/patients", headers=AUTH, json={**NEW, "procedure_id": proc})
        assert r.status_code == 422
    assert len(fake.tables["patients"]) == 3 and fake.audits == []


def test_a_surgery_date_whose_schedule_has_already_ended_is_rejected(client, fake):
    old = {**NEW, "surgery_date": "2026-06-01"}
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json=old)
    assert r.status_code == 422 and "already ended" in r.json()["detail"]
    assert len(fake.tables["patients"]) == 3


def test_if_the_episode_cannot_be_saved_the_patient_is_taken_back_out(client, fake):
    fake.fail_insert["episodes"] = RuntimeError("episodes_one_open_per_patient")
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert r.status_code == 409
    assert [p["id"] for p in fake.tables["patients"]].count("pb") == 1
    assert len(fake.tables["patients"]) == 3  # the new one is gone again
    assert fake.tables["episodes"] == []
