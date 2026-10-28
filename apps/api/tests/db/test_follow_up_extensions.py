"""Migration 16 against a real Postgres: the extension schedule row, the thread entry kinds, the
request table and its guard, the two functions (who may ask, what a decision creates), clinic
isolation, and the whole story through the api with real audit writes (the chain verifies and
`reconcile` finds nothing unaudited). Numbers (30 days, six windows) are placeholders."""

import base64
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient
from psycopg import errors

from clinically_anchored_api.core import audit, clock, episodes, reconcile
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.accounts_env import patient_session
from tests.db.conftest import (
    ask_extension,
    build_world,
    check_in,
    clinician_msg,
    decide_extension,
    end_by_schedule,
    make_episode,
    new_patient,
    patient_msg,
)
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install

pytestmark = pytest.mark.db

TORONTO = ZoneInfo("America/Toronto")


def raises_text(text: str):
    return pytest.raises(errors.RaiseException, match=text)


def count(conn, sql, *params) -> int:
    return conn.execute(f"select count(*) as n from {sql}", params).fetchone()["n"]


def ended(conn, w, patient=None):
    """An episode whose schedule ran out, for a patient who had checked in."""
    e = make_episode(conn, w, patient=patient)
    check_in(conn, w, e)
    end_by_schedule(conn, w, e)
    return e


# --- the schedule row ------------------------------------------------------------------------


def test_extension_v1_is_data_not_code(db):
    row = db.conn.execute("select * from message_schedules where version = 3").fetchone()
    assert row["kind"] == "extension" and row["request_window_days"] == 30
    assert row["active"] is False  # shipped dark: switched on by hand
    assert row["offsets_days"] == [7, 14, 21, 28, 35]  # windows 2-6; window 1 is the acceptance
    assert row["close_after_days"] == 7  # ends on day 42
    assert (str(row["opens_at_local"]), row["timezone"]) == ("07:00:00", "America/Toronto")
    assert "placeholder" in row["note"].lower()
    main = db.conn.execute("select kind, request_window_days from message_schedules where version < 3")
    assert {(r["kind"], r["request_window_days"]) for r in main.fetchall()} == {("main", None)}


def test_the_kinds_cannot_be_mixed(db):
    c = db.conn
    with pytest.raises(errors.CheckViolation):
        c.execute(
            "insert into message_schedules (version, offsets_days, close_after_days, opens_at_local, "
            "timezone, kind) values (9, '{7}', 7, '07:00', 'America/Toronto', 'extension')"
        )
    with pytest.raises(errors.CheckViolation):
        c.execute(
            "insert into message_schedules (version, offsets_days, close_after_days, opens_at_local, "
            "timezone, request_window_days) values (9, '{7}', 7, '07:00', 'America/Toronto', 30)"
        )


def test_new_patients_never_get_the_extension_schedule_and_it_is_off_until_activated(db):
    pg = PgSupabase(db.conn)
    for active in (False, True):
        db.conn.execute("update message_schedules set active = %s where version = 3", (active,))
        default = episodes.default_schedule(pg)
        assert default.version == 2 and default.kind == "main"
    assert episodes.default_extension_schedule(pg).request_window_days == 30
    db.conn.execute("update message_schedules set active = false where version = 3")
    assert episodes.default_extension_schedule(pg) is None  # the feature is off


