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
    V2_ROW,
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
    assert body["clinics"] == [
        {"id": A, "name": "A", "role": "owner", "office_phone": "905-555-0100"}
    ]


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
        body["id"], "2026-10-01", PROC_A, 2, USER,  # the newest active schedule: v2
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
    assert episode_event["metadata"]["schedule_version"] == 2
    snapshot = episode_event["payload"]["schedule"]  # what the episode was created under
    assert snapshot["offsets_days"] == [2, 5, 10, 17, 24, 31, 38, 45, 52]
    assert snapshot["close_after_days"] == 7
    assert snapshot["opens_at_local"] == "07:00:00" and snapshot["timezone"] == "America/Toronto"


def _version(fake):
    return fake.tables["episodes"][-1]["schedule_version"]


def test_a_new_patient_gets_the_newest_active_schedule(client, fake):
    client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert _version(fake) == 2


def test_a_newer_version_that_is_not_active_is_skipped(client, fake):
    # Staged for review: inserted with active = false, so it is not the default yet.
    fake.tables["message_schedules"].append({**V2_ROW, "version": 3, "active": False})
    client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert _version(fake) == 2
    fake.tables["message_schedules"][-1]["active"] = True  # flipped on: now it is the default
    client.post(f"/clinics/{A}/patients", headers=AUTH, json={**NEW, "full_name": "Second"})
    assert _version(fake) == 3


def test_retiring_the_newest_version_falls_back_to_the_one_before(client, fake):
    fake.tables["message_schedules"][1]["active"] = False  # v2 off
    client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert _version(fake) == 1


def test_no_active_schedule_is_a_clear_error_and_writes_no_episode(client, fake):
    for row in fake.tables["message_schedules"]:
        row["active"] = False
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert r.status_code == 503 and "schedule" in r.json()["detail"]
    assert fake.tables["episodes"] == []


def test_an_existing_episode_keeps_the_version_it_recorded(client, fake):
    # A v1 episode made before v2 existed, and a patient added now (v2), side by side.
    fake.add_episode(id="e-v1", patient="p0", surgery_date="2026-10-01", schedule_version=1,
                     checkin_completed_at="2026-10-02T12:00:00+00:00")
    client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    listed = {p["full_name"]: p["episode"] for p in
              client.get(f"/clinics/{A}/patients", headers=AUTH).json()}
    old, new = listed["Amy"], listed["Test Person"]
    assert (old["schedule_version"], new["schedule_version"]) == (1, 2)
    # NOW is Saturday 3 Oct 07:30 Toronto, day 2 after Thursday 1 Oct surgery. Under v1 that is
    # window 1 (opened 3 Oct 07:00, closes 4 Oct); under v2 window 1 runs to day 5, 6 Oct.
    assert old["state"]["interval_closes_at"].startswith("2026-10-04T11:00")
    assert new["state"]["intervals_total"] == old["state"]["intervals_total"] == 9
    assert new["state"]["episode_closes_at"].startswith("2026-11-29T12:00")  # day 59
    assert old["state"]["episode_closes_at"].startswith("2026-11-19T12:00")  # v1's day 49


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
