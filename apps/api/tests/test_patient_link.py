"""The patient's episode link: signature, revocation by generation, closed episodes, and the
lazy close of an episode whose schedule ran out. Rate limits are in test_ratelimit.py."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.security import (
    InvalidLinkToken,
    create_episode_token,
    verify_episode_token,
)
from clinically_anchored_api.main import app
from tests.fakes import FakeSupabase, install, pin_clock, token_for


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSupabase()
    fake.add_episode(checkin_completed_at="2026-10-02T12:00:00+00:00")
    pin_clock(monkeypatch)
    fake.audit = install(monkeypatch, fake)
    return fake


@pytest.fixture
def client(fake):
    return TestClient(app)


def tok(fake):
    return {"token": token_for(fake.tables["episodes"][0])}


# --- the token -----------------------------------------------------------------------


def test_token_round_trips():
    assert verify_episode_token(create_episode_token(episode_id="e", generation=3)) == ("e", 3)


def test_a_tampered_or_foreign_token_is_rejected():
    good = create_episode_token(episode_id="e", generation=1)
    with pytest.raises(InvalidLinkToken):
        verify_episode_token(good[:-2] + ("AA" if not good.endswith("AA") else "BB"))
    with pytest.raises(InvalidLinkToken):
        verify_episode_token("garbage")


def test_old_style_scoped_tokens_no_longer_open_anything(client, fake):
    from itsdangerous import URLSafeTimedSerializer

    secret = get_settings().checkin_link_secret
    for scope in ("checkin", "messages"):
        old = URLSafeTimedSerializer(secret, salt=f"link:{scope}").dumps(
            {"clinic_id": "c", "patient_id": "p"}
        )
        assert client.get("/messages", params={"token": old}).status_code == 401


def test_the_token_has_only_a_long_backstop_not_a_short_expiry(monkeypatch):
    token = create_episode_token(episode_id="e", generation=1)
    settings = get_settings()
    assert settings.patient_link_backstop_max_age_seconds >= 60 * 60 * 24 * 100
    monkeypatch.setattr(settings, "patient_link_backstop_max_age_seconds", -1)
    with pytest.raises(InvalidLinkToken, match="expired"):
        verify_episode_token(token)


# --- what the server decides on every request ------------------------------------------


def test_a_valid_link_opens_the_patients_thread(client, fake):
    fake.add_message(sender="clinician", body="Welcome")
    r = client.get("/messages", params=tok(fake))
    assert r.status_code == 200 and [m["body"] for m in r.json()] == ["Welcome"]


def test_the_thread_is_only_this_episodes_messages(client, fake):
    fake.add_message(body="mine")
    fake.add_message(body="someone else", episode="other", patient="p2")
    r = client.get("/messages", params=tok(fake))
    assert [m["body"] for m in r.json()] == ["mine"]


def test_an_unknown_episode_is_a_401(client, fake):
    ghost = create_episode_token(episode_id="ee000000-0000-0000-0000-000000000000", generation=1)
    assert client.get("/messages", params={"token": ghost}).status_code == 401


def test_resetting_the_link_kills_the_old_url_and_the_new_one_works(client, fake):
    old = tok(fake)
    fake.tables["episodes"][0]["link_generation"] = 2
    r = client.get("/messages", params=old)
    assert r.status_code == 401 and "no longer valid" in r.json()["detail"]
    assert client.get("/messages", params=tok(fake)).status_code == 200


def test_a_discharged_episode_refuses_everything_but_the_state_route(client, fake):
    fake.tables["episodes"][0].update(
        closed_at="2026-10-03T00:00:00+00:00", close_reason="discharged", closed_by="u"
    )
    p = tok(fake)
    assert client.get("/messages", params=p).status_code == 410
    r = client.post("/messages", params=p, json={"body": "hi", "category": "admin"})
    assert r.status_code == 410
    assert client.get("/check-ins/context", params=p).status_code == 410
    assert client.get("/consents", params=p).status_code == 410
    state = client.get("/episode", params=p)
    assert state.status_code == 200
    assert state.json() == {
        "phase": "closed", "office_phone": "905-555-0100", "check_in_done": False,
        "categories": [], "post_op_day": None, "interval_no": None, "intervals_total": None,
        "interval_closes_at": None, "next_opens_at": None, "episode_closes_at": None,
        "timezone": None, "can_send_scheduled": False, "can_reply": False, "windows": [],
    }


def test_an_episode_whose_schedule_ran_out_is_closed_and_audited_once(client, fake, monkeypatch):
    end = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)  # 07:00 EST, surgery 2026-10-01 + 49 days
    pin_clock(monkeypatch, end + timedelta(hours=3))

    def close(params):
        assert params["p_reason"] == "schedule_complete" and params["p_closed_by"] is None
        assert params["p_closed_at"].startswith("2026-11-19T12:00:00")
        row = {**fake.tables["episodes"][0], "closed_at": params["p_closed_at"],
               "close_reason": "schedule_complete"}
        fake.tables["episodes"][0].update(
            closed_at=row["closed_at"], close_reason=row["close_reason"]
        )
        return {"episode": row, "changed": True}

    fake.rpc_handlers["close_episode"] = close
    assert client.get("/messages", params=tok(fake)).status_code == 410
    assert client.get("/messages", params=tok(fake)).status_code == 410  # now stored as closed
    assert [c["event_type"] for c in fake.audit] == ["episode.closed"]  # one event, not two
    assert fake.audit[0]["metadata"]["actor"] == {"type": "system"}
    assert fake.audit[0]["payload"]["reason"] == "schedule_complete"
    assert len([c for c in fake.rpc_calls if c[0] == "close_episode"]) == 1


def test_a_failed_settle_does_not_turn_the_closed_screen_into_an_error(client, fake, monkeypatch):
    pin_clock(monkeypatch, datetime(2026, 12, 25, 15, 0, tzinfo=UTC))

    def boom(_params):
        raise RuntimeError("db down")

    fake.rpc_handlers["close_episode"] = boom
    assert client.get("/episode", params=tok(fake)).json()["phase"] == "closed"


def test_state_route_for_an_open_episode(client, fake):
    r = client.get("/episode", params=tok(fake))
    body = r.json()
    assert body["phase"] == "open" and body["interval_no"] == 1
    assert body["check_in_done"] is True and body["can_send_scheduled"] is True
    assert body["categories"] == ["recovery_update", "symptom_or_wound", "medication", "admin"]
    assert body["timezone"] == "America/Toronto" and body["office_phone"] == "905-555-0100"
    assert body["next_opens_at"].startswith("2026-10-04T11:00:00")  # 07:00 EDT on the 4th


def test_state_route_before_the_check_in_is_locked(client, fake):
    fake.tables["episodes"][0]["checkin_completed_at"] = None
    body = client.get("/episode", params=tok(fake)).json()
    assert body["phase"] == "locked_checkin" and body["can_send_scheduled"] is False


# --- the patient's view of the message windows ------------------------------------------------

PATIENT_WINDOW_KEYS = {"number", "status", "opens_at", "closes_at"}


def windows_of(client, fake):
    return client.get("/episode", params=tok(fake)).json()["windows"]


def statuses(client, fake):
    return [w["status"] for w in windows_of(client, fake)]


def test_the_state_lists_every_window_with_only_what_a_patient_needs(client, fake):
    body = client.get("/episode", params=tok(fake)).json()
    windows = body["windows"]
    assert [w["number"] for w in windows] == list(range(1, 10))
    assert all(set(w) == PATIENT_WINDOW_KEYS for w in windows)
    assert windows[0]["status"] == "open"
    assert windows[0]["opens_at"].startswith("2026-10-03T11:00:00")  # 07:00 EDT on the 3rd
    assert windows[0]["closes_at"] == windows[1]["opens_at"]
    # the rest of the state a patient screen needs is already there
    assert body["next_opens_at"] and body["episode_closes_at"] and body["can_reply"] is False


def test_nothing_clinic_only_is_in_the_patients_state(client, fake):
    fake.tables["episodes"][0]["checkin_completed_at"] = "2026-10-03T00:00:00+00:00"
    fake.tables["episodes"][0]["surgery_date"] = "2026-09-20"  # earlier windows have passed
    raw = client.get("/episode", params=tok(fake)).text.lower()
    for clinic_only in (
        "missed", "reason", "closed_before_check_in", "no_message", "locked", "cut_short",
        "schedule_version", "version", "red_flag", "reviewed", "check_in_id", "post_op_day_",
    ):
        assert clinic_only not in raw, clinic_only
    body = client.get("/episode", params=tok(fake)).json()
    assert set(body) == {
        "phase", "office_phone", "check_in_done", "categories", "post_op_day", "interval_no",
        "intervals_total", "interval_closes_at", "next_opens_at", "episode_closes_at", "timezone",
        "can_send_scheduled", "can_reply", "windows",
    }


def test_a_window_used_is_used_and_the_open_one_is_open(client, fake, monkeypatch):
    from clinically_anchored_api.core import episodes as episodes_core

    monkeypatch.setattr(episodes_core, "_facts", lambda _db, ids: {i: ({1}, False) for i in ids})
    assert statuses(client, fake)[:2] == ["used", "upcoming"]
    assert client.get("/episode", params=tok(fake)).json()["phase"] == "used"


def test_windows_that_ended_unused_are_passed_never_missed(client, fake):
    fake.tables["episodes"][0]["surgery_date"] = "2026-09-20"  # day 13: windows 1-3 have ended
    fake.tables["episodes"][0]["checkin_completed_at"] = "2026-09-21T12:00:00+00:00"  # day 1
    got = statuses(client, fake)
    assert got[:5] == ["passed", "passed", "passed", "open", "upcoming"]


def test_windows_that_ended_before_the_check_in_are_before_check_in(client, fake):
    fake.tables["episodes"][0]["surgery_date"] = "2026-09-20"
    fake.tables["episodes"][0]["checkin_completed_at"] = "2026-09-29T12:00:00+00:00"  # day 9
    got = statuses(client, fake)
    assert got[:5] == ["before_check_in", "before_check_in", "before_check_in", "open", "upcoming"]


def test_a_check_in_inside_a_window_gives_both(client, fake):
    fake.tables["episodes"][0]["surgery_date"] = "2026-09-20"
    fake.tables["episodes"][0]["checkin_completed_at"] = "2026-09-24T12:00:00+00:00"  # day 4
    got = statuses(client, fake)  # windows open on days 2, 3, 5, 7: day 4 is inside window 2
    assert got[:4] == ["before_check_in", "passed", "passed", "open"]


def test_before_the_check_in_a_running_window_is_upcoming_not_open(client, fake):
    fake.tables["episodes"][0]["checkin_completed_at"] = None
    body = client.get("/episode", params=tok(fake)).json()
    assert body["phase"] == "locked_checkin" and body["can_send_scheduled"] is False
    assert [w["status"] for w in body["windows"]] == ["upcoming"] * 9


def test_before_the_check_in_windows_that_ended_are_before_check_in(client, fake):
    fake.tables["episodes"][0]["checkin_completed_at"] = None
    fake.tables["episodes"][0]["surgery_date"] = "2026-09-20"
    got = statuses(client, fake)
    assert got[:5] == ["before_check_in"] * 3 + ["upcoming"] * 2  # window 4 runs but is locked


def test_a_closed_episode_returns_no_windows_and_nothing_else(client, fake):
    fake.tables["episodes"][0].update(
        closed_at="2026-10-02T13:00:00+00:00", close_reason="discharged"
    )
    body = client.get("/episode", params=tok(fake)).json()
    assert body["phase"] == "closed" and body["windows"] == []
    assert body["episode_closes_at"] is None and body["next_opens_at"] is None


def test_the_reply_flag_travels_with_the_windows(client, fake, monkeypatch):
    from clinically_anchored_api.core import episodes as episodes_core

    monkeypatch.setattr(episodes_core, "_facts", lambda _db, ids: {i: (set(), True) for i in ids})
    body = client.get("/episode", params=tok(fake)).json()
    assert body["can_reply"] is True and len(body["windows"]) == 9
