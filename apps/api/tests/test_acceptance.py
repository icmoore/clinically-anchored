"""The "Before you start" screen on the api side: accept, decline, re-prompt, withdraw.

These run on the in-memory fake (tests/fakes.py), so they run in CI; the same flow against a real
Postgres is in tests/db/test_patient_accounts_api.py and test_patient_acceptances_schema.py."""

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.core import gotrue, legal, patient_auth
from clinically_anchored_api.core.audit import AuditWriteError
from clinically_anchored_api.main import app
from tests.fakes import (
    AUTH,
    CLINIC_A,
    EPISODE_A,
    PATIENT_A,
    USER,
    FakeSupabase,
    install,
    patient_headers,
)

BOTH = ["messaging", "ai_assisted_replies"]


def body(action="accepted", **over):
    now = legal.current()
    return {
        "action": action, "terms_version": now.terms_version,
        "privacy_version": now.privacy_version, "consent_version": now.consent_version,
        "consent_sha256": now.consent_sha256,
        "consents": BOTH if action == "accepted" else [], **over,
    }


class World:
    def __init__(self, monkeypatch, *, status="invited", identity=True, role="clinician"):
        self.fake = FakeSupabase(role=role)
        self.events = install(monkeypatch, self.fake)
        self.fake.add_episode()
        self.fake.tables["patients"][0]["email"] = "pat@example.com"
        self.headers = patient_headers(self.fake, status=status)
        account = self.fake.tables["patient_accounts"][0]
        account.update(
            invited_at="2026-10-01T00:00:00+00:00", invited_by=USER, activated_at=None,
            turned_off_at=None, turned_off_by=None, locked_at=None, last_sign_in_at=None,
            identity_verified_at="2026-10-01T01:00:00+00:00" if identity else None,
            identity_failed_attempts=0, code_sent_at=None, code_failed_attempts=0,
        )
        self.account = account
        self.banned: list[str] = []
        monkeypatch.setattr(gotrue, "ban", lambda user_id: self.banned.append(user_id))
        self.fake.rpc_handlers["end_patient_sessions"] = lambda p: None
        self.fake.rpc_handlers["close_episode"] = self._close
        self.client = TestClient(app)

    def _close(self, params):
        episode = next(e for e in self.fake.tables["episodes"] if e["id"] == params["p_episode_id"])
        changed = episode["closed_at"] is None
        if changed:
            episode.update(closed_at="2026-10-02T00:00:00+00:00",
                           close_reason=params["p_reason"], closed_by=params["p_closed_by"])
        return {"episode": dict(episode), "changed": changed}

    def post(self, path, **kw):
        return self.client.post(path, headers=self.headers, **kw)

    def get_account(self):
        return self.client.get("/patient/account", headers=self.headers)

    def rows(self, table="patient_acceptances"):
        return self.fake.tables.get(table, [])

    def types(self):
        return [e["event_type"] for e in self.events]


@pytest.fixture
def w(monkeypatch):
    return World(monkeypatch)


# --- accept ---------------------------------------------------------------------------------


def test_accepting_records_the_versions_and_hashes_and_activates(w):
    assert w.get_account().json()["needs_acceptance"] is True
    r = w.post("/patient/consent", json=body())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active" and r.json()["needs_acceptance"] is False

    (row,) = w.rows()
    now = legal.current()
    assert row["action"] == "accepted"
    assert (row["clinic_id"], row["patient_id"], row["episode_id"]) == (
        CLINIC_A, PATIENT_A, EPISODE_A)
    assert (row["terms_version"], row["privacy_version"], row["consent_version"]) == (
        now.terms_version, now.privacy_version, now.consent_version)
    assert (row["terms_sha256"], row["privacy_sha256"], row["consent_sha256"]) == (
        now.terms_sha256, now.privacy_sha256, now.consent_sha256)
    assert row["created_at"]
    # the existing consents rows are written too, each at its own sentence's version
    granted = {c["consent_type"]: c["consent_text_version"] for c in w.rows("consents")}
    assert granted == now.consent_items


def test_the_hashes_stored_are_the_apis_own_not_the_browsers(w):
    # The browser sends only the consent hash, and it has to match; the document hashes are never
    # taken from it.
    r = w.post("/patient/consent", json={**body(), "terms_sha256": "f" * 64})
    assert r.status_code == 200
    assert w.rows()[0]["terms_sha256"] == legal.current().terms_sha256


