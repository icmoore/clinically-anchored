"""The AI patient summary endpoints: the model call is mocked; what the model is shown, how its
answer is verified, what is audited and saved, and how a saved one is read back without the model
are all exercised through the API against an in-memory fake."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api import briefs as briefs_module
from clinically_anchored_api.core import ai
from clinically_anchored_api.core.audit import AuditWriteError
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
URL = f"/clinics/{CLINIC_A}/patients/{PATIENT_A}/patient-brief"
CONCERN = "I am worried about the swelling\nand the redness"
T0 = "2026-10-02T13:00:00+00:00"  # 09:00 in Toronto


@pytest.fixture
def model(monkeypatch):
    state = {"calls": [], "text": "", "error": None}

    def fake_generate(**kw):
        state["calls"].append(kw)
        if state["error"]:
            raise state["error"]
        return ai.AIResult(
            text=state["text"], model_id=MODEL, prompt_version=kw["prompt_version"],
            usage=ai.TokenUsage(input_tokens=120, output_tokens=60), stop_reason="end_turn",
        )

    monkeypatch.setattr(briefs_module.ai, "generate", fake_generate)
    return state


@pytest.fixture
def fake(monkeypatch, model):
    f = FakeSupabase(role="delegate")
    f.add_episode(created_at="2026-10-01T00:00:00+00:00")
    f.add_episode(id=EPISODE_B, clinic=CLINIC_B, patient=PATIENT_B)
    pin_clock(monkeypatch)
    f.audits = install(monkeypatch, f)
    f.ids = {}
    return f


@pytest.fixture
def client(fake):
    return TestClient(app)


def check_in(fake, **extra) -> dict:
    row = {
        "id": new_id(), "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "episode_id": EPISODE_A,
        "post_op_day": 1, "created_at": T0, "reviewed_at": None, "is_red_flag": True,
        "answers": {"pain": {"scale": 8, "trend": "worse"}, "concerns_text": CONCERN},
        **extra,
    }
    fake.tables["check_ins"].append(row)
    return row


def touchpoint(fake, **extra) -> dict:
    row = {
        "id": new_id(), "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "kind": "call",
        "source": "manual", "occurred_at": "2026-10-02T15:00:00+00:00", "duration_minutes": 5,
        "note": "Told her\nto keep it dry", "logged_by": USER,
        "created_at": "2026-10-02T15:05:00+00:00",
        **extra,
    }
    fake.tables.setdefault("touchpoints", []).append(row)
    return row


@pytest.fixture
def record(fake):
    """C1, M1-M2, T1-T2 for PATIENT_A's episode, plus things that must NOT reach the model."""
    c1 = check_in(fake)
    fake.tables["episodes"][0]["checkin_completed_at"] = T0
    m1 = fake.add_message(sender="patient", body="The wound is red.\nAnd itchy.",
                          created_at="2026-10-03T08:00:00+00:00")
    m2 = fake.add_message(sender="clinician", body="Please keep it dry.",
                          created_at="2026-10-03T08:30:00+00:00")
    t1 = touchpoint(fake)
    t2 = touchpoint(fake, kind="visit", occurred_at="2026-10-02T20:00:00+00:00",
                    duration_minutes=None, note=None)
    fake.ids = {"C1": c1["id"], "M1": m1["id"], "M2": m2["id"], "T1": t1["id"], "T2": t2["id"]}
    # Not for the model: an automatic message row, a contact from before the episode, another
    # patient's and another episode's records, and a saved Conversation Summary.
    touchpoint(fake, kind="message", source="auto", note="AUTO-ROW-NOTE")
    touchpoint(fake, occurred_at="2026-09-01T10:00:00+00:00", note="BEFORE-THE-EPISODE")
    touchpoint(fake, patient_id=PATIENT_B, clinic_id=CLINIC_B, note="OTHER-PATIENT-NOTE")
    fake.add_message(patient=PATIENT_B, clinic=CLINIC_B, episode=EPISODE_B,
                     body="OTHER-PATIENT-MSG")
    fake.add_message(episode=new_id(), body="OTHER-EPISODE-MSG")
    fake.tables.setdefault("thread_summaries", []).append(
        {"id": new_id(), "clinic_id": CLINIC_A, "patient_id": PATIENT_A,
         "lines": [{"text": "CONVERSATION-SUMMARY-TEXT", "citations": []}]}
    )
    return fake


GOOD = (
    "- The picture now: Pain 8 with a red flag; the clinic advised keeping it dry. [C1, M2]\n"
    "- Trends: Pain 8 at check-in; bleeding and fever not reported. [C1]\n"
    "- Loose ends: Visit logged without a note. [T2]"
)


