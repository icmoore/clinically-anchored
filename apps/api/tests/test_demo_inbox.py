"""GET /clinics/{id}/demo-inbox: only an owner of a demo clinic sees the emails it did not send."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.main import app
from tests.fakes import AUTH, CLINIC_A, CLINIC_B, PATIENT_A, FakeSupabase, install


def make(monkeypatch, role="owner", demo=True):
    fake = FakeSupabase(role=role)
    fake.tables["clinics"][0]["is_demo"] = demo
    fake.tables["clinics"][1]["is_demo"] = True
    install(monkeypatch, fake)
    return fake, TestClient(app)


def email(fake, clinic=CLINIC_A, status="demo", recipient="pat@example.com", at="01", **extra):
    row = {
        "clinic_id": clinic, "patient_id": PATIENT_A, "recipient": recipient,
        "template": "sign_in_code", "status": status, "created_at": f"2026-10-01T10:00:{at}+00:00",
        "rendered_subject": f"subject {at}", "rendered_text": f"text {at} 123456",
        "rendered_html": f"<p>{at}</p>", **extra,
    }
    fake.tables["email_outbox"].append({"id": f"id-{at}-{clinic[:1]}-{status}", **row})


def test_an_owner_of_a_demo_clinic_sees_its_emails_newest_first(monkeypatch):
    fake, client = make(monkeypatch)
    email(fake, at="01")
    email(fake, at="03")
    email(fake, at="02", recipient="b@example.com")
    r = client.get(f"/clinics/{CLINIC_A}/demo-inbox", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert [e["subject"] for e in body] == ["subject 03", "subject 02", "subject 01"]
    first = body[0]
    assert first["text"] == "text 03 123456" and first["html"] == "<p>03</p>"
    assert set(first) == {"id", "recipient", "template", "patient_id", "subject", "text", "html",
                          "created_at"}


def test_only_demo_rows_of_this_clinic_are_shown(monkeypatch):
    fake, client = make(monkeypatch)
    email(fake, at="01")
    email(fake, clinic=CLINIC_B, at="02")  # another clinic's demo mail
    row = {"clinic_id": CLINIC_A, "status": "sent", "recipient": "x@mail.org"}
    fake.tables["email_outbox"].append({"id": "sent-1", "template": "invite", "patient_id": None,
                                        "created_at": "2026-10-01T10:00:09+00:00", **row})
    body = client.get(f"/clinics/{CLINIC_A}/demo-inbox", headers=AUTH).json()
    assert [e["subject"] for e in body] == ["subject 01"]


def test_limit_applies(monkeypatch):
    fake, client = make(monkeypatch)
    for n in range(5):
        email(fake, at=f"0{n}")
    assert len(client.get(f"/clinics/{CLINIC_A}/demo-inbox?limit=2", headers=AUTH).json()) == 2
    assert client.get(f"/clinics/{CLINIC_A}/demo-inbox?limit=0", headers=AUTH).status_code == 422


@pytest.mark.parametrize("role", ["clinician", "delegate"])
def test_other_members_of_a_demo_clinic_are_refused(monkeypatch, role):
    fake, client = make(monkeypatch, role=role)
    email(fake)
    r = client.get(f"/clinics/{CLINIC_A}/demo-inbox", headers=AUTH)
    assert r.status_code == 403 and "text" not in r.text


def test_an_owner_of_an_ordinary_clinic_gets_not_found(monkeypatch):
    fake, client = make(monkeypatch, demo=False)
    email(fake)  # even if rows somehow existed
    assert client.get(f"/clinics/{CLINIC_A}/demo-inbox", headers=AUTH).status_code == 404


def test_a_user_who_does_not_belong_to_the_clinic_is_refused(monkeypatch):
    fake, client = make(monkeypatch)
    email(fake, clinic=CLINIC_B)
    # The user is a member of A only: B's demo inbox is closed to them.
    assert client.get(f"/clinics/{CLINIC_B}/demo-inbox", headers=AUTH).status_code == 403


def test_no_sign_in_no_inbox(monkeypatch):
    _, client = make(monkeypatch)
    assert client.get(f"/clinics/{CLINIC_A}/demo-inbox").status_code == 401
    assert client.get(f"/clinics/{CLINIC_A}/demo-inbox",
                      headers={"Authorization": "Bearer nope"}).status_code == 401
