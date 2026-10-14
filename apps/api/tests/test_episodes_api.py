"""Episodes, clinician side: a new surgery for an existing patient, correcting the surgery
date, discharge, and the patient's link (issue, reset). The rules the database enforces
(frozen once closed, correction locked after the check-in) are tested in tests/db."""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.security import verify_episode_token
from clinically_anchored_api.main import app
from tests.fakes import (
    AUTH,
    CLINIC_A,
    CLINIC_B,
    EPISODE_A,
    PATIENT_A,
    PATIENT_B,
    PROC_A,
    USER,
    FakeSupabase,
    install,
    pin_clock,
)

A = CLINIC_A
EP = f"/clinics/{A}/episodes/{EPISODE_A}"


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase(role="clinician")
    f.add_episode()
    pin_clock(monkeypatch)
    f.audits = install(monkeypatch, f)
    monkeypatch.setattr(get_settings(), "web_base_url", "https://app.example.com/")
    return f


@pytest.fixture
def client(fake):
    return TestClient(app)


def row(fake):
    return fake.tables["episodes"][0]


def closed(fake):
    row(fake).update(
        closed_at="2026-10-03T00:00:00+00:00", close_reason="discharged", closed_by=USER
    )


def close_handler(fake):
    def close(p):
        r = row(fake)
        if r["closed_at"]:
            return {"episode": dict(r), "changed": False}
        r.update(closed_at="2026-10-03T11:30:00+00:00", close_reason=p["p_reason"],
                 closed_by=p["p_closed_by"])
        return {"episode": dict(r), "changed": True}

    fake.rpc_handlers["close_episode"] = close


# --- reading and a new surgery -----------------------------------------------------------


def test_get_the_patients_episode_and_its_state(client):
    body = client.get(f"/clinics/{A}/patients/{PATIENT_A}/episode", headers=AUTH).json()
    assert body["id"] == EPISODE_A and body["state"]["phase"] == "locked_checkin"
    assert body["procedure_name"] == "Colectomy"
    assert body["state"]["episode_closes_at"].startswith("2026-11-19T12:00:00")


def test_no_episode_is_a_404_and_other_clinics_are_403_or_404(client, fake):
    assert client.get(f"/clinics/{A}/patients/{PATIENT_B}/episode", headers=AUTH).status_code == 404
    fake.tables["episodes"].clear()
    assert client.get(f"/clinics/{A}/patients/{PATIENT_A}/episode", headers=AUTH).status_code == 404
    other = client.get(f"/clinics/{CLINIC_B}/patients/{PATIENT_B}/episode", headers=AUTH)
    assert other.status_code == 403


def test_a_new_surgery_for_an_existing_patient(client, fake):
    close_handler(fake)
    client.post(f"{EP}/close", headers=AUTH)  # the first episode is over
    r = client.post(
        f"/clinics/{A}/patients/{PATIENT_A}/episodes", headers=AUTH,
        json={"surgery_date": "2026-10-02", "procedure_id": PROC_A},
    )
    assert r.status_code == 200 and r.json()["state"]["phase"] == "locked_checkin"
    assert len(fake.tables["episodes"]) == 2
    assert fake.audits[-1]["event_type"] == "episode.created"


def test_a_second_open_episode_is_a_409(client, fake):
    fake.fail_insert["episodes"] = RuntimeError(
        'violates unique constraint "episodes_one_open_per_patient"'
    )
    r = client.post(
        f"/clinics/{A}/patients/{PATIENT_A}/episodes", headers=AUTH,
        json={"surgery_date": "2026-10-02", "procedure_id": PROC_A},
    )
    assert r.status_code == 409


def test_an_untouched_episode_that_ran_out_does_not_block_a_new_one(client, fake, monkeypatch):
    from datetime import UTC, datetime

    pin_clock(monkeypatch, datetime(2026, 12, 20, 15, 0, tzinfo=UTC))  # long after Nov 19
    close_handler(fake)
    r = client.post(
        f"/clinics/{A}/patients/{PATIENT_A}/episodes", headers=AUTH,
        json={"surgery_date": "2026-12-18", "procedure_id": PROC_A},
    )
    assert r.status_code == 200
    assert fake.rpc_calls[0][1]["p_reason"] == "schedule_complete"  # the old one was settled first
    assert [a["event_type"] for a in fake.audits] == ["episode.closed", "episode.created"]


# --- correcting --------------------------------------------------------------------------


def test_correct_the_surgery_date_and_audit_from_to(client, fake):
    r = client.patch(EP, headers=AUTH, json={"surgery_date": "2026-10-02"})
    assert r.status_code == 200 and row(fake)["surgery_date"] == "2026-10-02"
    (event,) = fake.audits
    assert event["event_type"] == "episode.corrected"
    assert event["payload"]["from"]["surgery_date"] == "2026-10-01"
    assert event["payload"]["to"]["surgery_date"] == "2026-10-02"


def test_nothing_to_change_is_a_422(client):
    assert client.patch(EP, headers=AUTH, json={}).status_code == 422


def test_correction_after_the_check_in_is_a_409_with_advice(client, fake):
    # The database's trigger refuses; the api turns that into advice.
    fake.fail_update["episodes"] = RuntimeError(
        "episodes_surgery_locked: fixed once the patient has checked in"
    )
    r = client.patch(EP, headers=AUTH, json={"surgery_date": "2026-10-02"})
    assert r.status_code == 409 and "start a new one" in r.json()["detail"]
    assert fake.audits == []