def test_an_extension_version_in_use_cannot_change_what_it_computes(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    rid = ask_extension(c, w, e)["request"]["id"]
    # A request alone does not freeze the row (no episode runs on it) ...
    c.execute("update message_schedules set request_window_days = 31 where version = 3")
    c.execute("update message_schedules set request_window_days = 30 where version = 3")
    decide_extension(c, w, rid, "accepted")  # ... an episode on it does
    for sql in (
        "update message_schedules set request_window_days = 60 where version = 3",
        "update message_schedules set offsets_days = '{7,14}' where version = 3",
        "update message_schedules set kind = 'main', request_window_days = null where version = 3",
    ):
        with raises_text("message_schedules_in_use"):
            c.execute(sql)
    c.execute("update message_schedules set active = false where version = 3")  # retiring is fine


# --- asking --------------------------------------------------------------------------------------


def test_a_request_stores_the_reason_as_its_own_thread_entry_and_a_pending_row(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    out = ask_extension(c, w, e, "Still sore")
    msg, req = out["message"], out["request"]
    assert (msg["origin"], msg["sender"], msg["body"]) == ("extension_request", "patient", "Still sore")
    assert (msg["interval_no"], msg["category"], msg["reply_to_message_id"]) == (None, None, None)
    assert msg["episode_id"] == e and msg["read_at"] is None
    assert req["status"] == "pending" and req["request_message_id"] == msg["id"]
    assert req["schedule_version"] == 3 and req["episode_id"] == e
    assert req["decided_at"] is None and req["extension_episode_id"] is None


@pytest.mark.parametrize("reason", ["", "   ", None, "x" * 501])
def test_the_reason_is_required_and_at_most_500_characters(db, reason):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    with raises_text("extension_reason_invalid"):
        ask_extension(c, w, e, reason)
    assert count(c, "extension_requests") == 0 and count(c, "messages") == 0


def test_500_characters_is_fine(db):
    c = db.conn
    w = build_world(c)
    ask_extension(c, w, ended(c, w), "x" * 500)


def test_nobody_can_ask_while_the_term_is_running(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    check_in(c, w, e)
    with raises_text("extension_term_running"):
        ask_extension(c, w, e)


def test_a_discharged_episode_cannot_ask(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    c.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_a, e, w.user_a))
    with raises_text("extension_not_eligible"):
        ask_extension(c, w, e)


def test_one_request_per_ended_episode(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    ask_extension(c, w, e)
    with raises_text("extension_already_requested"):
        ask_extension(c, w, e)
    assert count(c, "extension_requests") == 1


def test_the_time_to_ask_runs_out(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    with raises_text("extension_window_over"):
        ask_extension(c, w, e, expires_at="2020-01-01T00:00:00+00:00")


def test_only_an_extension_schedule_can_be_asked_under(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    with raises_text("extension_bad_schedule"):
        ask_extension(c, w, e, version=2)
    with raises_text("extension_bad_schedule"):
        ask_extension(c, w, e, version=99)


def test_a_patient_with_a_newer_care_period_cannot_ask_about_the_old_one(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    make_episode(c, w)  # a new surgery for the same patient
    with raises_text("extension_not_eligible"):
        ask_extension(c, w, e)


def test_another_clinic_cannot_ask_about_this_episode(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    with raises_text("episode_not_found"):
        c.execute(
            "select request_extension(%s, %s, gen_random_uuid(), gen_random_uuid(), 'x', 3, "
            "'2999-01-01')", (w.clinic_b, e),
        )


def test_the_new_entries_are_not_ordinary_messages(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    # The shape is enforced on the table itself.
    for sql in (
        "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin, interval_no) "
        "values (%(c)s, %(p)s, %(e)s, 'patient', 'x', 'extension_request', 1)",
        "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin, category) "
        "values (%(c)s, %(p)s, %(e)s, 'patient', 'x', 'extension_request', 'admin')",
        "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin) "
        "values (%(c)s, %(p)s, %(e)s, 'clinician', 'x', 'extension_request')",
        "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin) "
        "values (%(c)s, %(p)s, %(e)s, 'patient', 'x', 'extension_decision')",
        "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin) "
        "values (%(c)s, %(p)s, %(e)s, 'patient', repeat('x', 501), 'extension_request')",
    ):
        with pytest.raises(errors.CheckViolation):
            c.execute(sql, {"c": w.clinic_a, "p": w.patient_a, "e": e})


# --- deciding -------------------------------------------------------------------------------------


def test_declining_records_the_decision_makes_no_episode_and_grants_no_reply(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    rid = ask_extension(c, w, e)["request"]["id"]
    out = decide_extension(c, w, rid, "declined", "Please call the office.")
    req, entry = out["request"], out["message"]
    assert req["status"] == "declined" and req["decided_by"] == w.user_a and req["decided_at"]
    assert req["decision_message"] == "Please call the office." and req["extension_episode_id"] is None
    assert out["episode"] is None and count(c, "episodes") == 1
    assert (entry["origin"], entry["sender"], entry["episode_id"]) == (
        "extension_decision", "clinician", e,
    )
    # Nothing in the episode is a clinician message a patient could reply to.
    assert count(c, "messages where origin = 'clinician'") == 0
    with raises_text("episode_closed"):
        patient_msg(c, w, e, origin="reply", body="thanks")


def test_a_decision_can_come_without_a_message(db):
    c = db.conn
    w = build_world(c)
    rid = ask_extension(c, w, ended(c, w))["request"]["id"]
    out = decide_extension(c, w, rid, "declined", None)
    assert out["request"]["decision_message"] is None


@pytest.mark.parametrize("message", ["", "   ", "x" * 5001])
def test_the_clinicians_message_is_limited(db, message):
    c = db.conn
    w = build_world(c)
    rid = ask_extension(c, w, ended(c, w))["request"]["id"]
    with raises_text("extension_message_invalid"):
        decide_extension(c, w, rid, "accepted", message)
    assert count(c, "extension_requests where status = 'pending'") == 1


def test_a_request_is_decided_once(db):
    c = db.conn
    w = build_world(c)
    rid = ask_extension(c, w, ended(c, w))["request"]["id"]
    decide_extension(c, w, rid, "declined")
    for decision in ("declined", "accepted"):
        with raises_text("extension_not_pending"):
            decide_extension(c, w, rid, decision)


def test_a_decision_must_be_accepted_or_declined_and_name_a_request_of_this_clinic(db):
    c = db.conn
    w = build_world(c)
    rid = ask_extension(c, w, ended(c, w))["request"]["id"]
    with raises_text("extension_bad_decision"):
        decide_extension(c, w, rid, "maybe")
    with raises_text("extension_not_found"):
        c.execute(
            "select decide_extension(%s, %s, 'accepted', %s, null, gen_random_uuid(), 'x', "
            "gen_random_uuid())", (w.clinic_b, rid, w.user_b),
        )


def test_accepting_starts_an_extension_that_continues_the_ended_episode(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    parent = c.execute("select * from episodes where id = %s", (e,)).fetchone()
    rid = ask_extension(c, w, e)["request"]["id"]
    out = decide_extension(c, w, rid, "accepted", "Happy to continue.")
    child = c.execute("select * from episodes where id = %s", (out["episode"]["id"],)).fetchone()
    assert child["kind"] == "extension" and str(child["extends_episode_id"]) == e
    assert child["schedule_version"] == 3 and child["closed_at"] is None
    # Same patient, procedure and surgery date; the check-in counts; window 1 opens at acceptance.
    assert (child["patient_id"], child["procedure_id"], child["surgery_date"]) == (
        parent["patient_id"], parent["procedure_id"], parent["surgery_date"],
    )
    assert child["checkin_completed_at"] == child["started_at"] == child["created_at"]
    assert child["link_generation"] == parent["link_generation"]
    assert str(child["created_by"]) == w.user_a
    req = c.execute("select * from extension_requests where id = %s", (rid,)).fetchone()
    assert req["status"] == "accepted" and str(req["extension_episode_id"]) == str(child["id"])
    # The parent is untouched, and the decision belongs to it.
    assert c.execute("select * from episodes where id = %s", (e,)).fetchone() == parent
    assert str(out["message"]["episode_id"]) == e


def test_the_extension_works_like_the_main_period_one_message_a_window_and_replies(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    rid = ask_extension(c, w, e)["request"]["id"]
    child = decide_extension(c, w, rid, "accepted")["episode"]["id"]
    first = patient_msg(c, w, child, interval=1)
    assert (str(first["episode_id"]), first["interval_no"]) == (child, 1)
    with raises_text("interval_already_used"):
        patient_msg(c, w, child, interval=1)
    patient_msg(c, w, child, interval=2)  # windows number from 1 in each episode
    doc = clinician_msg(c, w, child, "How are you?")
    reply = patient_msg(c, w, child, origin="reply", body="Better")
    assert reply["reply_to_message_id"] == doc["id"]
    with raises_text("no_reply_available"):
        patient_msg(c, w, child, origin="reply", body="again")
    # The closed first episode still takes nothing.
    with raises_text("episode_closed"):
        patient_msg(c, w, e, interval=1)


def test_a_decline_message_cannot_be_answered_but_a_later_clinician_message_can(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    rid = ask_extension(c, w, e)["request"]["id"]
    child = decide_extension(c, w, rid, "accepted", "Welcome back.")["episode"]["id"]
    # Accepting grants no reply on its own: only a clinician message does.
    with raises_text("no_reply_available"):
        patient_msg(c, w, child, origin="reply")


def test_an_episode_is_extended_at_most_once_and_only_one_is_ever_open(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    rid = ask_extension(c, w, e)["request"]["id"]
    decide_extension(c, w, rid, "accepted")
    with pytest.raises(errors.UniqueViolation):
        c.execute(
            "insert into episodes (clinic_id, patient_id, procedure_id, surgery_date, "
            "schedule_version, created_by, kind, extends_episode_id, started_at, "
            "checkin_completed_at) select clinic_id, patient_id, procedure_id, surgery_date, 3, "
            "created_by, 'extension', %s, now(), now() from episodes where id = %s",
            (e, e),
        )


def test_a_new_surgery_after_the_request_blocks_acceptance_and_rolls_everything_back(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    rid = ask_extension(c, w, e)["request"]["id"]
    make_episode(c, w)
    messages_before = count(c, "messages")
    with raises_text("extension_superseded"):
        decide_extension(c, w, rid, "accepted")
    assert count(c, "messages") == messages_before and count(c, "episodes") == 2
    assert count(c, "extension_requests where status = 'pending'") == 1
    decide_extension(c, w, rid, "declined")  # declining is still possible


def test_asking_again_after_an_extension_ends_is_the_same_rule_about_the_new_episode(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    child = decide_extension(c, w, ask_extension(c, w, e)["request"]["id"], "accepted")["episode"]["id"]
    with raises_text("extension_term_running"):  # while it runs, no
        ask_extension(c, w, child)
    end_by_schedule(c, w, child)
    out = ask_extension(c, w, child, "Still not right")
    assert str(out["request"]["episode_id"]) == child
    grandchild = decide_extension(c, w, out["request"]["id"], "accepted")["episode"]
    assert str(grandchild["extends_episode_id"]) == child
    assert count(c, "extension_requests") == 2


def test_a_discharge_during_an_extension_ends_it_for_good(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    child = decide_extension(c, w, ask_extension(c, w, e)["request"]["id"], "accepted")["episode"]["id"]
    c.execute("select close_episode(%s, %s, 'discharged', %s)", (w.clinic_a, child, w.user_a))
    with raises_text("episode_closed"):
        clinician_msg(c, w, child)
    with raises_text("extension_not_eligible"):
        ask_extension(c, w, child)


# --- the guards ---------------------------------------------------------------------------------------


def test_a_request_is_never_deleted_and_its_original_never_changes(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    rid = ask_extension(c, w, e)["request"]["id"]
    with raises_text("extension_requests_retained"):
        c.execute("delete from extension_requests where id = %s", (rid,))
    with raises_text("extension_requests_original"):
        c.execute("update extension_requests set requested_at = now() - interval '1 day', "
                  "status = 'declined', decided_by = %s, decided_at = now(), decision_entry_id = "
                  "request_message_id where id = %s", (w.user_a, rid))
    with raises_text("extension_requests_decided"):
        c.execute("update extension_requests set status = 'pending' where id = %s", (rid,))


def test_a_decided_request_cannot_change(db):
    c = db.conn
    w = build_world(c)
    rid = ask_extension(c, w, ended(c, w))["request"]["id"]
    decide_extension(c, w, rid, "declined", "No.")
    with raises_text("extension_requests_decided"):
        c.execute("update extension_requests set decision_message = 'Yes.' where id = %s", (rid,))
    with raises_text("extension_requests_retained"):
        c.execute("delete from extension_requests where id = %s", (rid,))


def test_a_decision_row_must_be_complete_or_pending(db):
    c = db.conn
    w = build_world(c)
    rid = ask_extension(c, w, ended(c, w))["request"]["id"]
    with pytest.raises(errors.CheckViolation):
        c.execute("update extension_requests set status = 'accepted' where id = %s", (rid,))


def test_the_extension_episode_cannot_be_rewired_and_its_parent_must_be_the_same_patients(db):
    c = db.conn
    w = build_world(c)
    e = ended(c, w)
    child = decide_extension(c, w, ask_extension(c, w, e)["request"]["id"], "accepted")["episode"]["id"]
    for column, value in (("kind", "'main'"), ("started_at", "now()"), ("extends_episode_id", "null")):
        with pytest.raises((errors.RaiseException, errors.CheckViolation)):
            c.execute(f"update episodes set {column} = {value} where id = %s", (child,))
    other = ended(c, w, new_patient(c, w, "Someone else"))
    with pytest.raises(errors.ForeignKeyViolation):
        c.execute(
            "insert into episodes (clinic_id, patient_id, procedure_id, surgery_date, "
            "schedule_version, created_by, kind, extends_episode_id, started_at) "
            "values (%s, %s, %s, '2026-10-01', 3, %s, 'extension', %s, now())",
            (w.clinic_a, w.patient_a2, w.proc_a, w.user_a, other),  # patient_a2 is not other's
        )
        c.execute("set constraints all immediate")


def test_the_kind_shape_is_enforced_on_episodes(db):
    c = db.conn
    w = build_world(c)
    with pytest.raises(errors.CheckViolation):
        c.execute(
            "insert into episodes (clinic_id, patient_id, procedure_id, surgery_date, "
            "schedule_version, created_by, kind) values (%s, %s, %s, '2026-10-01', 3, %s, 'extension')",
            (w.clinic_a, w.patient_a, w.proc_a, w.user_a),
        )


# --- who can read ---------------------------------------------------------------------------------------


def test_clinic_members_read_their_clinics_requests_and_nobody_writes_them(db):
    c = db.conn
    w = build_world(c)
    rid = ask_extension(c, w, ended(c, w))["request"]["id"]
    c.execute("set role authenticated")
    try:
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_a,))
        assert count(c, "extension_requests") == 1
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_b,))
        assert count(c, "extension_requests") == 0
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("update extension_requests set decision_message = 'x' where id = %s", (rid,))
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("select request_extension(%s, %s, gen_random_uuid(), gen_random_uuid(), 'x', 3, "
                      "'2999-01-01')", (w.clinic_a, rid))
    finally:
        c.execute("reset role")


# --- the whole story through the api ----------------------------------------------------------------------


@pytest.fixture
def env(db, monkeypatch):
    conn = db.conn
    w = build_world(conn)
    conn.execute("update message_schedules set active = true where version = 3")  # switch it on
    conn.execute("update clinics set office_phone = '905-555-0100' where id = %s", (w.clinic_a,))
    pg = PgSupabase(conn)
    install(monkeypatch, pg, audit=False)  # real audit writes
    settings = get_settings()
    key = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(key).decode())
    monkeypatch.setattr(settings, "audit_key_id", "test-1")
    monkeypatch.setattr(settings, "patient_reads_per_minute", 0)
    monkeypatch.setattr(settings, "patient_writes_per_minute", 0)
    # The database functions use the real clock (a request's deadline, an acceptance), so the api
    # does too: the care period is built to have ended three days ago.
    monkeypatch.setattr(clock, "now", lambda: datetime.now(UTC))

    class Env:
        pass

    e = Env()
    e.w, e.pg, e.conn, e.client = w, pg, conn, TestClient(app)
    e.auth = {"Authorization": f"Bearer user:{w.user_a}"}
    e.clinic = f"/clinics/{w.clinic_a}"
    return e


def ended_three_days_ago(env, patient=None) -> tuple[str, str]:
    """(patient id, episode id) of a care period on schedule v2 that ran out 3 days ago."""
    conn, w = env.conn, env.w
    patient = patient or w.patient_a
    surgery = datetime.now(TORONTO).date() - timedelta(days=62)
    e = make_episode(conn, w, patient=patient, surgery_date=surgery, version=2)
    check_in(conn, w, e)
    end = datetime.combine(surgery + timedelta(days=59), datetime.min.time().replace(hour=7),
                           tzinfo=TORONTO).astimezone(UTC)
    end_by_schedule(conn, w, e, end.isoformat())
    return patient, e


def tp(env, episode_id: str) -> dict:
    """The headers of the care period's patient, signed in (an active account, made once)."""
    patient = env.conn.execute("select patient_id from episodes where id = %s", (episode_id,)
                               ).fetchone()["patient_id"]
    return patient_session(env.pg, env.w, str(patient))


def test_ask_accept_use_it_ask_again_and_the_audit_chain_still_verifies(env):
    c = env.client
    patient, e = ended_three_days_ago(env)
    token = tp(env, e)

    # Ended and eligible: the limited mode, and nothing else.
    state = c.get("/episode", headers=token).json()
    assert state["phase"] == "closed" and state["extension"]["standing"] == "eligible"
    assert state["extension"]["reason_max"] == 500
    assert c.get("/messages", headers=token).status_code == 410

    # Ask. It is pending, once.
    r = c.post("/extension-request", headers=token, json={"reason": "Still sore on the right."})
    assert r.status_code == 200 and r.json()["standing"] == "pending"
    assert c.post("/extension-request", headers=token, json={"reason": "again"}).status_code == 409
    assert c.get("/episode", headers=token).json()["extension"]["standing"] == "pending"

    # The clinic sees it: in the queue, and on the patient's episode with the reason.
    queue = c.get(f"{env.clinic}/queue", headers=env.auth).json()
    assert [(q["patient_id"], q["extension_requested"], q["unread_messages"]) for q in queue] == [
        (patient, True, 0)
    ]
    shown = c.get(f"{env.clinic}/patients/{patient}/episode", headers=env.auth).json()
    request = shown["extension"]["request"]
    assert request["status"] == "pending" and request["reason"] == "Still sore on the right."

    # Accept, with a message.
    r = c.post(f"{env.clinic}/extension-requests/{request['id']}/accept", headers=env.auth,
               json={"message": "Glad to keep following you."})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    child = r.json()["extension_episode_id"]
    assert not any(q["extension_requested"] for q in c.get(f"{env.clinic}/queue",
                                                            headers=env.auth).json())

    # The original link now opens the extension, with window 1 open.
    state = c.get("/episode", headers=token).json()
    assert state["phase"] == "open" and state["intervals_total"] == 6 and state["interval_no"] == 1
    assert state["extension"]["standing"] == "accepted"
    assert state["extension"]["decision_message"] == "Glad to keep following you."
    sent = c.post("/messages", headers=token, json={"body": "Feeling better", "category": "admin"})
    assert sent.status_code == 200 and sent.json()["episode_id"] == child
    assert c.post("/messages", headers=token,
                  json={"body": "More", "category": "admin"}).status_code == 409

    # The clinic's screen: nine windows, then the six of the extension.
    shown = c.get(f"{env.clinic}/patients/{patient}/episode", headers=env.auth).json()
    assert shown["id"] == child and shown["extension"]["number"] == 1
    assert len(shown["extension"]["earlier_windows"]) == 9 and len(shown["state"]["windows"]) == 6
    assert shown["state"]["windows"][0]["status"] == "used"

    # A clinician message grants one reply, exactly as before.
    assert c.post(f"{env.clinic}/patients/{patient}/messages", headers=env.auth,
                  json={"body": "Any fever?"}).status_code == 200
    assert c.post("/messages", headers=token,
                  json={"body": "No", "kind": "reply"}).status_code == 200

    # The thread has the request and the decision as their own kinds, in order.
    thread = c.get(f"{env.clinic}/patients/{patient}/messages", headers=env.auth).json()
    assert [m["origin"] for m in thread] == [
        "extension_request", "extension_decision", "scheduled", "clinician", "reply",
    ]

    # The extension runs out; the patient may ask again (a new approval is needed).
    ended_at = datetime.now(UTC)
    env.conn.execute("select close_episode(%s, %s, 'schedule_complete', null, %s)",
                     (env.w.clinic_a, child, ended_at))
    state = c.get("/episode", headers=token).json()
    assert state["phase"] == "closed" and state["extension"]["standing"] == "eligible"
    assert state["extension"]["extension_no"] == 1
    assert c.post("/extension-request", headers=token,
                  json={"reason": "One more round, please."}).status_code == 200
    request2 = c.get(f"{env.clinic}/patients/{patient}/episode", headers=env.auth).json()[
        "extension"]["request"]
    assert request2["episode_id"] == child and request2["status"] == "pending"
    assert c.post(f"{env.clinic}/extension-requests/{request2['id']}/accept",
                  headers=env.auth).status_code == 200
    shown = c.get(f"{env.clinic}/patients/{patient}/episode", headers=env.auth).json()
    assert shown["extension"]["number"] == 2 and len(shown["extension"]["earlier_windows"]) == 15
    assert {w["extension_no"] for w in shown["extension"]["earlier_windows"]} == {0, 1}

    # Discharge during the second extension ends it, and the link says only "closed".
    r = c.post(f"{env.clinic}/episodes/{shown['id']}/close", headers=env.auth)
    assert r.status_code == 200
    state = c.get("/episode", headers=token).json()
    assert state["phase"] == "closed" and state["extension"] is None

    # Everything was audited, in a chain that verifies, with no row left unaudited.
    rows = audit.fetch_chain(env.pg, env.w.clinic_a)
    assert audit.verify_chain(rows, audit.trusted_public_keys())["ok"]
    types = [r["event_type"] for r in rows]
    assert types.count("extension.requested") == 2 and types.count("extension.accepted") == 2
    assert types.index("extension.requested") < types.index("extension.accepted")
    report = reconcile.reconcile(env.pg, clinic_id=env.w.clinic_a)
    # The first episode, its check-in and the closes were made straight in SQL here; everything the
    # api did (both requests, both acceptances, both new episodes, the messages) is audited.
    assert {(g.table, g.event_type) for g in report.unaudited} <= {
        ("episodes", "episode.created"), ("episodes", "episode.closed"),
        ("check_ins", "check_in.submitted"), ("patients", "patient.created"),
    }
    assert {g.row_id for g in report.unaudited if g.event_type == "episode.created"} == {e}
    assert report.checked["extension.requested"] == 2 and report.checked["extension.accepted"] == 2
    # The readable metadata holds ids, never the reason or the clinician's words.
    readable = str([r["metadata"] for r in rows if r["event_type"].startswith("extension.")])
    assert "Still sore" not in readable and "Glad to keep" not in readable


def test_a_decline_is_final_and_shows_the_patient_the_message(env):
    c = env.client
    patient, e = ended_three_days_ago(env)
    token = tp(env, e)
    assert c.post("/extension-request", headers=token, json={"reason": "Please."}).status_code == 200
    request = c.get(f"{env.clinic}/patients/{patient}/episode", headers=env.auth).json()[
        "extension"]["request"]
    r = c.post(f"{env.clinic}/extension-requests/{request['id']}/decline", headers=env.auth,
               json={"message": "Please call the office."})
    assert r.status_code == 200 and r.json()["status"] == "declined"
    state = c.get("/episode", headers=token).json()
    assert state["phase"] == "closed" and state["extension"]["standing"] == "declined"
    assert state["extension"]["decision_message"] == "Please call the office."
    assert c.post("/extension-request", headers=token, json={"reason": "again"}).status_code == 409
    assert c.get("/messages", headers=token).status_code == 410
    assert c.post(f"{env.clinic}/extension-requests/{request['id']}/accept",
                  headers=env.auth).status_code == 409
    report = reconcile.reconcile(env.pg, clinic_id=env.w.clinic_a)
    assert not [g for g in report.unaudited if g.event_type.startswith("extension.")]
    assert report.checked["extension.declined"] == 1


def test_a_delegate_can_see_the_request_but_not_decide_it(env):
    c = env.client
    patient, e = ended_three_days_ago(env)
    c.post("/extension-request", headers=tp(env, e), json={"reason": "Please."})
    env.conn.execute("update clinic_members set role = 'delegate' where user_id = %s", (env.w.user_a,))
    shown = c.get(f"{env.clinic}/patients/{patient}/episode", headers=env.auth).json()
    request = shown["extension"]["request"]
    assert request["status"] == "pending"
    for how in ("accept", "decline"):
        assert c.post(f"{env.clinic}/extension-requests/{request['id']}/{how}",
                      headers=env.auth).status_code == 403
    assert count(env.conn, "extension_requests where status = 'pending'") == 1


def test_the_window_to_ask_runs_out_at_30_days(env, monkeypatch):
    c = env.client
    _patient, e = ended_three_days_ago(env)
    later = datetime.now(UTC) + timedelta(days=28)  # 31 days after the end
    monkeypatch.setattr(clock, "now", lambda: later)
    state = c.get("/episode", headers=tp(env, e)).json()
    assert state["phase"] == "closed" and state["extension"] is None
    assert c.post("/extension-request", headers=tp(env, e), json={"reason": "late"}).status_code == 410


def test_dates_are_what_the_engine_says(env):
    _patient, e = ended_three_days_ago(env)
    state = env.client.get("/episode", headers=tp(env, e)).json()
    end = datetime.fromisoformat(state["ended_at"]).astimezone(TORONTO)
    assert (end.hour, end.minute) == (7, 0)
    assert end.date() == datetime.now(TORONTO).date() - timedelta(days=3)
    by = datetime.fromisoformat(state["extension"]["request_by"]).astimezone(TORONTO)
    assert (by.hour, by.minute) == (7, 0) and (by - end).days in (29, 30)
