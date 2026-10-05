"""Patient contact/time log: manual logging, listing, clinic scoping, and the
auto-logged `message` touchpoint written when a clinician message is sent
(direct send, or an approved/edited AI draft). Against an in-memory fake that
honours eq() filters and emulates the `decide_message_draft` database function;
audit events are captured, not signed."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import drafts as drafts_module
from clinically_anchored_api.api import messages as messages_module
from clinically_anchored_api.api import touchpoints as touchpoints_api
from clinically_anchored_api.core import auth as auth_module
from clinically_anchored_api.core import touchpoints as touchpoints_core
from clinically_anchored_api.core.audit import AuditWriteError
from clinically_anchored_api.core.security import create_link_token
from clinically_anchored_api.main import app

CLINIC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLINIC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PATIENT_A = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
PATIENT_B = "b1b1b1b1-b1b1-b1b1-b1b1-b1b1b1b1b1b1"
DOCTOR = "99999999-9999-9999-9999-999999999999"
SECRETARY = "77777777-7777-7777-7777-777777777777"
AUTH = {"Authorization": "Bearer doctor"}
SECRETARY_AUTH = {"Authorization": "Bearer secretary"}


class _Query:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self._eq: list[tuple[str, object]] = []
        self._order: tuple[str, bool] | None = None
        self._insert: dict | None = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def insert(self, payload):
        self._insert = payload
        return self

    def execute(self):
        if self._insert is not None:
            return type("R", (), {"data": [self.db.insert(self.name, self._insert)]})()
        rows = [dict(r) for r in self.db.tables[self.name]
                if all(r.get(c) == v for c, v in self._eq)]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: r[col], reverse=desc)
        return type("R", (), {"data": rows})()


class _Rpc:
    def __init__(self, db, params):
        self.db, self.params = db, params

    def execute(self):
        return type("R", (), {"data": self.db.decide(**self.params)})()


class _FakeAuth:
    def get_user(self, jwt):
        users = {"doctor": DOCTOR, "secretary": SECRETARY}
        if jwt not in users:
            raise ValueError("bad jwt")
        return type("UR", (), {"user": type("U", (), {"id": users[jwt]})()})()


class _FakeSupabase:
    def __init__(self):
        self.auth = _FakeAuth()
        self.fail_touchpoint_insert = False
        self.tables = {
            "clinic_members": [
                {"clinic_id": CLINIC_A, "user_id": DOCTOR, "role": "clinician"},
                {"clinic_id": CLINIC_A, "user_id": SECRETARY, "role": "delegate"},
            ],
            "patients": [
                {"id": PATIENT_A, "clinic_id": CLINIC_A},
                {"id": PATIENT_B, "clinic_id": CLINIC_B},
            ],
            "messages": [],
            "message_drafts": [],
            "touchpoints": [],
        }

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params):
        assert name == "decide_message_draft"
        return _Rpc(self, params)

    def insert(self, name, payload):
        if name == "touchpoints" and self.fail_touchpoint_insert:
            raise RuntimeError("touchpoints table unavailable")
        rows = self.tables[name]
        n = len(rows) + 1
        defaults = {
            "messages": {"id": f"msg-{n}", "read_at": None,
                         "created_at": f"2026-01-01T00:00:{n:02d}Z"},
            "touchpoints": {"id": f"tp-{n}", "occurred_at": "2026-01-01T12:00:00Z",
                            "duration_minutes": None, "note": None, "logged_by": None,
                            "created_at": f"2026-01-01T12:00:{n:02d}Z"},
        }[name]
        row = {**defaults, **payload}
        rows.append(row)
        return dict(row)

    def decide(self, p_clinic_id, p_draft_id, p_decision, p_final_text, p_decided_by):
        draft = next(d for d in self.tables["message_drafts"] if d["id"] == p_draft_id)
        message = None
        if p_decision != "rejected":
            body = draft["draft_text"] if p_decision == "approved" else p_final_text
            message = self.insert("messages", {
                "clinic_id": draft["clinic_id"], "patient_id": draft["patient_id"],
                "sender": "clinician", "body": body,
            })
            draft.update(final_text=body, sent_message_id=message["id"])
        draft.update(status=p_decision, decided_by=p_decided_by, decided_at="2026-01-02T00:00:00Z")
        return {"draft": dict(draft), "message": message}


@pytest.fixture
def audit_calls(monkeypatch):
    calls: list[dict] = []
    recorder = lambda _db, **kw: calls.append(kw)  # noqa: E731
    for module in (touchpoints_core, messages_module, drafts_module):
        monkeypatch.setattr(module, "record_event", recorder)
    return calls


@pytest.fixture
def fake(monkeypatch, audit_calls):
    fake = _FakeSupabase()
    for module in (touchpoints_api, messages_module, drafts_module, auth_module):
        monkeypatch.setattr(module, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def client(fake):
    return TestClient(app)


def _url(clinic=CLINIC_A, patient=PATIENT_A):
    return f"/clinics/{clinic}/patients/{patient}/touchpoints"


def _touchpoint_events(audit_calls):
    return [c for c in audit_calls if c["event_type"] == "touchpoint.logged"]


def _pending_draft(fake, text="Thanks, rest up."):
    fake.tables["message_drafts"].append({
        "id": "d1", "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "source_message_id": "m0",
        "draft_text": text, "model_id": "m", "prompt_version": "v1", "status": "pending",
        "final_text": None, "sent_message_id": None, "created_by": DOCTOR,
        "created_at": "2026-01-01T00:00:00Z", "decided_by": None, "decided_at": None,
    })


# --- manual logging -----------------------------------------------------------


def test_requires_bearer_token(client):
    assert client.get(_url()).status_code == 401
    assert client.post(_url(), json={"kind": "call"}).status_code == 401


def test_non_member_is_rejected(client):
    assert client.get(_url(CLINIC_B, PATIENT_B), headers=AUTH).status_code == 403
    r = client.post(_url(CLINIC_B, PATIENT_B), headers=AUTH, json={"kind": "call"})
    assert r.status_code == 403


def test_manual_create_stores_and_returns_the_entry(client, fake):
    when = "2026-01-01T09:30:00Z"
    r = client.post(
        _url(), headers=AUTH,
        json={"kind": "call", "occurred_at": when, "duration_minutes": 12, "note": " Pain better "},
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["kind"], body["source"], body["duration_minutes"], body["note"]) == (
        "call", "manual", 12, "Pain better",
    )
    assert body["logged_by"] == DOCTOR
    row = fake.tables["touchpoints"][0]
    assert (row["clinic_id"], row["patient_id"]) == (CLINIC_A, PATIENT_A)
    assert datetime.fromisoformat(row["occurred_at"]) == datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


def test_manual_create_defaults_occurred_at_to_now_and_optionals_to_null(client, fake):
    r = client.post(_url(), headers=AUTH, json={"kind": "visit"})
    assert r.status_code == 200
    row = fake.tables["touchpoints"][0]
    assert row["duration_minutes"] is None and row["note"] is None
    age = datetime.now(UTC) - datetime.fromisoformat(row["occurred_at"])
    assert abs(age) < timedelta(seconds=30)


def test_any_clinic_member_may_log_including_delegates(client, fake):
    r = client.post(_url(), headers=SECRETARY_AUTH, json={"kind": "email"})
    assert r.status_code == 200
    assert fake.tables["touchpoints"][0]["logged_by"] == SECRETARY


def test_manual_entries_cannot_be_messages_or_auto_or_client_chosen_actor(client, fake):
    assert client.post(_url(), headers=AUTH, json={"kind": "message"}).status_code == 422
    r = client.post(
        _url(), headers=AUTH, json={"kind": "call", "source": "auto", "logged_by": "someone-else"}
    )
    assert r.status_code == 200
    row = fake.tables["touchpoints"][0]
    assert (row["source"], row["logged_by"]) == ("manual", DOCTOR)


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "call", "duration_minutes": 0},
        {"kind": "call", "duration_minutes": -5},
        {"kind": "call", "duration_minutes": 1441},
        {"kind": "call", "note": "x" * 5001},
        {"kind": "fax"},
        {"kind": "call", "occurred_at": "2026-01-01T09:30:00"},  # no timezone
        {"kind": "call", "occurred_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
    ],
)
def test_invalid_input_is_rejected(client, fake, bad):
    assert client.post(_url(), headers=AUTH, json=bad).status_code == 422
    assert fake.tables["touchpoints"] == []


def test_blank_note_is_stored_as_null(client, fake):
    client.post(_url(), headers=AUTH, json={"kind": "call", "note": "   "})
    assert fake.tables["touchpoints"][0]["note"] is None


def test_manual_create_audits_with_member_actor_and_no_note_in_metadata(client, fake, audit_calls):
    client.post(
        _url(), headers=AUTH, json={"kind": "call", "duration_minutes": 5, "note": "private"}
    )
    (event,) = _touchpoint_events(audit_calls)
    row = fake.tables["touchpoints"][0]
    assert event["clinic_id"] == CLINIC_A
    assert event["metadata"] == {
        "ref_type": "touchpoint",
        "ref_id": row["id"],
        "actor": {"type": "member", "id": DOCTOR, "role": "clinician"},
    }
    payload = event["payload"]
    assert payload["note"] == "private"  # hashed by record_event, never stored
    assert (payload["kind"], payload["source"]) == ("call", "manual")
    assert payload["duration_minutes"] == 5
    assert payload["touchpoint_id"] == row["id"] and payload["logged_by"] == DOCTOR
    assert "private" not in str(event["metadata"])


def test_manual_create_reports_500_when_audit_write_fails(client, fake, monkeypatch):
    def boom(_db, **_kw):
        raise AuditWriteError("down")

    monkeypatch.setattr(touchpoints_core, "record_event", boom)
    r = client.post(_url(), headers=AUTH, json={"kind": "call"})
    assert r.status_code == 500 and "not audited" in r.json()["detail"]
    assert len(fake.tables["touchpoints"]) == 1  # saved; reconcile.py will list it


# --- list ---------------------------------------------------------------------


def test_list_is_newest_first_by_when_it_happened(client):
    # Logged out of order: a backdated entry is created last but happened first.
    for kind, when in [
        ("call", "2026-01-03T09:00:00Z"),
        ("visit", "2026-01-05T09:00:00Z"),
        ("email", "2026-01-01T09:00:00Z"),
    ]:
        client.post(_url(), headers=AUTH, json={"kind": kind, "occurred_at": when})
    r = client.get(_url(), headers=AUTH)
    assert r.status_code == 200
    assert [t["kind"] for t in r.json()] == ["visit", "call", "email"]


def test_list_is_scoped_to_the_patient_and_clinic(client, fake):
    fake.tables["patients"].append({"id": "other", "clinic_id": CLINIC_A})
    client.post(_url(), headers=AUTH, json={"kind": "call"})
    client.post(_url(patient="other"), headers=AUTH, json={"kind": "visit"})
    fake.tables["touchpoints"].append({
        "id": "foreign", "clinic_id": CLINIC_B, "patient_id": PATIENT_B, "kind": "call",
        "source": "manual", "occurred_at": "2026-01-01T00:00:00Z", "duration_minutes": None,
        "note": None, "logged_by": None, "created_at": "2026-01-01T00:00:00Z",
    })
    assert [t["kind"] for t in client.get(_url(), headers=AUTH).json()] == ["call"]


def test_another_clinics_patient_is_404_and_writes_nothing(client, fake):
    # Member of A addressing A's URL with B's patient id.
    assert client.get(_url(CLINIC_A, PATIENT_B), headers=AUTH).status_code == 404
    r = client.post(_url(CLINIC_A, PATIENT_B), headers=AUTH, json={"kind": "call"})
    assert r.status_code == 404
    assert fake.tables["touchpoints"] == []


def test_cannot_read_another_clinics_touchpoints(client, fake):
    fake.tables["touchpoints"].append({
        "id": "foreign", "clinic_id": CLINIC_B, "patient_id": PATIENT_B, "kind": "call",
        "source": "manual", "occurred_at": "2026-01-01T00:00:00Z", "duration_minutes": None,
        "note": "b only", "logged_by": None, "created_at": "2026-01-01T00:00:00Z",
    })
    assert client.get(_url(CLINIC_B, PATIENT_B), headers=AUTH).status_code == 403
    assert client.get(_url(CLINIC_A, PATIENT_B), headers=AUTH).status_code == 404


# --- auto-logging on send -----------------------------------------------------


def _assert_auto_message_touchpoint(fake, audit_calls, message_id):
    (row,) = fake.tables["touchpoints"]
    message = next(m for m in fake.tables["messages"] if m["id"] == message_id)
    assert (row["clinic_id"], row["patient_id"]) == (CLINIC_A, PATIENT_A)
    assert (row["kind"], row["source"], row["logged_by"]) == ("message", "auto", None)
    assert row["occurred_at"] == message["created_at"]
    (event,) = _touchpoint_events(audit_calls)
    assert event["metadata"] == {
        "ref_type": "touchpoint", "ref_id": row["id"], "actor": {"type": "system"},
    }
    assert event["payload"]["source"] == "auto" and event["payload"]["logged_by"] is None


def test_sending_a_message_auto_logs_a_touchpoint(client, fake, audit_calls):
    r = client.post(
        f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/messages", headers=AUTH, json={"body": "Hello"}
    )
    assert r.status_code == 200
    _assert_auto_message_touchpoint(fake, audit_calls, r.json()["id"])
    # And it shows up in the contact log.
    assert [t["kind"] for t in client.get(_url(), headers=AUTH).json()] == ["message"]


def test_a_patients_message_is_not_a_touchpoint(client, fake):
    token = create_link_token(clinic_id=CLINIC_A, patient_id=PATIENT_A, scope="messages")
    r = client.post("/messages", params={"token": token}, json={"body": "Pain is worse"})
    assert r.status_code == 200
    assert fake.tables["touchpoints"] == []


@pytest.mark.parametrize(
    ("path", "payload"),
    [("approve", None), ("edit", {"final_text": "Rest, and call us if it worsens."})],
)
def test_approving_or_editing_a_draft_auto_logs_a_touchpoint(
    client, fake, audit_calls, path, payload
):
    _pending_draft(fake)
    r = client.post(f"/clinics/{CLINIC_A}/drafts/d1/{path}", headers=AUTH, json=payload)
    assert r.status_code == 200
    _assert_auto_message_touchpoint(fake, audit_calls, r.json()["message"]["id"])


def test_rejecting_a_draft_logs_nothing(client, fake, audit_calls):
    _pending_draft(fake)
    assert client.post(f"/clinics/{CLINIC_A}/drafts/d1/reject", headers=AUTH).status_code == 200
    assert fake.tables["touchpoints"] == []
    assert _touchpoint_events(audit_calls) == []


def test_a_failed_auto_log_does_not_fail_the_send(client, fake, audit_calls):
    fake.fail_touchpoint_insert = True
    r = client.post(
        f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/messages", headers=AUTH, json={"body": "Hello"}
    )
    assert r.status_code == 200
    assert [m["body"] for m in fake.tables["messages"]] == ["Hello"]
    assert fake.tables["touchpoints"] == []


def test_a_failed_auto_log_does_not_fail_a_draft_decision(client, fake):
    _pending_draft(fake)
    fake.fail_touchpoint_insert = True
    r = client.post(f"/clinics/{CLINIC_A}/drafts/d1/approve", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["draft"]["status"] == "approved" and r.json()["message"] is not None


def test_a_failed_auto_log_audit_does_not_fail_the_send(client, fake, monkeypatch):
    def boom(_db, **_kw):
        raise AuditWriteError("down")

    monkeypatch.setattr(touchpoints_core, "record_event", boom)
    r = client.post(
        f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/messages", headers=AUTH, json={"body": "Hello"}
    )
    assert r.status_code == 200
    assert len(fake.tables["touchpoints"]) == 1  # kept; reconcile.py will list it as unaudited