def test_every_answer_is_audited_before_the_row_is_written(w, monkeypatch):
    order = []
    original = w.fake.table

    def spy(name):
        query = original(name)
        real = query.insert

        def insert(payload):
            order.append(f"insert:{name}")
            return real(payload)

        query.insert = insert
        return query

    w.fake.table = spy
    real_record = patient_auth.record_event
    monkeypatch.setattr(
        patient_auth, "record_event",
        lambda db, **kw: (order.append(f"audit:{kw['event_type']}"), real_record(db, **kw))[1],
    )
    assert w.post("/patient/consent", json=body()).status_code == 200
    assert order.index("audit:acceptance.accepted") < order.index("insert:patient_acceptances")
    assert order.index("audit:consent.granted") < order.index("insert:consents")
    assert w.types().count("acceptance.accepted") == 1
    event = next(e for e in w.events if e["event_type"] == "acceptance.accepted")
    assert event["metadata"]["ref_id"] == w.rows()[0]["id"]
    assert event["metadata"]["actor"] == {"type": "patient", "id": PATIENT_A}
    assert event["payload"]["terms_sha256"] == legal.current().terms_sha256


@pytest.mark.parametrize("consents", [[], ["messaging"], ["ai_assisted_replies"],
                                      BOTH + ["marketing"]])
def test_both_boxes_are_required_by_the_server_too(w, consents):
    r = w.post("/patient/consent", json=body(consents=consents))
    assert r.status_code == 422
    assert w.rows() == [] and w.rows("consents") == [] and w.events == []
    assert w.account["status"] == "invited"


@pytest.mark.parametrize("over", [
    {"terms_version": "0.9"}, {"privacy_version": "0.9"}, {"consent_version": "old"},
    {"consent_sha256": "0" * 64},
])
def test_an_answer_for_words_that_are_not_the_current_ones_is_refused(w, over):
    r = w.post("/patient/consent", json=body(**over))
    assert r.status_code == 409 and "reload" in r.json()["detail"]
    assert w.rows() == [] and w.events == []


def test_the_surgery_date_comes_first(monkeypatch):
    w = World(monkeypatch, identity=False)
    r = w.post("/patient/consent", json=body())
    assert r.status_code == 409 and "surgery date" in r.json()["detail"]
    assert w.rows() == []


def test_a_double_click_adds_nothing(w):
    assert w.post("/patient/consent", json=body()).status_code == 200
    assert w.post("/patient/consent", json=body()).status_code == 200
    assert len(w.rows()) == 1 and len(w.rows("consents")) == 2
    assert w.types().count("acceptance.accepted") == 1


def test_if_the_audit_cannot_be_written_nothing_is_saved(w, monkeypatch):
    def failing(*_a, **_k):
        raise AuditWriteError("down")

    monkeypatch.setattr(patient_auth, "record_event", failing)
    r = w.post("/patient/consent", json=body())
    assert r.status_code == 500
    assert w.rows() == [] and w.rows("consents") == [] and w.account["status"] == "invited"


# --- decline ---------------------------------------------------------------------------------


def test_declining_is_recorded_and_changes_nothing_else(w):
    r = w.post("/patient/consent", json=body("declined"))
    assert r.status_code == 200
    assert r.json()["status"] == "invited" and r.json()["needs_acceptance"] is True
    (row,) = w.rows()
    assert row["action"] == "declined" and row["terms_version"] == legal.current().terms_version
    assert w.rows("consents") == [] and w.types() == ["acceptance.declined"]
    assert w.account["status"] == "invited"
    # asked again the next time
    assert w.get_account().json()["needs_acceptance"] is True


def test_accepting_after_declining_works_and_the_latest_row_wins(w):
    w.post("/patient/consent", json=body("declined"))
    assert w.post("/patient/consent", json=body()).json()["status"] == "active"
    assert [r["action"] for r in w.rows()] == ["declined", "accepted"]
    assert legal.matches_current(legal.latest(w.fake, CLINIC_A, PATIENT_A))


# --- re-prompt on a version bump ---------------------------------------------------------------


@pytest.mark.parametrize("change", [
    {"terms_version": "1.1", "terms_sha256": "d" * 64},
    {"privacy_version": "1.1", "privacy_sha256": "d" * 64},
    {"consent_version": "ai_assisted_replies:1.0;messaging:1.1", "consent_sha256": "d" * 64},
    {"terms_sha256": "e" * 64},  # same version, different text: asked again to be safe
])
def test_a_new_version_sends_an_active_patient_back_to_the_screen(w, monkeypatch, change):
    w.post("/patient/consent", json=body())
    assert w.get_account().json()["needs_acceptance"] is False
    bumped = dataclasses.replace(legal.current(), **change)
    monkeypatch.setattr(legal, "current", lambda: bumped)
    me = w.get_account().json()
    assert me["status"] == "active" and me["needs_acceptance"] is True
    # and what they are told to expect is the new version
    assert me["terms_version"] == bumped.terms_version

    r = w.post("/patient/consent", json=body(
        terms_version=bumped.terms_version, privacy_version=bumped.privacy_version,
        consent_version=bumped.consent_version, consent_sha256=bumped.consent_sha256))
    assert r.status_code == 200 and r.json()["needs_acceptance"] is False
    assert len(w.rows()) == 2  # the old acceptance is kept, and a new one added