# --- generating ---------------------------------------------------------------------------


def test_returns_three_verified_bullets_with_typed_sources(client, record, model):
    model["text"] = GOOD
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = record.ids
    assert [b["label"] for b in body["bullets"]] == ["The picture now", "Trends", "Loose ends"]
    assert body["bullets"][0]["sources"] == [
        {"kind": "check_in", "id": ids["C1"]}, {"kind": "message", "id": ids["M2"]},
    ]
    assert body["bullets"][2]["sources"] == [{"kind": "touchpoint", "id": ids["T2"]}]
    assert body["bullets"][1]["text"] == "Pain 8 at check-in; bleeding and fever not reported."
    assert (body["model_id"], body["prompt_version"]) == (MODEL, "patient_brief_v1")
    assert body["covers"] == {"messages": 2, "check_ins": 1, "touchpoints": 2}
    assert body["new_since"] == {"messages": 0, "check_ins": 0, "touchpoints": 0}
    assert body["truncated"] is False and body["episode_id"] == EPISODE_A
    assert body["generated_at"].startswith("2026-10-03T11:30:00")


def test_the_model_is_shown_the_episodes_record_and_nothing_else(client, record, model):
    model["text"] = GOOD
    client.post(URL, headers=AUTH)
    (call,) = model["calls"]
    assert call["clinic_id"] == CLINIC_A and call["prompt_version"] == "patient_brief_v1"
    seen = call["system"] + call["prompt"]

    # computed state, in the clinic's time zone, with the red-flag rules the check-in matched
    assert "post-op day 2" in call["prompt"] and "Surgery date 2026-10-01" in call["prompt"]
    assert "Status: a message window is open. Message windows: 1 open; 2 upcoming" in seen
    assert "[C1] Check-in completed 2026-10-02 09:00 (post-op day 1). Red flag: yes" in seen
    assert "severe or worsening pain" in seen and "Reviewed by the clinic: not yet" in seen
    assert 'Answers: {"pain":{"scale":8,"trend":"worse"}}' in seen
    assert "Patient's own words: \"I am worried about the swelling and the redness\"" in seen
    # the thread, flattened, with who said it; and calls/visits with their notes
    assert "[M1] patient (medication): The wound is red. And itchy." in seen
    assert "[M2] clinician: Please keep it dry." in seen
    assert "[T1] call, 2026-10-02 11:00, 5 min: Told her to keep it dry" in seen
    assert "[T2] visit, 2026-10-02 16:00: (no note)" in seen
    # none of the ids, and none of what must stay out
    assert not any(i in seen for i in record.ids.values())
    for leak in ("OTHER-PATIENT-MSG", "OTHER-PATIENT-NOTE", "OTHER-EPISODE-MSG", "AUTO-ROW-NOTE",
                 "BEFORE-THE-EPISODE", "CONVERSATION-SUMMARY-TEXT", "Pat A", "Pat B"):
        assert leak not in seen


def test_the_system_prompt_carries_the_content_rules(client, record, model):
    model["text"] = GOOD
    client.post(URL, headers=AUTH)
    system = model["calls"][0]["system"]
    assert "PLACEHOLDER" in system
    for needle in ("The picture now:", "Trends:", "Loose ends:", "not reported", "None.",
                   "Do not diagnose", "Do not repeat the procedure"):
        assert needle in system


def test_it_is_audited_with_the_text_only_in_the_hashed_payload(client, record, model):
    model["text"] = GOOD
    body = client.post(URL, headers=AUTH).json()
    (event,) = record.audits
    assert event["event_type"] == "patient_brief.generated" and event["clinic_id"] == CLINIC_A
    meta = event["metadata"]
    assert meta["ref_type"] == "patient_brief" and meta["ref_id"] == body["brief_id"]
    assert (meta["model_id"], meta["prompt_version"]) == (MODEL, "patient_brief_v1")
    counts = (meta["covers_messages"], meta["covers_check_ins"], meta["covers_touchpoints"])
    assert counts == (2, 1, 2)
    assert meta["actor"]["id"] == USER and meta["patient_id"] == PATIENT_A
    assert meta["input_tokens"] == 120 and meta["output_tokens"] == 60
    assert "bullets" not in meta and "Pain 8" not in str(meta)  # no summary text in metadata
    payload = event["payload"]
    assert payload["bullets"][0]["text"].startswith("Pain 8")
    assert payload["bullets"][0]["sources"][0] == {"kind": "check_in", "id": record.ids["C1"]}
    assert payload["check_in_ids"] == [record.ids["C1"]]


