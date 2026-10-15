"""The clinic's office phone: owner-only edit, North American validation, its own audit event, a
visible "not set" state for the clinic, and a patient screen that never shows a blank number."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import clinics as clinics_module
from clinically_anchored_api.core.audit import AuditWriteError
from clinically_anchored_api.main import app
from tests.fakes import (
    AUTH,
    CLINIC_A,
    CLINIC_B,
    USER,
    FakeSupabase,
    install,
    pin_clock,
)

URL = f"/clinics/{CLINIC_A}/office-phone"


def make(monkeypatch, role="owner", phone="905-555-0100"):
    fake = FakeSupabase(role=role)
    fake.tables["clinics"][0]["office_phone"] = phone
    pin_clock(monkeypatch)
    fake.audits = install(monkeypatch, fake)
    return fake, TestClient(app)


def phone_of(fake, clinic=CLINIC_A):
    return next(c for c in fake.tables["clinics"] if c["id"] == clinic)["office_phone"]


# --- who sees it, who changes it ---------------------------------------------------------------


def test_me_carries_each_clinics_office_phone_and_null_when_blank(monkeypatch):
    fake, client = make(monkeypatch)
    fake.tables["clinic_members"].append(
        {"clinic_id": CLINIC_B, "user_id": USER, "role": "clinician"}
    )
    fake.tables["clinics"][1]["office_phone"] = "   "  # a blank must read as "not set"
    clinics = {c["id"]: c for c in client.get("/me", headers=AUTH).json()["clinics"]}
    assert clinics[CLINIC_A]["office_phone"] == "905-555-0100"
    assert clinics[CLINIC_B]["office_phone"] is None


@pytest.mark.parametrize("role", ["clinician", "delegate"])
def test_only_an_owner_can_change_it(monkeypatch, role):
    fake, client = make(monkeypatch, role=role)
    r = client.put(URL, headers=AUTH, json={"office_phone": "416-555-0188"})
    assert r.status_code == 403 and "owner" in r.json()["detail"]
    assert phone_of(fake) == "905-555-0100" and fake.audits == []


def test_a_non_member_cannot_and_needs_to_be_signed_in(monkeypatch):
    fake, client = make(monkeypatch)
    other = client.put(
        f"/clinics/{CLINIC_B}/office-phone", headers=AUTH, json={"office_phone": "416-555-0188"}
    )
    assert other.status_code == 403
    assert client.put(URL, json={"office_phone": "416-555-0188"}).status_code == 401
    assert phone_of(fake, CLINIC_B) is None and fake.audits == []


# --- setting, changing, clearing ------------------------------------------------------------


def test_an_owner_sets_it_in_any_north_american_format_and_it_is_stored_in_one_form(monkeypatch):
    fake, client = make(monkeypatch, phone=None)
    r = client.put(URL, headers=AUTH, json={"office_phone": "+1 (416) 555-0188"})
    assert r.status_code == 200
    assert r.json() == {
        "id": CLINIC_A, "name": "A", "role": "owner", "office_phone": "416-555-0188",
    }
    assert phone_of(fake) == "416-555-0188"


def test_a_change_is_audited_as_its_own_event_with_old_and_new_in_the_hashed_payload(monkeypatch):
    fake, client = make(monkeypatch)
    client.put(URL, headers=AUTH, json={"office_phone": "416.555.0188"})
    (event,) = fake.audits
    assert event["event_type"] == "clinic.office_phone_changed"
    assert event["clinic_id"] == CLINIC_A
    assert event["payload"] == {
        "clinic_id": CLINIC_A, "from": "905-555-0100", "to": "416-555-0188",
    }
    assert event["metadata"] == {
        "ref_type": "clinic", "ref_id": CLINIC_A, "action": "changed",
        "actor": {"type": "member", "id": USER, "role": "owner"},
    }


def test_the_audit_action_says_whether_it_was_set_changed_or_cleared(monkeypatch):
    fake, client = make(monkeypatch, phone=None)
    client.put(URL, headers=AUTH, json={"office_phone": "905-555-0100"})
    client.put(URL, headers=AUTH, json={"office_phone": "905-555-0101"})
    client.put(URL, headers=AUTH, json={"office_phone": None})
    assert [e["metadata"]["action"] for e in fake.audits] == ["set", "changed", "cleared"]


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_null_or_blank_clears_it(monkeypatch, blank):
    fake, client = make(monkeypatch)
    r = client.put(URL, headers=AUTH, json={"office_phone": blank})
    assert r.status_code == 200 and r.json()["office_phone"] is None
    assert phone_of(fake) is None
    assert fake.audits[0]["payload"]["to"] is None


def test_saving_what_is_already_there_writes_and_audits_nothing(monkeypatch):
    fake, client = make(monkeypatch)
    for same in ("905-555-0100", "(905) 555-0100", "+1 905 555 0100"):
        assert client.put(URL, headers=AUTH, json={"office_phone": same}).status_code == 200
    fake.tables["clinics"][0]["office_phone"] = None
    still_unset = client.put(URL, headers=AUTH, json={"office_phone": ""})
    assert still_unset.status_code == 200
    assert fake.audits == []


@pytest.mark.parametrize(
    "bad",
    ["call us", "905-555-0100 ext 4", "555-0100", "+44 20 7946 0958", "905-155-0100",
     "911-555-0100"],
)
def test_a_number_that_is_not_north_american_is_refused_and_changes_nothing(
    monkeypatch, bad
):
    fake, client = make(monkeypatch)
    r = client.put(URL, headers=AUTH, json={"office_phone": bad})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and r.json()["detail"]
    assert phone_of(fake) == "905-555-0100" and fake.audits == []


def test_a_failed_audit_is_reported_not_hidden(monkeypatch):
    fake, client = make(monkeypatch)

    def boom(_db, **_kw):
        raise AuditWriteError("down")

    monkeypatch.setattr(clinics_module, "record_event", boom)
    r = client.put(URL, headers=AUTH, json={"office_phone": "416-555-0188"})
    assert r.status_code == 500 and "not audited" in r.json()["detail"]


# --- what the patient sees -----------------------------------------------------------------------


def patient_state(monkeypatch, phone, closed=False):
    fake = FakeSupabase()
    fake.tables["clinics"][0]["office_phone"] = phone
    fake.add_episode(
        checkin_completed_at="2026-10-02T12:00:00+00:00",
        **({"closed_at": "2026-10-02T13:00:00+00:00", "close_reason": "discharged",
            "closed_by": USER} if closed else {}),
    )
    pin_clock(monkeypatch)
    install(monkeypatch, fake)
    from tests.fakes import token_for

    client = TestClient(app)
    token = token_for(fake.tables["episodes"][0])
    return client, {"token": token}


@pytest.mark.parametrize("stored", [None, "", "   "])
@pytest.mark.parametrize("closed", [False, True])
def test_with_no_number_the_patient_api_says_null_never_a_blank(monkeypatch, stored, closed):
    client, params = patient_state(monkeypatch, stored, closed)
    assert client.get("/episode", params=params).json()["office_phone"] is None


def test_the_patient_sees_the_number_when_there_is_one_on_every_screen(monkeypatch):
    client, params = patient_state(monkeypatch, "905-555-0100")
    assert client.get("/episode", params=params).json()["office_phone"] == "905-555-0100"
    assert client.get("/check-ins/context", params=params).json()["office_phone"] == "905-555-0100"


def test_the_check_in_screen_gets_null_too_when_there_is_no_number(monkeypatch):
    client, params = patient_state(monkeypatch, None)
    assert client.get("/check-ins/context", params=params).json()["office_phone"] is None
