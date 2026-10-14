"""Per-patient rate limits on link-token routes: the sliding window itself,
and how it's wired (verified tokens only, separate read/write budgets,
per-patient isolation, 429 + Retry-After)."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.core import ratelimit
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.fakes import EPISODE_B, PATIENT_B, FakeSupabase, install, pin_clock, token_for


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ratelimit, "_clock", clock)
    return clock


# --- the window ---------------------------------------------------------------


def test_allows_up_to_limit_then_reports_wait(clock):
    assert [ratelimit.hit(("k",), 3, 60) for _ in range(3)] == [0, 0, 0]
    assert ratelimit.hit(("k",), 3, 60) == 60
    clock.now += 45
    assert ratelimit.hit(("k",), 3, 60) == 15  # oldest hit leaves in 15s


def test_window_slides_rather_than_resetting_all_at_once(clock):
    ratelimit.hit(("k",), 2, 60)
    clock.now += 40
    ratelimit.hit(("k",), 2, 60)
    assert ratelimit.hit(("k",), 2, 60) > 0
    clock.now += 21  # first hit is now 61s old, second only 21s
    assert ratelimit.hit(("k",), 2, 60) == 0
    assert ratelimit.hit(("k",), 2, 60) > 0


def test_rejected_requests_are_not_counted(clock):
    ratelimit.hit(("k",), 1, 60)
    for _ in range(50):
        assert ratelimit.hit(("k",), 1, 60) > 0
    clock.now += 60
    assert ratelimit.hit(("k",), 1, 60) == 0  # hammering didn't extend the block


def test_keys_are_independent(clock):
    assert ratelimit.hit(("a",), 1, 60) == 0
    assert ratelimit.hit(("b",), 1, 60) == 0
    assert ratelimit.hit(("a",), 1, 60) > 0


def test_expired_keys_are_swept(clock, monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_KEYS", 5)
    for i in range(6):
        ratelimit.hit((i,), 1, 60)
    clock.now += 61
    ratelimit.hit(("fresh",), 1, 60)
    assert list(ratelimit._hits) == [("fresh",)]


# --- wiring -------------------------------------------------------------------


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.rows = [{"id": "m1", "read_at": None, "created_at": "2026-01-01T00:00:00Z", **payload}]
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


@pytest.fixture
def client(monkeypatch):
    fake = FakeSupabase()
    fake.add_episode(checkin_completed_at="2026-10-02T12:00:00+00:00")
    fake.add_episode(id=EPISODE_B, patient=PATIENT_B)
    pin_clock(monkeypatch)
    install(monkeypatch, fake)
    fake.rpc_handlers["send_patient_message"] = lambda p: {
        "id": "m", "clinic_id": p["p_clinic_id"], "patient_id": "p",
        "episode_id": p["p_episode_id"],
        "sender": "patient", "body": p["p_body"], "origin": "scheduled", "interval_no": 1,
        "category": "admin", "reply_to_message_id": None, "read_at": None,
        "created_at": "2026-10-03T11:30:00+00:00",
    }
    settings = get_settings()
    monkeypatch.setattr(settings, "patient_link_reads_per_minute", 3)
    monkeypatch.setattr(settings, "patient_link_writes_per_minute", 2)
    client = TestClient(app)
    client.fake = fake
    return client


def _tok(client, which=0):
    return token_for(client.fake.tables["episodes"][which])


def _post(client, which=0):
    return client.post(
        "/messages", params={"token": _tok(client, which)},
        json={"body": "hi", "category": "admin"},
    )


def test_reads_are_throttled_with_retry_after(client, clock):
    params = {"token": _tok(client)}
    assert [client.get("/messages", params=params).status_code for _ in range(3)] == [200] * 3
    r = client.get("/messages", params=params)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) == 60
    clock.now += 61
    assert client.get("/messages", params=params).status_code == 200


def test_writes_have_their_own_stricter_budget(client):
    codes = [_post(client).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    reads = client.get("/messages", params={"token": _tok(client)})
    assert reads.status_code == 200  # reads have their own budget


def test_budget_is_per_episode_not_global(client):
    for _ in range(3):
        client.get("/messages", params={"token": _tok(client)})
    assert client.get("/messages", params={"token": _tok(client)}).status_code == 429
    assert client.get("/messages", params={"token": _tok(client, 1)}).status_code != 429


def test_the_write_budget_is_shared_across_the_patients_routes(client):
    for _ in range(2):
        _post(client)
    r = client.post("/check-ins", params={"token": _tok(client)}, json={"answers": {}})
    assert r.status_code == 429


def test_invalid_tokens_are_not_counted(client):
    for _ in range(10):
        assert client.get("/messages", params={"token": "garbage"}).status_code == 401
    assert client.get("/messages", params={"token": _tok(client)}).status_code == 200


def test_a_link_for_a_missing_or_reset_episode_still_costs_budget_only_once_signed(client):
    stale = _tok(client)
    client.fake.tables["episodes"][0]["link_generation"] = 2
    codes = [client.get("/messages", params={"token": stale}).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]  # signed, so counted: the database isn't hammered


def test_zero_disables_a_budget(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "patient_link_reads_per_minute", 0)
    params = {"token": _tok(client)}
    assert all(client.get("/messages", params=params).status_code == 200 for _ in range(20))