def test_it_is_saved_after_the_audit_write(client, record, model):
    model["text"] = GOOD
    body = client.post(URL, headers=AUTH).json()
    (saved,) = record.tables["patient_briefs"]
    assert saved["id"] == body["brief_id"] and saved["episode_id"] == EPISODE_A
    assert saved["generated_by"] == USER and saved["truncated"] is False
    assert saved["covers"] == {"messages": 2, "check_ins": 1, "touchpoints": 2}
    assert saved["bullets"][2]["sources"] == [{"kind": "touchpoint", "id": record.ids["T2"]}]


# --- failures: nothing shown, nothing saved --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        GOOD.replace("[C1, M2]", ""),  # no citation
        GOOD.replace("[T2]", "[T9]"),  # a label that was never given
        GOOD.replace("Trends:", "Trend:"),  # misspelt label
        GOOD.split("\n", 1)[1] + "\n" + GOOD.split("\n")[0],  # misordered
        GOOD + "\n- Extra: x. [M1]",  # a fourth bullet
        "Everything looks fine.",  # not the format at all
    ],
)
def test_a_summary_that_does_not_verify_is_discarded_whole(client, record, model, text):
    model["text"] = text
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 502 and "discarded" in r.json()["detail"]
    assert record.audits == []
    assert not record.tables.get("patient_briefs")


def test_a_cited_record_that_vanished_from_the_database_is_discarded(
    client, record, model, monkeypatch
):
    """The model's labels were real when the prompt was built; the database is asked again."""
    model["text"] = GOOD
    original = briefs_module.verify_brief

    def verify_after_a_delete(supabase, **kw):
        supabase.tables["touchpoints"][:] = [
            r for r in supabase.tables["touchpoints"] if r["id"] != record.ids["T2"]
        ]
        return original(supabase, **kw)

    monkeypatch.setattr(briefs_module, "verify_brief", verify_after_a_delete)
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 502 and not record.tables.get("patient_briefs")


def test_if_the_audit_write_fails_nothing_is_shown_or_saved(client, record, model, monkeypatch):
    model["text"] = GOOD

    def boom(*_a, **_k):
        raise AuditWriteError("down")

    monkeypatch.setattr(briefs_module, "record_event", boom)
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 500 and "not shown" in r.json()["detail"]
    assert not record.tables.get("patient_briefs")


def test_if_saving_fails_the_brief_is_still_returned(client, record, model):
    model["text"] = GOOD
    record.fail_insert["patient_briefs"] = RuntimeError("db down")
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 200 and len(record.audits) == 1
    assert not record.tables.get("patient_briefs")


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


def test_a_check_in_alone_is_enough_to_call_the_model(client, fake, model):
    check_in(fake)
    model["text"] = "not even the right format"  # discarded, but the model was called
    client.post(URL, headers=AUTH)
    (call,) = model["calls"]
    assert "[C1]" in call["prompt"]
    assert "Messages (oldest first), one per line as \"[label] sender: text\":\n(none yet)" in call[
        "prompt"
    ]
    assert "kind, date: note\":\n(none)" in call["prompt"]


def test_a_message_alone_is_enough_to_call_the_model(client, fake, model):
    fake.add_message(sender="patient", body="hello", created_at="2026-10-03T08:00:00+00:00")
    model["text"] = "not even the right format"
    client.post(URL, headers=AUTH)
    (call,) = model["calls"]
    assert "[M1] patient (medication): hello" in call["prompt"]
    assert "as \"[label] ...\":\n(none yet)" in call["prompt"]  # the check-ins block


def test_a_patient_with_no_care_period_is_a_422(client, fake, model):
    fake.tables["episodes"].clear()
    r = client.post(URL, headers=AUTH)
    assert r.status_code == 422 and "no care period" in r.json()["detail"]
    assert model["calls"] == []


def test_same_access_rules_as_the_conversation_summary(client, record, model):
    model["text"] = GOOD
    assert client.post(URL, headers={"Authorization": "Bearer bad"}).status_code == 401
    assert client.post(URL).status_code in (401, 403)
    # a member of another clinic, and another clinic's patient
    other = f"/clinics/{CLINIC_B}/patients/{PATIENT_B}/patient-brief"
    assert client.post(other, headers=AUTH).status_code == 403
    assert client.get(f"{other}/latest", headers=AUTH).status_code == 403
    wrong = f"/clinics/{CLINIC_A}/patients/{PATIENT_B}/patient-brief"
    assert client.post(wrong, headers=AUTH).status_code == 404
    assert client.get(f"{wrong}/latest", headers=AUTH).status_code == 404
    assert model["calls"] == []
    # any member may generate, whatever the role (the fixture's member is a delegate)
    assert client.post(URL, headers=AUTH).status_code == 200


