"""On-demand thread summaries: the model call is mocked; citation handling is
exercised end to end through the endpoint (the logic itself is in test_citations)."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import messages as messages_module
from clinically_anchored_api.api import summaries as summaries_module
from clinically_anchored_api.core import ai
from clinically_anchored_api.core import auth as auth_module
from clinically_anchored_api.core.security import create_link_token
from clinically_anchored_api.main import app

CLINIC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLINIC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PATIENT_A = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
PATIENT_A2 = "a2a2a2a2-a2a2-a2a2-a2a2-a2a2a2a2a2a2"
PATIENT_B = "b1b1b1b1-b1b1-b1b1-b1b1-b1b1b1b1b1b1"
USER = "99999999-9999-9999-9999-999999999999"
AUTH = {"Authorization": "Bearer good-jwt"}
MODEL = "test.model-v1"


def mid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


class _Query:
    def __init__(self, db, name):
        self.db, self.rows = db, db.tables[name]
        self._eq: list[tuple[str, object]] = []
        self._in = None
        self._order = None
        self._desc = False
        self._limit = None
        self._insert = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in = (col, set(vals))
        return self

    def order(self, col, desc=False):
        self._order, self._desc = col, desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, payload):
        self._insert = payload
        return self

    def execute(self):
        if self._insert is not None:
            row = {"id": mid(1000 + len(self.rows)), "read_at": None,
                   "created_at": f"2026-02-01T00:00:{len(self.rows):02d}Z", **self._insert}
            self.rows.append(row)
            return type("R", (), {"data": [row]})()
        rows = [dict(r) for r in self.rows if all(r.get(c) == v for c, v in self._eq)]
        if self._in:
            rows = [r for r in rows if r[self._in[0]] in self._in[1]]
        if self._order:
            rows.sort(key=lambda r: r[self._order], reverse=self._desc)
        if self._limit:
            rows = rows[: self._limit]
        return type("R", (), {"data": rows})()


class _FakeAuth:
    def get_user(self, jwt):
        if jwt != "good-jwt":
            raise ValueError("bad jwt")
        return type("UR", (), {"user": type("U", (), {"id": USER})()})()


class _FakeSupabase:
    def __init__(self):
        self.auth = _FakeAuth()
        self.tables = {
            "clinic_members": [{"clinic_id": CLINIC_A, "user_id": USER, "role": "delegate"}],
            "patients": [
                {"id": PATIENT_A, "clinic_id": CLINIC_A},
                {"id": PATIENT_A2, "clinic_id": CLINIC_A},
                {"id": PATIENT_B, "clinic_id": CLINIC_B},
            ],
            "messages": [],
        }

    def table(self, name):
        return _Query(self, name)


@pytest.fixture
def audit_calls(monkeypatch):
    calls: list[dict] = []
    recorder = lambda _db, **kw: calls.append(kw)  # noqa: E731
    monkeypatch.setattr(summaries_module, "record_event", recorder)
    monkeypatch.setattr(messages_module, "record_event", recorder)
    return calls


@pytest.fixture
def model(monkeypatch):
    state = {"calls": [], "text": "", "error": None}

    def fake_generate(**kw):
        state["calls"].append(kw)
        if state["error"]:
            raise state["error"]
        return ai.AIResult(
            text=state["text"], model_id=MODEL, prompt_version=kw["prompt_version"],
            usage=ai.TokenUsage(input_tokens=100, output_tokens=40), stop_reason="end_turn",
        )

    monkeypatch.setattr(summaries_module.ai, "generate", fake_generate)
    return state


@pytest.fixture
def fake(monkeypatch, audit_calls, model):
    fake = _FakeSupabase()
    for module in (summaries_module, messages_module, auth_module):
        monkeypatch.setattr(module, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def client(fake):
    return TestClient(app)


def _add(fake, n, sender, body, patient=PATIENT_A, clinic=CLINIC_A):
    fake.tables["messages"].append({
        "id": mid(n), "clinic_id": clinic, "patient_id": patient, "sender": sender, "body": body,
        "read_at": None, "created_at": f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}Z",
    })


@pytest.fixture
def thread(fake):
    _add(fake, 1, "patient", "My incision is red.\nAnd it itches.")
    _add(fake, 2, "clinician", "How is the pain?")
    _add(fake, 3, "patient", "Pain is better.")
    _add(fake, 90, "patient", "Another patient's message", patient=PATIENT_A2)
    return fake


def _url(patient=PATIENT_A, clinic=CLINIC_A):
    return f"/clinics/{clinic}/patients/{patient}/summaries"


# The thread fixture is messages 1-3 of PATIENT_A, shown to the model as M1-M3.
GOOD = "- Reports a red, itchy incision. [M1]\n- Pain is improving. [M3]"


# --- happy path ------------------------------------------------------------------


def test_returns_a_verified_cited_summary(client, thread, model):
    model["text"] = GOOD
    r = client.post(_url(), headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["lines"] == [
        {"text": "Reports a red, itchy incision.", "citations": [mid(1)]},
        {"text": "Pain is improving.", "citations": [mid(3)]},
    ]
    assert (body["model_id"], body["prompt_version"]) == (MODEL, "summary_v3")
    assert body["covers_messages"] == 3 and body["truncated"] is False
    assert body["patient_id"] == PATIENT_A and body["summary_id"]


def test_prompt_lists_the_patients_thread_only_one_message_per_line(client, thread, model):
    model["text"] = GOOD
    client.post(_url(), headers=AUTH)
    call = model["calls"][0]
    assert call["clinic_id"] == CLINIC_A and call["prompt_version"] == "summary_v3"
    assert "[M1] patient: My incision is red. And it itches." in call["prompt"]
    assert "[M2] clinician: How is the pain?" in call["prompt"]
    assert mid(1) not in call["prompt"]  # the model sees short labels, not full message ids
    assert "Another patient's message" not in call["prompt"]
    assert "{{" not in call["system"] + call["prompt"]


def test_audited_with_model_prompt_version_and_hashed_content(client, thread, model, audit_calls):
    model["text"] = GOOD
    body = client.post(_url(), headers=AUTH).json()
    assert len(audit_calls) == 1
    call = audit_calls[0]
    assert call["event_type"] == "summary.generated" and call["clinic_id"] == CLINIC_A
    assert call["metadata"] == {
        "ref_type": "summary", "ref_id": body["summary_id"], "patient_id": PATIENT_A,
        "actor": {"type": "member", "id": USER, "role": "delegate"},
        "model_id": MODEL, "prompt_version": "summary_v3", "covers_messages": 3,
        "input_tokens": 100, "output_tokens": 40,
    }
    assert call["payload"]["lines"][0]["citations"] == [mid(1)]
    assert "incision" not in str(call["metadata"])  # content only in the hashed payload


# --- unverifiable summaries are a hard failure -------------------------------------


def _assert_discarded(response, audit_calls):
    assert response.status_code == 502
    assert "could not be verified" in response.json()["detail"]
    assert "lines" not in response.json()  # nothing was returned
    assert audit_calls == []  # and nothing was recorded as generated


def test_citation_to_a_nonexistent_message_discards_the_summary(
    client, thread, model, audit_calls
):
    model["text"] = "- Real. [M1]\n- Invented. [M77]"
    _assert_discarded(client.post(_url(), headers=AUTH), audit_calls)


def test_citation_to_another_patients_message_discards_the_summary(
    client, thread, model, audit_calls
):
    # A real message id, but in another patient's thread -- and not a label it was given.
    model["text"] = f"- Leaks. [{mid(90)}]"
    r = client.post(_url(), headers=AUTH)
    _assert_discarded(r, audit_calls)
    assert "Another patient" not in r.text


def test_citation_to_another_clinics_message_discards_the_summary(
    client, thread, model, audit_calls
):
    _add(thread, 91, "patient", "Other clinic's message", patient=PATIENT_B, clinic=CLINIC_B)
    model["text"] = f"- Leaks. [{mid(91)}]"
    _assert_discarded(client.post(_url(), headers=AUTH), audit_calls)


@pytest.mark.parametrize(
    "text",
    [
        "- A claim with no citation at all",
        "Here is your summary:\n- Pain is improving. [M3]",
        f"- Cites a raw message id, not a label. [{mid(3)}]",
        "- Cites a label that was never shown. [M4]",
        "- Empty citation. []\n- Fine. [M1]",
        "",
    ],
)
def test_malformed_output_discards_the_summary(client, thread, model, audit_calls, text):
    model["text"] = text
    _assert_discarded(client.post(_url(), headers=AUTH), audit_calls)


def test_one_bad_citation_does_not_get_silently_dropped(client, thread, model, audit_calls):
    model["text"] = "- Good. [M1]\n- Mixed. [M3, M77]\n- Good. [M2]"
    _assert_discarded(client.post(_url(), headers=AUTH), audit_calls)


def test_if_the_audit_write_fails_the_summary_is_not_shown(client, thread, model, monkeypatch):
    model["text"] = GOOD

    def boom(_db, **_kw):
        raise summaries_module.AuditWriteError("down")

    monkeypatch.setattr(summaries_module, "record_event", boom)
    r = client.post(_url(), headers=AUTH)
    assert r.status_code == 500 and "lines" not in r.json()
    assert "not shown" in r.json()["detail"]


# --- model and request failures ----------------------------------------------------


def test_daily_cap_is_a_429(client, thread, model, audit_calls):
    model["error"] = ai.AIDailyCapExceeded(CLINIC_A, 200)
    r = client.post(_url(), headers=AUTH)
    assert r.status_code == 429 and "cap" in r.json()["detail"]
    assert audit_calls == []


def test_model_failure_is_a_502(client, thread, model, audit_calls):
    model["error"] = ai.AIInvocationError("boom")
    assert client.post(_url(), headers=AUTH).status_code == 502
    assert audit_calls == []


def test_empty_thread_is_a_422_and_the_model_is_not_called(client, fake, model):
    r = client.post(_url(), headers=AUTH)
    assert r.status_code == 422
    assert model["calls"] == []


def test_a_long_thread_is_truncated_to_the_most_recent_and_says_so(client, fake, model):
    for n in range(1, 61):
        _add(fake, n, "patient", f"message number {n}")
    model["text"] = "- Latest. [M50]"  # the 50-message window is M1-M50; M50 is message 60
    body = client.post(_url(), headers=AUTH).json()
    assert body["covers_messages"] == 50 and body["truncated"] is True
    prompt = model["calls"][0]["prompt"]
    assert "message number 60" in prompt and "message number 11" in prompt
    assert "message number 10\n" not in prompt and prompt.count("] patient:") == 50
    assert "[M1] patient: message number 11" in prompt
    assert "[M50] patient: message number 60" in prompt


def test_labels_resolve_to_the_right_messages_and_are_case_insensitive(client, fake, model):
    for n in range(1, 61):
        _add(fake, n, "patient", f"message number {n}")
    model["text"] = "- Earliest in the window. [m1]\n- Latest. [M50]"
    body = client.post(_url(), headers=AUTH).json()
    assert [ln["citations"] for ln in body["lines"]] == [[mid(11)], [mid(60)]]


def test_a_message_outside_the_window_cannot_be_cited(client, fake, model, audit_calls):
    # The model is only shown (and so can only cite) the window. Neither a label past it nor the
    # full id of an older real message is accepted.
    for n in range(1, 61):
        _add(fake, n, "patient", f"message number {n}")
    model["text"] = "- Beyond the window. [M51]"
    _assert_discarded(client.post(_url(), headers=AUTH), audit_calls)
    model["text"] = f"- Old but real. [{mid(5)}]"
    _assert_discarded(client.post(_url(), headers=AUTH), audit_calls)


# --- access, and "only on demand" -----------------------------------------------------


def test_requires_membership_and_the_right_clinic_and_patient(client, thread):
    assert client.post(_url()).status_code == 401
    assert client.post(_url(), headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post(_url(patient=PATIENT_B, clinic=CLINIC_B), headers=AUTH).status_code == 403
    assert client.post(_url(patient=PATIENT_B), headers=AUTH).status_code == 404


def test_new_messages_never_trigger_a_summary(client, fake, model, audit_calls):
    # Messages arriving from either side must not call the model or write summary events.
    token = create_link_token(clinic_id=CLINIC_A, patient_id=PATIENT_A, scope="messages")
    r = client.post("/messages", params={"token": token}, json={"body": "new message"})
    assert r.status_code == 200
    fake.tables["clinic_members"][0]["role"] = "clinician"
    r = client.post(f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/messages", headers=AUTH,
                    json={"body": "reply"})
    assert r.status_code == 200
    assert model["calls"] == []
    assert all(c["event_type"] != "summary.generated" for c in audit_calls)


def test_the_only_route_that_summarises_is_the_explicit_post():
    paths = app.openapi()["paths"]
    summary_routes = {p: sorted(ops) for p, ops in paths.items() if "summar" in p}
    assert summary_routes == {"/clinics/{clinic_id}/patients/{patient_id}/summaries": ["post"]}
