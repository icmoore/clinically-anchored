"""The AI care provider summary endpoints: the model call is mocked; what the model is shown, how
its answer is checked, what is audited and saved, and how a saved one is read back without the
model are all exercised through the API against an in-memory fake."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import care_provider_summaries as module
from clinically_anchored_api.core import ai
from clinically_anchored_api.core.audit import AuditWriteError
from clinically_anchored_api.core.provider_summary import (
    MAX_CHARS,
    SummaryFormatError,
    validate_summary,
)
from clinically_anchored_api.main import app
from tests.fakes import (
    AUTH,
    CLINIC_A,
    CLINIC_B,
    EPISODE_A,
    EPISODE_B,
    PATIENT_A,
    PATIENT_B,
    USER,
    FakeSupabase,
    install,
    new_id,
    pin_clock,
)

MODEL = "test.model-v1"
URL = f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/care-provider-summary"
T0 = "2026-10-02T13:00:00+00:00"  # 09:00 in Toronto
GOOD = (
    "The patient had a colectomy on 2026-10-01 and is on post-op day 2. At check-in they reported "
    "pain of 8 out of 10 and a red flag was raised; the clinic advised keeping the wound dry."
)


@pytest.fixture
def model(monkeypatch):
    state = {"calls": [], "text": GOOD, "error": None}

    def fake_generate(**kw):
        state["calls"].append(kw)
        if state["error"]:
            raise state["error"]
        return ai.AIResult(
            text=state["text"], model_id=MODEL, prompt_version=kw["prompt_version"],
            usage=ai.TokenUsage(input_tokens=120, output_tokens=60), stop_reason="end_turn",
        )

    monkeypatch.setattr(module.ai, "generate", fake_generate)
    return state


@pytest.fixture
def fake(monkeypatch, model):
    f = FakeSupabase(role="delegate")
    f.add_episode(created_at="2026-10-01T00:00:00+00:00")
    f.add_episode(id=EPISODE_B, clinic=CLINIC_B, patient=PATIENT_B)
    pin_clock(monkeypatch)
    f.audits = install(monkeypatch, f)
    return f


@pytest.fixture
def client(fake):
    return TestClient(app)


def check_in(fake, **extra) -> dict:
    row = {
        "id": new_id(), "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "episode_id": EPISODE_A,
        "post_op_day": 1, "created_at": T0, "reviewed_at": None, "is_red_flag": True,
        "answers": {"pain": {"scale": 8, "trend": "worse"}, "concerns_text": "It hurts a lot"},
        **extra,
    }
    fake.tables["check_ins"].append(row)
    return row


def touchpoint(fake, **extra) -> dict:
    row = {
        "id": new_id(), "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "kind": "call",
        "source": "manual", "occurred_at": "2026-10-02T15:00:00+00:00", "duration_minutes": 5,
        "note": "Told her to keep it dry", "logged_by": USER,
        "created_at": "2026-10-02T15:05:00+00:00",
        **extra,
    }
    fake.tables.setdefault("touchpoints", []).append(row)
    return row


@pytest.fixture
def record(fake):
    """A check-in, two messages and a call for PATIENT_A's episode, plus things that must NOT
    reach the model."""
    check_in(fake)
    fake.add_message(sender="patient", body="The wound is red.",
                     created_at="2026-10-03T08:00:00+00:00")
    fake.add_message(sender="clinician", body="Please keep it dry.",
                     created_at="2026-10-03T08:30:00+00:00")
    touchpoint(fake)
    touchpoint(fake, patient_id=PATIENT_B, clinic_id=CLINIC_B, note="OTHER-PATIENT-NOTE")
    fake.add_message(patient=PATIENT_B, clinic=CLINIC_B, episode=EPISODE_B,
                     body="OTHER-PATIENT-MSG")
    fake.tables.setdefault("thread_summaries", []).append(
        {"id": new_id(), "clinic_id": CLINIC_A, "patient_id": PATIENT_A,
         "lines": [{"text": "CONVERSATION-SUMMARY-TEXT", "citations": []}]}
    )
    fake.tables.setdefault("patient_briefs", []).append(
        {"id": new_id(), "clinic_id": CLINIC_A, "patient_id": PATIENT_A,
         "bullets": [{"text": "PATIENT-BRIEF-TEXT"}]}
    )
    return fake


# --- generating ---------------------------------------------------------------------------


def test_returns_a_plain_paragraph_with_no_citations(client, record):
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == GOOD
    assert (body["model_id"], body["prompt_version"]) == (MODEL, "care_provider_summary_v1")
    assert body["covers"] == {"messages": 2, "check_ins": 1, "touchpoints": 1}
    assert body["new_since"] == {"messages": 0, "check_ins": 0, "touchpoints": 0}
    assert body["truncated"] is False and body["episode_id"] == EPISODE_A
    assert body["generated_at"].startswith("2026-10-03T11:30:00")
    assert "bullets" not in body


def test_the_model_is_shown_the_episodes_record_and_nothing_else(client, record, model):
    client.post(URL, headers=AUTH)
    (call,) = model["calls"]
    assert call["clinic_id"] == CLINIC_A and call["prompt_version"] == "care_provider_summary_v1"
    seen = call["system"] + call["prompt"]
    # the procedure name is shown here (the AI patient summary leaves it out)
    assert "Procedure: Colectomy. Surgery date 2026-10-01; post-op day 2." in call["prompt"]
    assert "[C1] Check-in completed 2026-10-02 09:00 (post-op day 1). Red flag: yes" in seen
    assert "[M1] patient (medication): The wound is red." in seen
    assert "[M2] clinician: Please keep it dry." in seen
    assert "[T1] call, 2026-10-02 11:00, 5 min: Told her to keep it dry" in seen
    for leak in ("OTHER-PATIENT-MSG", "OTHER-PATIENT-NOTE", "CONVERSATION-SUMMARY-TEXT",
                 "PATIENT-BRIEF-TEXT", "Pat A", "Pat B"):
        assert leak not in seen


def test_the_system_prompt_carries_the_format_and_content_rules(client, record, model):
    client.post(URL, headers=AUTH)
    system = model["calls"][0]["system"]
    assert "PLACEHOLDER" in system
    for needle in ("One paragraph", "120 words", "Never write the \"[label]\" tags",
                   "Do not diagnose", "other care provider"):
        assert needle in system


def test_it_is_audited_with_the_text_only_in_the_hashed_payload(client, record):
    body = client.post(URL, headers=AUTH).json()
    (event,) = record.audits
    assert event["event_type"] == "care_provider_summary.generated"
    assert event["clinic_id"] == CLINIC_A
    meta = event["metadata"]
    assert meta["ref_type"] == "care_provider_summary" and meta["ref_id"] == body["summary_id"]
    assert (meta["model_id"], meta["prompt_version"]) == (MODEL, "care_provider_summary_v1")
    counts = (meta["covers_messages"], meta["covers_check_ins"], meta["covers_touchpoints"])
    assert counts == (2, 1, 1)
    assert meta["actor"]["id"] == USER and meta["patient_id"] == PATIENT_A
    assert meta["input_tokens"] == 120 and meta["output_tokens"] == 60
    assert "colectomy" not in str(meta)  # no summary text in readable metadata
    assert event["payload"]["text"] == GOOD
    assert event["payload"]["episode_id"] == EPISODE_A


def test_it_is_saved_after_the_audit_write(client, record):
    body = client.post(URL, headers=AUTH).json()
    (saved,) = record.tables["care_provider_summaries"]
    assert saved["id"] == body["summary_id"] and saved["episode_id"] == EPISODE_A
    assert saved["generated_by"] == USER and saved["truncated"] is False
    assert saved["summary_text"] == GOOD
    assert saved["covers"] == {"messages": 2, "check_ins": 1, "touchpoints": 1}


# --- failures: nothing shown, nothing saved --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",  # empty
        "   \n ",
        "x" * (MAX_CHARS + 1),  # too long
        GOOD + "\n\n" + GOOD,  # two paragraphs
        "- " + GOOD,  # a bullet
        "# Summary\n" + GOOD,  # a heading
        GOOD + " [C1]",  # a record label leaked into the text
        GOOD + " [M2, T1]",
    ],
)
def test_a_summary_in_the_wrong_form_is_discarded_whole(client, record, model, text):
    model["text"] = text
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 502 and "discarded" in r.json()["detail"]
    assert record.audits == []
    assert not record.tables.get("care_provider_summaries")


def test_if_the_audit_write_fails_nothing_is_shown_or_saved(client, record, monkeypatch):
    def boom(*_a, **_k):
        raise AuditWriteError("down")

    monkeypatch.setattr(module, "record_event", boom)
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 500 and "not shown" in r.json()["detail"]
    assert not record.tables.get("care_provider_summaries")


def test_if_saving_fails_the_summary_is_still_returned(client, record):
    record.fail_insert["care_provider_summaries"] = RuntimeError("db down")
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 200 and len(record.audits) == 1
    assert not record.tables.get("care_provider_summaries")


def test_model_errors_map_to_429_and_502(client, record, model):
    model["error"] = ai.AIDailyCapExceeded(CLINIC_A, 5)
    assert client.post(URL, headers=AUTH).status_code == 429
    model["error"] = ai.AIInvocationError("bedrock down")
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 502 and "failed" in r.json()["detail"]
    assert record.audits == []


# --- preconditions and access ---------------------------------------------------------------


def test_nothing_to_summarise_without_a_check_in_or_a_message(client, fake, model):
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 422 and "no check-in and no messages" in r.json()["detail"]
    touchpoint(fake)  # a call alone is not enough
    assert client.post(URL, headers=AUTH).status_code == 422
    assert model["calls"] == []


def test_a_patient_with_no_care_period_is_a_422(client, fake, model):
    fake.tables["episodes"].clear()
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 422 and "no care period" in r.json()["detail"]
    assert model["calls"] == []


def test_same_access_rules_as_the_ai_patient_summary(client, record, model):
    assert client.post(URL, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post(URL).status_code in (401, 403)
    other = f"/clinics/{CLINIC_B}/patients/{PATIENT_B}/care-provider-summary"
    assert client.post(other, headers=AUTH).status_code == 403
    assert client.get(f"{other}/latest", headers=AUTH).status_code == 403
    wrong = f"/clinics/{CLINIC_A}/patients/{PATIENT_B}/care-provider-summary"
    assert client.post(wrong, headers=AUTH).status_code == 404
    assert client.get(f"{wrong}/latest", headers=AUTH).status_code == 404
    assert model["calls"] == []
    # any member may generate, whatever the role (the fixture's member is a delegate)
    assert client.post(URL, headers=AUTH).status_code == 200


# --- reading the saved one back, and what is new since ---------------------------------------


def test_opening_the_page_with_nothing_saved_is_null(client, record, model):
    r = client.get(f"{URL}/latest", headers=AUTH)
    assert r.status_code == 200 and r.json() is None
    assert model["calls"] == []


def test_the_newest_saved_one_loads_without_calling_the_model(client, record, model):
    shown = client.post(URL, headers=AUTH).json()
    model["calls"].clear()
    latest = client.get(f"{URL}/latest", headers=AUTH)
    assert latest.status_code == 200 and latest.json() == shown
    assert model["calls"] == []


def test_the_newest_of_several_is_the_one_read_back(client, record, model, monkeypatch):
    first = client.post(URL, headers=AUTH).json()
    pin_clock(monkeypatch, datetime(2026, 10, 3, 14, 0, tzinfo=UTC))
    model["text"] = GOOD.replace("pain of 8", "pain of eight")
    second = client.post(URL, headers=AUTH).json()
    assert second["summary_id"] != first["summary_id"]
    assert len(record.tables["care_provider_summaries"]) == 2
    latest = client.get(f"{URL}/latest", headers=AUTH).json()
    assert latest["summary_id"] == second["summary_id"] and "eight" in latest["text"]


def test_new_since_counts_what_arrived_after_it_was_generated(client, record):
    client.post(URL, headers=AUTH)  # generated at 2026-10-03T11:30Z
    after = "2026-10-03T13:00:00+00:00"
    record.add_message(sender="patient", body="n1", created_at=after)
    check_in(record, created_at=after)
    touchpoint(record, created_at=after)
    record.add_message(patient=PATIENT_B, clinic=CLINIC_B, episode=EPISODE_B, created_at=after)
    latest = client.get(f"{URL}/latest", headers=AUTH).json()
    assert latest["new_since"] == {"messages": 1, "check_ins": 1, "touchpoints": 1}
    assert latest["covers"] == {"messages": 2, "check_ins": 1, "touchpoints": 1}  # unchanged


# --- the shape check on its own ---------------------------------------------------------------


def test_validate_summary_returns_the_stripped_text():
    assert validate_summary(f"  {GOOD}\n") == GOOD
    # a single line break inside a paragraph is only wrapping
    assert validate_summary("First half,\nsecond half.") == "First half,\nsecond half."


@pytest.mark.parametrize(
    "text", ["", " ", "x" * (MAX_CHARS + 1), "a\n\nb", "* a", "1. a", "a [m3] b", "a [C12, M1] b"]
)
def test_validate_summary_rejects_the_wrong_shape(text):
    with pytest.raises(SummaryFormatError):
        validate_summary(text)