def test_old_messages_are_left_out_and_the_response_says_so(client, fake, model):
    check_in(fake)
    total = briefs_module.MESSAGE_LIMIT + 3
    for n in range(total):
        fake.add_message(sender="patient", body=f"msg {n}",
                         created_at=f"2026-10-03T08:{n // 60:02d}:{n % 60:02d}+00:00")
    model["text"] = GOOD.replace("[C1, M2]", "[C1]").replace("[T2]", "[C1]")
    body = client.post(URL, headers=AUTH).json()
    assert body["truncated"] is True and body["covers"]["messages"] == briefs_module.MESSAGE_LIMIT
    prompt = model["calls"][0]["prompt"]
    assert "[M1] patient (medication): msg 3\n" in prompt  # the three oldest were left out
    assert f"[M50] patient (medication): msg {total - 1}\n" in prompt and "[M51]" not in prompt
    assert "msg 2\n" not in prompt


# --- reading the saved one back, and what is new since ---------------------------------------


def test_opening_the_page_with_nothing_saved_is_null(client, record, model):
    r = client.get(f"{URL}/latest", headers=AUTH)
    assert r.status_code == 200 and r.json() is None
    assert model["calls"] == []


def test_the_newest_saved_one_loads_without_calling_the_model(client, record, model):
    model["text"] = GOOD
    shown = client.post(URL, headers=AUTH).json()
    model["calls"].clear()
    latest = client.get(f"{URL}/latest", headers=AUTH)
    assert latest.status_code == 200 and latest.json() == shown
    assert model["calls"] == []


def test_the_newest_of_several_is_the_one_read_back(client, record, model, monkeypatch):
    model["text"] = GOOD
    first = client.post(URL, headers=AUTH).json()
    pin_clock(monkeypatch, datetime(2026, 10, 3, 14, 0, tzinfo=UTC))
    model["text"] = GOOD.replace("Pain 8 at check-in", "Pain eight at check-in")
    second = client.post(URL, headers=AUTH).json()
    assert second["brief_id"] != first["brief_id"] and len(record.tables["patient_briefs"]) == 2
    assert client.get(f"{URL}/latest", headers=AUTH).json()["brief_id"] == second["brief_id"]


def test_new_since_counts_what_arrived_after_it_was_generated(client, record, model):
    model["text"] = GOOD
    client.post(URL, headers=AUTH)  # generated at 2026-10-03T11:30Z
    after = "2026-10-03T13:00:00+00:00"
    before = "2026-10-03T10:00:00+00:00"
    # new: two messages (either sender) and one check-in in this episode; three contacts logged
    record.add_message(sender="patient", body="n1", created_at=after)
    record.add_message(sender="clinician", body="n2", created_at=after)
    check_in(record, created_at=after)
    touchpoint(record, created_at=after)
    touchpoint(record, kind="visit", created_at=after)
    touchpoint(record, kind="email", created_at=after)
    # not new: older than the brief, an automatic message row, other patients/episodes/clinics
    record.add_message(sender="patient", body="old", created_at=before)
    touchpoint(record, kind="message", source="auto", created_at=after)
    touchpoint(record, created_at=before)
    touchpoint(record, patient_id=PATIENT_B, clinic_id=CLINIC_B, created_at=after)
    record.add_message(patient=PATIENT_B, clinic=CLINIC_B, episode=EPISODE_B, created_at=after)
    record.add_message(episode=new_id(), created_at=after)
    check_in(record, patient_id=PATIENT_B, clinic_id=CLINIC_B, episode_id=EPISODE_B,
             created_at=after)
    latest = client.get(f"{URL}/latest", headers=AUTH).json()
    assert latest["new_since"] == {"messages": 2, "check_ins": 1, "touchpoints": 3}
    assert latest["covers"] == {"messages": 2, "check_ins": 1, "touchpoints": 2}  # unchanged


def test_the_activity_that_was_part_of_the_input_is_not_new(client, record, model):
    model["text"] = GOOD
    client.post(URL, headers=AUTH)
    latest = client.get(f"{URL}/latest", headers=AUTH).json()
    assert latest["new_since"] == {"messages": 0, "check_ins": 0, "touchpoints": 0}