def test_correction_validates_the_procedure_and_the_date(client, fake):
    assert client.patch(EP, headers=AUTH, json={"procedure_id": "nope"}).status_code == 422
    r = client.patch(EP, headers=AUTH, json={"surgery_date": "2026-01-01"})
    assert r.status_code == 422 and "already ended" in r.json()["detail"]


def test_a_closed_episode_cannot_be_corrected(client, fake):
    closed(fake)
    assert client.patch(EP, headers=AUTH, json={"surgery_date": "2026-10-02"}).status_code == 409


def test_another_clinics_episode_is_not_found(client, fake):
    fake.add_episode(id="e-b", clinic=CLINIC_B, patient=PATIENT_B)
    change = {"surgery_date": "2026-10-02"}
    r = client.patch(f"/clinics/{A}/episodes/e-b", headers=AUTH, json=change)
    assert r.status_code == 404


# --- discharge ------------------------------------------------------------------------------


def test_discharge_closes_audits_and_returns_the_closed_state(client, fake):
    close_handler(fake)
    r = client.post(f"{EP}/close", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["close_reason"] == "discharged" and body["state"]["phase"] == "closed"
    (event,) = fake.audits
    assert event["event_type"] == "episode.closed"
    assert event["payload"]["reason"] == "discharged" and event["payload"]["closed_by"] == USER
    assert event["metadata"]["actor"] == {"type": "member", "id": USER, "role": "clinician"}


def test_discharging_twice_is_a_409_and_audits_once(client, fake):
    close_handler(fake)
    assert client.post(f"{EP}/close", headers=AUTH).status_code == 200
    assert client.post(f"{EP}/close", headers=AUTH).status_code == 409
    assert len(fake.audits) == 1


def test_a_delegate_cannot_discharge(client, fake):
    fake.tables["clinic_members"][0]["role"] = "delegate"
    close_handler(fake)
    assert client.post(f"{EP}/close", headers=AUTH).status_code == 403
    assert fake.rpc_calls == []


def test_a_failed_audit_on_discharge_is_a_500(client, fake, monkeypatch):
    from clinically_anchored_api.core import episodes as core

    def boom(_db, **_kw):
        raise core.AuditWriteError("down")

    close_handler(fake)
    monkeypatch.setattr(core, "record_event", boom)
    r = client.post(f"{EP}/close", headers=AUTH)
    assert r.status_code == 500 and "not audited" in r.json()["detail"]


# --- the link ------------------------------------------------------------------------------


def test_the_link_is_the_same_url_each_time_until_reset(client, fake):
    one = client.post(f"{EP}/link", headers=AUTH).json()
    two = client.post(f"{EP}/link", headers=AUTH).json()
    assert one == two and one["generation"] == 1
    url = urlparse(one["url"])
    assert (url.netloc, url.path) == ("app.example.com", "/care")
    assert verify_episode_token(parse_qs(url.query)["token"][0]) == (EPISODE_A, 1)


def test_issuing_is_audited_without_the_token(client, fake):
    url = client.post(f"{EP}/link", headers=AUTH).json()["url"]
    (event,) = fake.audits
    assert event["event_type"] == "link.issued"
    assert parse_qs(urlparse(url).query)["token"][0] not in str(event)
    assert event["metadata"]["generation"] == 1 and event["metadata"]["episode_id"] == EPISODE_A


def test_reset_gives_a_new_url_that_works_and_kills_the_old_one(client, fake):
    old = client.post(f"{EP}/link", headers=AUTH).json()
    new = client.post(f"{EP}/link/reset", headers=AUTH).json()
    assert new["generation"] == 2 and new["url"] != old["url"]
    assert row(fake)["link_generation"] == 2
    old_token = parse_qs(urlparse(old["url"]).query)["token"][0]
    new_token = parse_qs(urlparse(new["url"]).query)["token"][0]
    assert client.get("/messages", params={"token": old_token}).status_code == 401
    assert client.get("/messages", params={"token": new_token}).status_code == 200
    assert fake.audits[-1]["event_type"] == "episode.link_rotated"
    assert fake.audits[-1]["payload"] == {
        "episode_id": EPISODE_A, "patient_id": PATIENT_A, "from_generation": 1, "to_generation": 2,
    }


def test_no_link_for_a_closed_episode(client, fake):
    closed(fake)
    assert client.post(f"{EP}/link", headers=AUTH).status_code == 409
    assert client.post(f"{EP}/link/reset", headers=AUTH).status_code == 409


def test_link_needs_web_base_url_outside_development(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "web_base_url", "")
    monkeypatch.setattr(settings, "environment", "production")
    assert client.post(f"{EP}/link", headers=AUTH).status_code == 503


def test_link_for_another_clinics_episode_is_404_and_not_a_member_is_403(client, fake):
    fake.add_episode(id="e-b", clinic=CLINIC_B, patient=PATIENT_B)
    assert client.post(f"/clinics/{A}/episodes/e-b/link", headers=AUTH).status_code == 404
    assert client.post(f"/clinics/{CLINIC_B}/episodes/e-b/link", headers=AUTH).status_code == 403
    assert fake.audits == []