def test_an_old_acceptance_is_not_overwritten_by_the_stale_screen(w, monkeypatch):
    w.post("/patient/consent", json=body())
    stale = body()  # what a screen still showing 1.0 sends
    bumped = dataclasses.replace(legal.current(), terms_version="1.1")
    monkeypatch.setattr(legal, "current", lambda: bumped)
    r = w.post("/patient/consent", json=stale)
    assert r.status_code == 409
    assert len(w.rows()) == 1


# --- withdraw ---------------------------------------------------------------------------------


def test_withdrawing_records_it_stops_the_windows_and_signs_the_patient_out(monkeypatch):
    w = World(monkeypatch, status="active")
    w.post("/patient/consent", json=body())
    w.events.clear()

    r = w.post("/patient/withdraw")
    assert r.status_code == 200, r.text

    last = w.rows()[-1]
    assert last["action"] == "withdrawn" and last["episode_id"] == EPISODE_A
    assert last["terms_version"] == legal.current().terms_version  # what they had accepted
    assert all(c["revoked_at"] for c in w.rows("consents"))
    episode = w.fake.tables["episodes"][0]
    assert episode["close_reason"] == "withdrawn" and episode["closed_by"] == f"puser-{PATIENT_A}"
    assert w.account["status"] == "off" and w.account["turned_off_by"] == f"puser-{PATIENT_A}"
    assert w.banned == [f"puser-{PATIENT_A}"]
    assert ("end_patient_sessions", {"p_user_id": f"puser-{PATIENT_A}"}) in w.fake.rpc_calls
    assert w.types() == ["acceptance.withdrawn", "consent.revoked", "consent.revoked",
                         "episode.closed", "patient_account.access_turned_off"]


def test_a_withdrawn_patient_cannot_use_the_app_or_withdraw_again(monkeypatch):
    w = World(monkeypatch, status="active")
    w.post("/patient/consent", json=body())
    assert w.post("/patient/withdraw").status_code == 200
    patient_auth.reset_cache()
    r = w.get_account()
    assert r.status_code == 200 and r.json()["status"] == "off"
    assert w.post("/patient/withdraw").status_code == 403
    assert w.client.get("/episode", headers=w.headers).status_code == 403


def test_withdrawing_with_no_audit_changes_nothing(monkeypatch):
    w = World(monkeypatch, status="active")
    w.post("/patient/consent", json=body())

    def failing(*_a, **_k):
        raise AuditWriteError("down")

    monkeypatch.setattr(patient_auth, "record_event", failing)
    assert w.post("/patient/withdraw").status_code == 500
    assert len(w.rows()) == 1 and w.account["status"] == "active"
    assert w.fake.tables["episodes"][0]["closed_at"] is None


# --- what the clinic sees ---------------------------------------------------------------------


def clinic_view(w):
    r = w.client.get(f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/account", headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()["acceptance"]


def test_the_clinic_sees_where_the_patient_stands(monkeypatch):
    w = World(monkeypatch)
    assert clinic_view(w) is None
    w.post("/patient/consent", json=body("declined"))
    assert clinic_view(w)["status"] == "declined"
    w.post("/patient/consent", json=body())
    view = clinic_view(w)
    assert view["status"] == "accepted" and view["terms_version"] == legal.current().terms_version
    assert view["at"]

    bumped = dataclasses.replace(legal.current(), privacy_version="1.1")
    monkeypatch.setattr(legal, "current", lambda: bumped)
    assert clinic_view(w)["status"] == "outdated"
    monkeypatch.undo()


def test_the_clinic_sees_a_withdrawal(monkeypatch):
    w = World(monkeypatch, status="active")
    w.post("/patient/consent", json=body())
    w.post("/patient/withdraw")
    assert clinic_view(w)["status"] == "withdrawn"


# --- the generated copy of the legal config -----------------------------------------------------


def test_the_apis_copy_of_the_legal_config_matches_the_files():
    """If this fails, run scripts/sync_legal and commit the result."""
    import importlib.machinery
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "scripts" / "sync_legal"
    if not path.exists():
        pytest.skip("the web content folder is not in this checkout")
    loader = importlib.machinery.SourceFileLoader("sync_legal", str(path))
    spec = importlib.util.spec_from_loader("sync_legal", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    stored = json.loads(legal._FILE.read_text())
    assert stored == module.build(), "legal_current.json is out of date: run scripts/sync_legal"
