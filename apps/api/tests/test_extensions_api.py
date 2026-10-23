"""Follow-up extensions through the api, against the in-memory fake: what the patient's link allows
at each stage, the request, the clinician's decision, roles, audit order, the queue and the
episode screen's data. What the database itself enforces is in
tests/db/test_follow_up_extensions.py.
Numbers (30 days, six windows) and wording are placeholders pending the surgeon."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.main import app
from tests.fakes import (
    AUTH,
    CLINIC_A,
    CLINIC_B,
    EPISODE_A,
    PATIENT_A,
    USER,
    FakeSupabase,
    install,
    new_id,
    pin_clock,
    token_for,
)

A = CLINIC_A
# Surgery 2026-10-01 on schedule v1 ends on day 49: Thursday 19 November 07:00 Toronto (12:00 UTC).
ENDED = "2026-11-19T12:00:00+00:00"
DAY_55 = datetime(2026, 11, 25, 15, 0, tzinfo=UTC)  # six days after it ended
REASON = "Still sore when I lift things."
CHILD = "e3e3e3e3-e3e3-e3e3-e3e3-e3e3e3e3e3e3"
REQ = "f1f1f1f1-f1f1-f1f1-f1f1-f1f1f1f1f1f1"


def at(days: float) -> datetime:
    return datetime.fromisoformat(ENDED) + timedelta(days=days)


class World:
    """A FakeSupabase with an episode that ended by schedule, rpc handlers that behave like
    migration 16's functions closely enough for the api's follow-up reads, and an event log in
    which audit events and database calls appear in the order they happened."""

    def __init__(self, monkeypatch, role="clinician", now=DAY_55, ended=True):
        self.fake = FakeSupabase(role=role)
        self.episode = self.fake.add_episode(
            checkin_completed_at="2026-10-02T12:00:00+00:00",
            **({"closed_at": ENDED, "close_reason": "schedule_complete"} if ended else {}),
        )
        pin_clock(monkeypatch, now)
        self.log = install(monkeypatch, self.fake)
        self.fake.rpc_handlers["request_extension"] = self._request
        self.fake.rpc_handlers["decide_extension"] = self._decide
        self.fail_rpc: Exception | None = None
        self.client = TestClient(app)

    @property
    def token(self) -> dict:
        return {"token": token_for(self.episode)}

    # --- stand-ins for the database functions -------------------------------------------
    def _request(self, p):
        self.log.append({"event_type": "rpc:request_extension"})
        if self.fail_rpc:
            raise self.fail_rpc
        msg = self.fake.add_message(
            id=p["p_message_id"], origin="extension_request", episode=p["p_episode_id"],
            body=p["p_reason"], created_at="2026-11-25T15:00:00+00:00",
        )
        req = {
            "id": p["p_request_id"], "clinic_id": A, "patient_id": PATIENT_A,
            "episode_id": p["p_episode_id"], "schedule_version": p["p_schedule_version"],
            "request_message_id": msg["id"], "requested_at": "2026-11-25T15:00:00+00:00",
            "status": "pending", "decided_by": None, "decided_at": None,
            "decision_message": None, "decision_entry_id": None, "extension_episode_id": None,
        }
        self.fake.tables["extension_requests"].append(req)
        return {"request": req, "message": msg}

    def _decide(self, p):
        self.log.append({"event_type": "rpc:decide_extension"})
        if self.fail_rpc:
            raise self.fail_rpc
        req = next(
            r for r in self.fake.tables["extension_requests"] if r["id"] == p["p_request_id"]
        )
        entry = self.fake.add_message(
            id=p["p_entry_id"], sender="clinician", origin="extension_decision",
            episode=req["episode_id"], body=p["p_entry_body"], created_at=DAY_55.isoformat(),
        )
        child = None
        if p["p_decision"] == "accepted":
            child = self.fake.add_episode(
                id=p["p_episode_id"], schedule_version=3, kind="extension",
                extends_episode_id=req["episode_id"], started_at=DAY_55.isoformat(),
                checkin_completed_at=DAY_55.isoformat(), created_at=DAY_55.isoformat(),
            )
        req.update(
            status=p["p_decision"], decided_by=p["p_decided_by"], decided_at=DAY_55.isoformat(),
            decision_message=p["p_message"], decision_entry_id=entry["id"],
            extension_episode_id=child["id"] if child else None,
        )
        return {"request": dict(req), "message": entry, "episode": child}

    def add_request(self, status="pending", **extra) -> dict:
        msg = self.fake.add_message(
            origin="extension_request", episode=EPISODE_A, body=REASON,
            created_at="2026-11-21T15:00:00+00:00",
        )
        req = {
            "id": REQ, "clinic_id": A, "patient_id": PATIENT_A, "episode_id": EPISODE_A,
            "schedule_version": 3, "request_message_id": msg["id"],
            "requested_at": "2026-11-21T15:00:00+00:00", "status": status, "decided_by": None,
            "decided_at": None, "decision_message": None, "decision_entry_id": None,
            "extension_episode_id": None, **extra,
        }
        self.fake.tables["extension_requests"].append(req)
        return req


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def events(world):
    return [e["event_type"] for e in world.log]


# --- the patient's link, after the term ended -----------------------------------------------


def test_an_ended_episode_in_its_window_shows_the_request_form_data_and_nothing_else(world):
    state = world.client.get("/episode", params=world.token).json()
    assert state["phase"] == "closed" and state["ended_at"] == "2026-11-19T12:00:00+00:00"
    assert state["timezone"] == "America/Toronto"  # the dates on the ended screen are written in it
    assert state["extension"] == {
        "standing": "eligible", "request_by": "2026-12-19T12:00:00+00:00", "reason_max": 500,
        "requested_at": None, "decision_message": None, "extension_no": 0,
    }
    assert state["office_phone"] == "905-555-0100"
    assert state["windows"] == [] and not state["can_send_scheduled"]
    for path in ("/messages", "/check-ins/context", "/consents"):
        assert world.client.get(path, params=world.token).status_code == 410
    assert world.client.post(
        "/messages", params=world.token, json={"body": "hi", "category": "admin"}
    ).status_code == 410


def test_after_the_window_with_no_request_the_link_says_only_closed(monkeypatch):
    w = World(monkeypatch, now=at(30) + timedelta(minutes=1))
    state = w.client.get("/episode", params=w.token).json()
    assert state["phase"] == "closed" and state["extension"] is None and state["ended_at"] is None
    r = w.client.post("/extension-request", params=w.token, json={"reason": REASON})
    assert r.status_code == 410 and "rpc:request_extension" not in events(w)


def test_the_window_is_still_open_on_day_29(monkeypatch):
    w = World(monkeypatch, now=at(29.9))
    assert w.client.get("/episode", params=w.token).json()["extension"]["standing"] == "eligible"


def test_a_term_that_is_still_running_cannot_ask(monkeypatch):
    w = World(monkeypatch, now=at(-1), ended=False)
    r = w.client.post("/extension-request", params=w.token, json={"reason": REASON})
    assert r.status_code == 410
    assert w.client.get("/episode", params=w.token).json()["extension"] is None


def test_a_discharged_episode_cannot_ask_and_shows_only_closed(monkeypatch):
    w = World(monkeypatch, ended=False)
    w.episode.update(closed_at="2026-11-10T12:00:00+00:00", close_reason="discharged",
                     closed_by=USER)
    state = w.client.get("/episode", params=w.token).json()
    assert state["phase"] == "closed" and state["extension"] is None
    assert w.client.post(
        "/extension-request", params=w.token, json={"reason": REASON}
    ).status_code == 410


# --- asking ----------------------------------------------------------------------------------


def test_asking_audits_first_then_saves_and_returns_pending(world):
    r = world.client.post("/extension-request", params=world.token, json={"reason": f"  {REASON} "})
    assert r.status_code == 200
    body = r.json()
    assert body["standing"] == "pending" and body["reason_max"] == 500
    assert events(world) == ["extension.requested", "rpc:request_extension"]  # audit FIRST
    audit = world.log[0]
    assert audit["clinic_id"] == A
    assert audit["payload"]["reason"] == REASON and audit["payload"]["episode_id"] == EPISODE_A
    meta = audit["metadata"]
    assert meta["ref_type"] == "extension_request" and meta["actor"]["type"] == "patient"
    call = world.fake.rpc_calls[0][1]
    assert call["p_request_id"] == meta["ref_id"] and call["p_reason"] == REASON
    assert call["p_schedule_version"] == 3 and call["p_expires_at"] == "2026-12-19T12:00:00+00:00"
    # Neither the reason text nor the clinician's name is in the readable metadata.
    assert REASON not in str(meta)
    # And the patient now sees "pending" with no way to ask again.
    state = world.client.get("/episode", params=world.token).json()
    assert state["extension"]["standing"] == "pending"
    again = world.client.post("/extension-request", params=world.token, json={"reason": REASON})
    assert again.status_code == 409


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_the_reason_is_required_and_limited(world, reason):
    r = world.client.post("/extension-request", params=world.token, json={"reason": reason})
    assert r.status_code == 422 and events(world) == []


def test_exactly_500_characters_is_allowed(world):
    r = world.client.post("/extension-request", params=world.token, json={"reason": "x" * 500})
    assert r.status_code == 200


def test_if_the_audit_cannot_be_written_nothing_is_saved(world, monkeypatch):
    from clinically_anchored_api.api import extensions
    from clinically_anchored_api.core.audit import AuditWriteError

    def boom(*_a, **_k):
        raise AuditWriteError("down")

    monkeypatch.setattr(extensions, "record_event", boom)
    r = world.client.post("/extension-request", params=world.token, json={"reason": REASON})
    assert r.status_code == 500 and world.fake.rpc_calls == []
    assert world.fake.tables["extension_requests"] == []


@pytest.mark.parametrize(
    ("code", "status"),
    [("extension_already_requested", 409), ("extension_window_over", 410),
     ("extension_not_eligible", 409), ("extension_term_running", 409),
     ("extension_reason_invalid", 422)],
)
def test_the_databases_refusals_become_plain_statuses(world, code, status):
    world.fail_rpc = RuntimeError(code)
    r = world.client.post("/extension-request", params=world.token, json={"reason": REASON})
    assert r.status_code == status


def test_patient_state_after_a_decline_shows_the_message_and_no_form(world):
    world.add_request(status="declined", decided_by=USER, decided_at=at(8).isoformat(),
                      decision_message="We do not need to see you further.",
                      decision_entry_id=new_id())
    state = world.client.get("/episode", params=world.token).json()
    assert state["extension"]["standing"] == "declined"
    assert state["extension"]["decision_message"] == "We do not need to see you further."
    assert world.client.post(
        "/extension-request", params=world.token, json={"reason": REASON}
    ).status_code == 409


def test_a_decline_stays_readable_past_day_30_when_it_came_late(monkeypatch):
    w = World(monkeypatch, now=at(45))
    w.add_request(status="declined", decided_by=USER, decided_at=at(40).isoformat(),
                  decision_entry_id=new_id())
    assert w.client.get("/episode", params=w.token).json()["extension"]["standing"] == "declined"
    later = World(monkeypatch, now=at(71))
    later.add_request(status="declined", decided_by=USER, decided_at=at(40).isoformat(),
                      decision_entry_id=new_id())
    assert later.client.get("/episode", params=later.token).json()["extension"] is None


def test_a_pending_request_keeps_working_long_after_day_30(monkeypatch):
    w = World(monkeypatch, now=at(120))
    w.add_request()
    assert w.client.get("/episode", params=w.token).json()["extension"]["standing"] == "pending"


# --- an accepted extension -----------------------------------------------------------------------


def accepted_world(monkeypatch):
    w = World(monkeypatch, now=DAY_55 + timedelta(days=9))
    w.add_request(status="accepted", decided_by=USER, decided_at=DAY_55.isoformat(),
                  decision_message="Happy to follow you a little longer.",
                  decision_entry_id=new_id(), extension_episode_id=CHILD)
    w.fake.add_episode(
        id=CHILD, schedule_version=3, kind="extension", extends_episode_id=EPISODE_A,
        started_at=DAY_55.isoformat(), checkin_completed_at=DAY_55.isoformat(),
        created_at=DAY_55.isoformat(),
    )
    return w


def test_the_original_link_opens_the_extension_and_messaging_works_normally(monkeypatch):
    w = accepted_world(monkeypatch)
    state = w.client.get("/episode", params=w.token).json()
    assert state["phase"] == "open" and state["intervals_total"] == 6
    assert state["interval_no"] == 2 and state["can_send_scheduled"]
    assert state["extension"]["standing"] == "accepted"
    assert state["extension"]["decision_message"] == "Happy to follow you a little longer."
    assert state["extension"]["extension_no"] == 1
    assert [x["status"] for x in state["windows"]] == [
        "passed", "open", "upcoming", "upcoming", "upcoming", "upcoming"
    ]
    assert w.client.get("/messages", params=w.token).status_code == 200
    sent = []
    w.fake.rpc_handlers["send_patient_message"] = lambda p: sent.append(p) or {
        **w.fake.add_message(episode=CHILD, interval_no=p["p_interval_no"]),
    }
    r = w.client.post("/messages", params=w.token, json={"body": "hi", "category": "admin"})
    assert r.status_code == 200 and sent[0]["p_episode_id"] == CHILD
    assert sent[0]["p_interval_no"] == 2  # the extension's window 2, worked out here


def test_resetting_the_link_on_the_extension_revokes_the_original_url(monkeypatch):
    w = accepted_world(monkeypatch)
    child = w.fake.tables["episodes"][-1]
    original = w.token
    child["link_generation"] = 2
    assert w.client.get("/episode", params=original).status_code == 401
    assert w.client.get("/episode", params={"token": token_for(child)}).status_code == 200


def test_when_the_extension_ends_the_patient_may_ask_again(monkeypatch):
    end = DAY_55 + timedelta(days=42, hours=6)  # day 42 after 25 Nov, 07:00 Toronto
    w = accepted_world(monkeypatch)
    child = w.fake.tables["episodes"][-1]
    child.update(closed_at=end.isoformat(), close_reason="schedule_complete")
    pin_clock(monkeypatch, end + timedelta(days=3))
    state = w.client.get("/episode", params=w.token).json()
    assert state["phase"] == "closed"
    assert state["extension"]["standing"] == "eligible" and state["extension"]["extension_no"] == 1
    assert w.client.post(
        "/extension-request", params=w.token, json={"reason": REASON}
    ).status_code == 200
    assert world_call(w)["p_episode_id"] == CHILD  # about the extension, not the first episode


def world_call(w):
    return next(c for n, c in w.fake.rpc_calls if n == "request_extension")


def test_a_discharge_during_an_extension_kills_the_link(monkeypatch):
    w = accepted_world(monkeypatch)
    w.fake.tables["episodes"][-1].update(
        closed_at=(DAY_55 + timedelta(days=3)).isoformat(), close_reason="discharged",
        closed_by=USER,
    )
    state = w.client.get("/episode", params=w.token).json()
    assert state["phase"] == "closed" and state["extension"] is None
    assert w.client.get("/messages", params=w.token).status_code == 410
    assert w.client.post(
        "/extension-request", params=w.token, json={"reason": REASON}
    ).status_code == 410


# --- the clinician decides ---------------------------------------------------------------------


def decide(w, how, body=None, request_id=REQ, clinic=A):
    return w.client.post(
        f"/clinics/{clinic}/extension-requests/{request_id}/{how}", headers=AUTH, json=body
    )


def test_accepting_audits_first_creates_the_extension_and_audits_its_episode(world):
    world.add_request()
    r = decide(world, "accept", {"message": "  Happy to.  "})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "accepted" and body["decision_message"] == "Happy to."
    assert body["reason"] == REASON and body["extension_episode_id"]
    assert events(world) == ["extension.accepted", "rpc:decide_extension", "episode.created"]
    audit = world.log[0]
    assert audit["metadata"]["ref_type"] == "extension_request"
    assert audit["metadata"]["ref_id"] == REQ
    assert audit["payload"]["message"] == "Happy to."
    assert audit["metadata"]["actor"] == {"type": "member", "id": USER, "role": "clinician"}
    call = world.fake.rpc_calls[0][1]
    assert call["p_decision"] == "accepted" and call["p_entry_body"] == (
        "Extension request accepted. Happy to."
    )
    created = world.log[2]
    assert created["payload"]["extends_episode_id"] == EPISODE_A
    assert created["metadata"]["ref_id"] == call["p_episode_id"]
    # The thread entry is its own kind, written by the clinician, and grants no reply.
    entry = next(m for m in world.fake.tables["messages"] if m["origin"] == "extension_decision")
    assert entry["sender"] == "clinician"
    assert entry["body"].startswith("Extension request accepted.")


@pytest.mark.parametrize("body", [None, {}, {"message": "   "}])
def test_the_message_is_optional(monkeypatch, body):
    w = World(monkeypatch)
    w.add_request()
    assert decide(w, "accept", body).status_code == 200
    call = w.fake.rpc_calls[-1][1]
    assert call["p_message"] is None and call["p_entry_body"] == "Extension request accepted."


def test_declining_audits_first_and_creates_no_episode(world):
    world.add_request()
    r = decide(world, "decline", {"message": "Please call the office."})
    assert r.status_code == 200 and r.json()["status"] == "declined"
    assert events(world) == ["extension.declined", "rpc:decide_extension"]
    assert world.log[0]["payload"]["extension_episode_id"] is None
    assert len(world.fake.tables["episodes"]) == 1
    entry = next(m for m in world.fake.tables["messages"] if m["origin"] == "extension_decision")
    assert entry["body"] == "Extension request declined. Please call the office."


def test_a_decline_does_not_grant_the_patient_a_reply(world):
    from clinically_anchored_api.core import episodes as episodes_core

    world.add_request()
    decide(world, "decline", {"message": "Please call the office."})
    used, reply = episodes_core._facts(world.fake, [EPISODE_A])[EPISODE_A]
    assert used == set() and reply is False


def test_only_owners_and_clinicians_decide(monkeypatch):
    for role, status in (("owner", 200), ("clinician", 200), ("delegate", 403)):
        w = World(monkeypatch, role=role)
        w.add_request()
        assert decide(w, "accept").status_code == status, role
        w2 = World(monkeypatch, role=role)
        w2.add_request()
        assert decide(w2, "decline").status_code == status, role
        if status == 403:
            assert w.fake.rpc_calls == [] and events(w) == []


def test_a_request_of_another_clinic_is_a_404(world):
    world.add_request()
    world.fake.tables["clinic_members"].append(
        {"clinic_id": CLINIC_B, "user_id": USER, "role": "clinician"}
    )
    assert decide(world, "accept", clinic=CLINIC_B).status_code == 404
    assert decide(world, "accept", request_id=new_id()).status_code == 404
    assert world.fake.rpc_calls == []


def test_an_already_decided_request_is_a_409(world):
    world.add_request(status="declined", decided_by=USER, decided_at=at(8).isoformat(),
                      decision_entry_id=new_id())
    assert decide(world, "accept").status_code == 409
    assert decide(world, "decline").status_code == 409
    assert world.fake.rpc_calls == [] and events(world) == []


def test_a_newer_care_period_blocks_acceptance_but_not_a_decline(world):
    world.add_request()
    world.fake.add_episode(id=new_id(), created_at="2026-11-24T00:00:00+00:00",
                           surgery_date="2026-11-20")
    assert decide(world, "accept").status_code == 409 and world.fake.rpc_calls == []
    assert decide(world, "decline").status_code == 200


def test_the_database_losing_a_race_is_reported_not_hidden(world):
    world.add_request()
    world.fail_rpc = RuntimeError("extension_not_pending")
    assert decide(world, "accept").status_code == 409
    world.fail_rpc = RuntimeError("extension_superseded")
    assert decide(world, "accept").status_code == 409


def test_an_unaudited_decision_is_not_saved(world, monkeypatch):
    from clinically_anchored_api.api import extensions
    from clinically_anchored_api.core.audit import AuditWriteError

    world.add_request()

    def boom(*_a, **_k):
        raise AuditWriteError("down")

    monkeypatch.setattr(extensions, "record_event", boom)
    assert decide(world, "accept").status_code == 500
    assert world.fake.rpc_calls == []


def test_the_message_is_length_limited(world):
    world.add_request()
    assert decide(world, "accept", {"message": "x" * 5001}).status_code == 422


# --- what the clinic's screens read ---------------------------------------------------------------


def test_the_episode_carries_the_pending_request_with_its_reason(world):
    world.add_request()
    body = world.client.get(
        f"/clinics/{A}/patients/{PATIENT_A}/episode", headers=AUTH
    ).json()
    ext = body["extension"]
    assert ext["number"] == 0 and ext["can_request"] is False  # it has been asked already
    assert ext["request"]["status"] == "pending" and ext["request"]["reason"] == REASON
    assert ext["request"]["requested_at"].startswith("2026-11-21")
    assert ext["request_by"] == "2026-12-19T12:00:00+00:00"


def test_the_extension_episode_shows_nine_windows_before_its_six(monkeypatch):
    w = accepted_world(monkeypatch)
    body = w.client.get(f"/clinics/{A}/patients/{PATIENT_A}/episode", headers=AUTH).json()
    assert body["id"] == CHILD and body["extension"]["number"] == 1
    assert body["extension"]["extends_episode_id"] == EPISODE_A
    earlier = body["extension"]["earlier_windows"]
    assert [x["number"] for x in earlier] == list(range(1, 10))
    assert {x["extension_no"] for x in earlier} == {0}
    state = body["state"]
    assert [x["number"] for x in state["windows"]] == [1, 2, 3, 4, 5, 6]
    assert {x["extension_no"] for x in state["windows"]} == {1}
    assert state["interval_no"] == 2 and state["intervals_total"] == 6


def test_the_queue_flags_a_pending_request_and_does_not_count_it_as_unread(world):
    world.add_request()
    queue = world.client.get(f"/clinics/{A}/queue", headers=AUTH).json()
    assert len(queue) == 1
    item = queue[0]
    assert item["patient_id"] == PATIENT_A and item["extension_requested"] is True
    assert item["unread_messages"] == 0 and item["unread_categories"] == []


def test_a_decided_request_leaves_the_queue(world):
    world.add_request(status="declined", decided_by=USER, decided_at=at(8).isoformat(),
                      decision_entry_id=new_id())
    assert world.client.get(f"/clinics/{A}/queue", headers=AUTH).json() == []


def test_the_request_entry_cannot_be_marked_read_and_never_gets_a_read_event(world):
    world.add_request()
    entry = next(m for m in world.fake.tables["messages"] if m["origin"] == "extension_request")
    r = world.client.post(f"/clinics/{A}/messages/{entry['id']}/read", headers=AUTH)
    assert r.status_code == 200 and r.json()["read_at"] is None
    assert events(world) == [] and entry["read_at"] is None


def test_the_thread_lists_the_new_entry_kinds(world):
    world.add_request()
    decide(world, "decline", {"message": "Please call."})
    thread = world.client.get(f"/clinics/{A}/patients/{PATIENT_A}/messages", headers=AUTH).json()
    assert [m["origin"] for m in thread] == ["extension_request", "extension_decision"]
    assert [m["sender"] for m in thread] == ["patient", "clinician"]


def test_with_the_feature_switched_off_the_link_is_the_plain_closed_screen(world):
    next(r for r in world.fake.tables["message_schedules"] if r["version"] == 3)["active"] = False
    state = world.client.get("/episode", params=world.token).json()
    assert state["phase"] == "closed" and state["extension"] is None
    r = world.client.post("/extension-request", params=world.token, json={"reason": REASON})
    assert r.status_code == 410 and world.fake.rpc_calls == []


def test_a_request_already_made_survives_the_feature_being_switched_off(world):
    world.add_request()
    next(r for r in world.fake.tables["message_schedules"] if r["version"] == 3)["active"] = False
    state = world.client.get("/episode", params=world.token).json()
    assert state["extension"]["standing"] == "pending"  # it keeps the version it was made under
