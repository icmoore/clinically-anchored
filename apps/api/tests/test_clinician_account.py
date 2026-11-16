"""POST /me/password-changed: the audit record of a clinician's password change. The password
change itself happens in the browser against Supabase Auth; this only records it."""

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.core import gotrue
from clinically_anchored_api.core.audit import AuditWriteError
from clinically_anchored_api.main import app
from tests.fakes import AUTH, CLINIC_A, CLINIC_B, NOW, USER, FakeSupabase, install, pin_clock

URL = "/me/password-changed"


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase(role="owner")
    f.tables["clinic_members"].append({"clinic_id": CLINIC_B, "user_id": USER, "role": "clinician"})
    pin_clock(monkeypatch)
    f.audits = install(monkeypatch, f)
    f.changed_at = NOW - timedelta(seconds=20)
    monkeypatch.setattr(gotrue, "user_updated_at", lambda _uid: f.changed_at)
    return f


@pytest.fixture
def client(fake):
    return TestClient(app)


def test_it_audits_the_change_in_every_clinic_the_user_belongs_to(client, fake):
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"audited_in_clinics": 2}
    assert [(e["clinic_id"], e["event_type"]) for e in fake.audits] == [
        (CLINIC_A, "clinician.password_changed"),
        (CLINIC_B, "clinician.password_changed"),
    ]
    assert fake.audits[0]["metadata"]["actor"] == {"type": "member", "id": USER, "role": "owner"}
    assert fake.audits[1]["metadata"]["actor"]["role"] == "clinician"


def test_the_audit_record_holds_no_password_material(client, fake):
    # The request has no body for a password to ride in, and the record is the user id only.
    assert client.post(URL, headers=AUTH, json={"password": "hunter2hunter2"}).status_code == 200
    for event in fake.audits:
        assert event["payload"] == {"user_id": USER}
        assert "hunter2" not in json.dumps(event) and "password" not in json.dumps(
            {**event["payload"], **event["metadata"]}
        )


def test_it_requires_a_signed_in_user(client, fake):
    assert client.post(URL).status_code == 401
    assert fake.audits == []


def test_a_claim_with_no_recent_change_at_auth_is_not_audited(client, fake):
    fake.changed_at = NOW - timedelta(minutes=10)
    assert client.post(URL, headers=AUTH).status_code == 409
    fake.changed_at = None
    assert client.post(URL, headers=AUTH).status_code == 409
    assert fake.audits == []


def test_it_does_not_guess_when_supabase_auth_cannot_be_asked(client, fake, monkeypatch):
    def boom(_uid):
        raise RuntimeError("down")

    monkeypatch.setattr(gotrue, "user_updated_at", boom)
    assert client.post(URL, headers=AUTH).status_code == 503
    assert fake.audits == []


def test_a_user_in_no_clinic_has_nowhere_to_audit(client, fake):
    fake.tables["clinic_members"].clear()
    assert client.post(URL, headers=AUTH).status_code == 403


def test_a_failed_audit_write_is_reported(client, fake, monkeypatch):
    import clinically_anchored_api.api.clinician_account as mod

    def fail(*_a, **_k):
        raise AuditWriteError("nope")

    monkeypatch.setattr(mod, "record_event", fail)
    assert client.post(URL, headers=AUTH).status_code == 500


def test_it_is_rate_limited(client, fake):
    codes = [client.post(URL, headers=AUTH).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
