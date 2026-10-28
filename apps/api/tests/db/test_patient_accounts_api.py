"""Patient accounts through the api, on the real schema (migrations 1-18).

Covers: sign-in that never says whether an email is registered, code limits, the first-sign-in
steps (surgery date, consent), that a patient reaches only their own data, that clinician and
patient credentials never mix, turn-off / re-invite, the single-use fallback link, audit (first,
then change) and reconcile."""

import json
import re
from datetime import timedelta

import pytest
from psycopg.types.json import Jsonb

from clinically_anchored_api.core import audit, gotrue, patient_auth, reconcile
from clinically_anchored_api.core import clock as clock_module
from clinically_anchored_api.core.audit import AuditWriteError
from clinically_anchored_api.core.security import create_episode_token
from tests.db.accounts_env import Env
from tests.db.conftest import check_in, clinician_msg, patient_msg

pytestmark = pytest.mark.db

VERSION = "2026-10-v1"
BOTH = ["messaging", "ai_assisted_replies"]
GENERIC_REQUEST = {"ok": True}


@pytest.fixture
def env(db, monkeypatch):
    return Env(db, monkeypatch)


def count(c, table, where="true"):
    return c.execute(f"select count(*) as n from {table} where {where}").fetchone()["n"]


def code_mails(env, address):
    return [s for s in sent_to(env, address)
            if "sign-in code" in s["Content"]["Simple"]["Subject"]["Data"]]


def sent_to(env, address):
    return [s for s in env.ses.sent if s["Destination"]["ToAddresses"] == [address]]


# =========================================================================================
# Sign-in
# =========================================================================================


def test_asking_for_a_code_gives_the_same_answer_for_every_address(env):
    invited = env.add_patient("Invited", "invited@mail.org")
    active = env.add_patient("Active", "active@mail.org")
    off = env.add_patient("Off", "off@mail.org")
    locked = env.add_patient("Locked", "locked@mail.org")
    env.add_patient("Never invited", "never@mail.org")
    env.act(invited, "invite")
    env.activate(active, "active@mail.org")
    env.act(off, "invite")
    env.act(off, "turn-off")
    env.act(locked, "invite")
    env.c.execute("update patient_accounts set status = 'locked', locked_at = now() "
                  "where patient_id = %s", (locked,))

    answers = {}
    for who in ("invited@mail.org", "active@mail.org", "off@mail.org", "locked@mail.org",
                "never@mail.org", "nobody@mail.org"):
        r = env.request_code(who)
        answers[who] = (r.status_code, r.json(), dict(r.headers).get("content-length"))
    assert len({json.dumps(v, sort_keys=True) for v in answers.values()}) == 1
    assert answers["nobody@mail.org"][:2] == (202, GENERIC_REQUEST)

    # ...but only the invited and the active patient got a code.
    got = {a for a in answers if env.last_code(a)}
    assert got == {"invited@mail.org", "active@mail.org"}


def test_a_stranger_makes_no_call_to_supabase_auth(env):
    env.request_code("nobody@mail.org")
    assert "generate_link" not in env.gt.calls and "create_user" not in env.gt.calls


def test_a_code_is_mailed_through_ses_and_stored_nowhere(env):
    pid = env.add_patient()
    env.act(pid, "invite")
    env.request_code("pat@mail.org")
    code = env.last_code("pat@mail.org")
    assert code and re.fullmatch(r"\d{6}", code)
    mail = env.ses.last_to("pat@mail.org")["Content"]["Simple"]
    assert "Lakeview Surgical" in mail["Subject"]["Data"] and code not in mail["Subject"]["Data"]
    for table in ("email_outbox", "audit_log", "patient_accounts", "patient_sign_in_links"):
        dump = json.dumps([dict(r) for r in env.c.execute(f"select * from {table}").fetchall()],
                          default=str)
        assert code not in dump, table


def test_a_demo_clinic_keeps_the_code_for_the_demo_inbox_and_sends_nothing(env):
    w = env.w
    pid = env.add_patient("Demo Pat", "demo@example.com", clinic=w.clinic_b)
    r = env.client.post(env.url(pid, "/account/invite", w.clinic_b), headers=env.staff(w.user_b))
    assert r.status_code == 200, r.text
    env.request_code("demo@example.com")
    code = env.last_code("demo@example.com", clinic=w.clinic_b)
    assert code
    c = env.c
    c.execute("update clinic_members set role = 'owner' where user_id = %s", (w.user_b,))
    inbox = env.client.get(f"/clinics/{w.clinic_b}/demo-inbox", headers=env.staff(w.user_b)).json()
    assert [e["template"] for e in inbox] == ["sign_in_code", "invite"]
    assert code in inbox[0]["text"]
    assert env.ses.sent == []


def test_the_right_code_signs_in_once_and_only_once(env):
    pid = env.add_patient()
    env.act(pid, "invite")
    env.request_code("pat@mail.org")
    code = env.last_code("pat@mail.org")
    first = env.verify(" PAT@Mail.org ", f" {code} ")
    assert first.status_code == 200
    body = first.json()
    assert body["access_token"] and body["refresh_token"] and body["expires_in"] == 3600
    assert env.verify("pat@mail.org", code).status_code == 401  # spent
    assert env.account(pid)["last_sign_in_at"] is not None
    assert env.account(pid)["code_sent_at"] is None


def test_every_way_of_failing_to_enter_a_code_looks_the_same(env):
    invited = env.add_patient("I", "i@mail.org")
    off = env.add_patient("O", "o@mail.org")
    env.act(invited, "invite")
    env.act(off, "invite")
    env.act(off, "turn-off")
    env.request_code("i@mail.org")
    good = env.last_code("i@mail.org")
    wrong = "000000" if good != "000000" else "111111"

    def fail(email, code):
        r = env.verify(email, code)
        return r.status_code, json.dumps(r.json(), sort_keys=True)

    seen = {
        fail("nobody@mail.org", good),  # no such address
        fail("o@mail.org", good),  # access turned off
        fail("i@mail.org", wrong),  # wrong code
        fail("i@mail.org", "12345"),  # not six digits
        fail("not an email", good),
    }
    assert len(seen) == 1
    status, body = seen.pop()
    assert status == 401 and "did not work" in json.loads(body)["detail"]
    # And none of those was the real code, which still works:
    assert env.verify("i@mail.org", good).status_code == 200


def test_five_tries_per_code_then_a_new_code_is_needed(env):
    pid = env.add_patient()
    env.act(pid, "invite")
    env.request_code("pat@mail.org")
    good = env.last_code("pat@mail.org")
    wrong = "000001" if good != "000001" else "000002"
    for _ in range(5):
        assert env.verify("pat@mail.org", wrong).status_code == 401
    assert env.verify("pat@mail.org", good).status_code == 401  # the right code, too late
    assert env.gt.calls.count("verify_otp") == 5  # the sixth never reached Supabase
    env.request_code("pat@mail.org")  # a new code starts the count again
    assert env.verify("pat@mail.org", env.last_code("pat@mail.org")).status_code == 200


def test_the_fifth_try_may_still_be_the_right_one(env):
    pid = env.add_patient()
    env.act(pid, "invite")
    env.request_code("pat@mail.org")
    good = env.last_code("pat@mail.org")
    for _ in range(4):
        env.verify("pat@mail.org", "000009" if good != "000009" else "000008")
    assert env.verify("pat@mail.org", good).status_code == 200


def test_a_code_expires_after_ten_minutes(env):
    pid = env.add_patient()
    env.act(pid, "invite")
    env.request_code("pat@mail.org")
    code = env.last_code("pat@mail.org")
    env.c.execute("update patient_accounts set code_sent_at = now() - interval '601 seconds'")
    assert env.verify("pat@mail.org", code).status_code == 401
    env.c.execute("update patient_accounts set code_sent_at = now() - interval '590 seconds'")
    assert env.verify("pat@mail.org", code).status_code == 200


def test_codes_are_limited_to_one_a_minute_and_five_an_hour_without_saying_so(env):
    env.settings.patient_code_cooldown_seconds = 60
    pid = env.add_patient()
    env.act(pid, "invite")
    replies = [env.request_code("pat@mail.org").json() for _ in range(3)]
    assert replies == [GENERIC_REQUEST] * 3
    assert len(code_mails(env, "pat@mail.org")) == 1  # one a minute; the rest were swallowed

    env.settings.patient_code_cooldown_seconds = 0
    env.settings.patient_codes_per_hour = 4
    for _ in range(6):
        assert env.request_code("pat@mail.org").status_code == 202
    assert len(code_mails(env, "pat@mail.org")) == 4  # the earlier one plus three more


def test_one_address_can_only_try_so_often_per_minute(env):
    env.settings.patient_auth_per_ip_per_minute = 3
    for _ in range(3):
        assert env.request_code("x@mail.org").status_code == 202
    r = env.request_code("y@mail.org")
    assert r.status_code == 429 and r.headers["retry-after"]
    # each kind of attempt has its own budget
    assert env.verify("x@mail.org", "123456").status_code == 401


def test_sign_in_says_unavailable_when_the_anon_key_is_missing_for_everyone(env):
    env.settings.supabase_anon_key = ""
    env.add_patient()
    assert env.verify("pat@mail.org", "123456").status_code == 503
    assert env.verify("nobody@mail.org", "123456").status_code == 503


def test_a_bad_address_is_a_validation_error_not_a_lookup(env):
    assert env.request_code("not-an-address").status_code == 422


# =========================================================================================
# First sign-in: surgery date, then consent
# =========================================================================================


def invited_session(env, email="pat@mail.org", **kw):
    pid = env.add_patient(email=email, **kw)
    assert env.act(pid, "invite").status_code == 200
    return pid, env.sign_in(email)


def test_an_invited_patient_reaches_only_the_first_sign_in_steps(env):
    pid, headers = invited_session(env)
    me = env.client.get("/patient/account", headers=headers).json()
    assert me["status"] == "invited" and me["needs_identity"] and me["needs_consent"]
    assert me["consent_version"] == VERSION and me["required_consents"] == BOTH
    assert me["clinic_name"] == "Lakeview Surgical" and me["office_phone"] == "905-555-0100"
    assert me["identity_attempts_left"] == 3
    for path in ("/episode", "/messages", "/check-ins/context", "/consents"):
        r = env.client.get(path, headers=headers)
        assert r.status_code == 403 and "finish signing in" in r.json()["detail"], path


def test_consent_comes_after_the_surgery_date(env):
    pid, headers = invited_session(env)
    r = env.client.post("/patient/consent", headers=headers,
                        json={"consent_text_version": VERSION, "consents": BOTH})
    assert r.status_code == 409 and "surgery date" in r.json()["detail"]
    assert count(env.c, "consents") == 0


def test_the_right_surgery_date_then_consent_makes_the_account_active(env):
    pid, headers = invited_session(env)
    r = env.client.post("/patient/identity", headers=headers,
                        json={"surgery_date": env.surgery_date(pid)})
    assert r.status_code == 200 and not r.json()["needs_identity"] and r.json()["needs_consent"]
    assert env.account(pid)["status"] == "invited"  # not active until they agree
    r = env.client.post("/patient/consent", headers=headers,
                        json={"consent_text_version": VERSION, "consents": BOTH})
    assert r.status_code == 200 and r.json()["status"] == "active"
    a = env.account(pid)
    assert a["status"] == "active" and a["activated_at"] and a["identity_verified_at"]
    rows = env.c.execute("select consent_type, consent_text_version from consents "
                         "order by consent_type").fetchall()
    assert [(r["consent_type"], r["consent_text_version"]) for r in rows] == [
        ("ai_assisted_replies", VERSION), ("messaging", VERSION)]
    assert env.client.get("/episode", headers=headers).status_code == 200


@pytest.mark.parametrize("body,status", [
    ({"consent_text_version": "old", "consents": BOTH}, 409),  # not the wording they were shown
    ({"consent_text_version": VERSION, "consents": ["messaging"]}, 422),  # both are required
    ({"consent_text_version": VERSION, "consents": BOTH + ["marketing"]}, 422),
])
def test_consent_must_be_both_types_of_the_current_wording(env, body, status):
    pid, headers = invited_session(env)
    env.client.post("/patient/identity", headers=headers,
                    json={"surgery_date": env.surgery_date(pid)})
    assert env.client.post("/patient/consent", headers=headers, json=body).status_code == status
    assert count(env.c, "consents") == 0


def test_three_wrong_surgery_dates_lock_the_account_and_say_so(env):
    pid, headers = invited_session(env)
    wrong = {"surgery_date": "2001-01-01"}
    r1 = env.client.post("/patient/identity", headers=headers, json=wrong)
    r2 = env.client.post("/patient/identity", headers=headers, json=wrong)
    assert (r1.status_code, r1.json()["attempts_left"]) == (422, 2)
    assert (r2.status_code, r2.json()["attempts_left"]) == (422, 1)
    r3 = env.client.post("/patient/identity", headers=headers, json=wrong)
    assert r3.status_code == 403 and r3.json()["locked"] is True
    assert "call your surgeon's office" in r3.json()["detail"]
    a = env.account(pid)
    assert a["status"] == "locked" and a["locked_at"]
    # the right date no longer helps; only the clinic can unlock
    r = env.client.post("/patient/identity", headers=headers,
                        json={"surgery_date": env.surgery_date(pid)})
    assert r.status_code == 403
    assert env.client.get("/patient/account", headers=headers).json()["status"] == "locked"
    assert env.client.get("/episode", headers=headers).status_code == 403
    # no new codes either
    env.request_code("pat@mail.org")
    assert len(sent_to(env, "pat@mail.org")) == 2  # the invite and the one code from sign-in
    assert env.audit_types().count("patient_account.identity_locked") == 1


def test_the_date_is_checked_against_the_latest_care_period(env):
    pid = env.add_patient()
    env.act(pid, "invite")
    # a newer surgery for the same patient: that is the one to match
    from datetime import date

    from tests.db.conftest import make_episode
    old = env.surgery_date(pid)
    env.c.execute("update episodes set closed_at = now(), close_reason = 'schedule_complete'")
    make_episode(env.c, env.w, patient=pid, surgery_date=date.today() - timedelta(days=1))
    headers = env.sign_in("pat@mail.org")
    assert env.client.post("/patient/identity", headers=headers,
                           json={"surgery_date": old}).status_code == 422
    new = env.surgery_date(pid)
    assert new != old
    assert env.client.post("/patient/identity", headers=headers,
                           json={"surgery_date": new}).status_code == 200


# =========================================================================================
# A patient reaches only their own data; clinician and patient credentials never mix
# =========================================================================================


def two_active_patients(env):
    a = env.add_patient("Pat A", "a@mail.org")
    b = env.add_patient("Pat B", "b@mail.org")
    ha, hb = env.activate(a, "a@mail.org"), env.activate(b, "b@mail.org")
    eps = {p: str(env.c.execute("select id from episodes where patient_id = %s", (p,)
                                ).fetchone()["id"]) for p in (a, b)}
    for p, name in ((a, "alpha"), (b, "bravo")):
        check_in(env.c, env.w, eps[p])
        clinician_msg(env.c, env.w, eps[p], body=f"hello {name}")
    return a, b, ha, hb, eps


def bodies(response):
    return [m["body"] for m in response.json()]


def test_each_patient_sees_only_their_own_thread(env):
    a, b, ha, hb, eps = two_active_patients(env)
    assert bodies(env.client.get("/messages", headers=ha)) == ["hello alpha"]
    assert bodies(env.client.get("/messages", headers=hb)) == ["hello bravo"]


def test_nothing_in_the_request_can_name_another_patient(env):
    a, b, ha, hb, eps = two_active_patients(env)
    # another patient's legacy link, ids in the query and in the body: all ignored
    other_link = create_episode_token(episode_id=eps[b], generation=1)
    r = env.client.get(f"/messages?token={other_link}&patient_id={b}&clinic_id={env.w.clinic_b}"
                       f"&episode_id={eps[b]}", headers=ha)
    assert bodies(r) == ["hello alpha"]
    r = env.client.post(f"/consents?token={other_link}", headers=ha, json={
        "consent_type": "messaging", "consent_text_version": "v9", "patient_id": b})
    assert r.status_code == 200
    owners = env.c.execute("select patient_id::text from consents where consent_text_version = "
                           "'v9'").fetchall()
    assert [o["patient_id"] for o in owners] == [a]
    # the state endpoint too
    assert env.client.get(f"/episode?token={other_link}", headers=ha).status_code == 200


def test_a_header_that_does_not_pass_is_never_replaced_by_a_link_in_the_url(env):
    a, b, ha, hb, eps = two_active_patients(env)
    link = create_episode_token(episode_id=eps[a], generation=1)
    assert env.client.get(f"/messages?token={link}").status_code == 200  # the old way still works
    for headers, status in (
        ({"Authorization": "Bearer garbage"}, 401),
        ({"Authorization": "Bearer user:" + env.w.user_a}, 403),  # a clinician's token
        ({"Authorization": "Basic abc"}, 401),
    ):
        r = env.client.get(f"/messages?token={link}", headers=headers)
        assert r.status_code == status, headers
    assert env.client.get("/messages").status_code == 401


def test_a_clinicians_token_is_not_a_patient(env):
    r = env.client.get("/messages", headers=env.staff())
    assert r.status_code == 403 and "not a patient account" in r.json()["detail"]
    assert env.client.get("/patient/account", headers=env.staff()).status_code == 403


def test_a_patients_token_opens_no_clinician_route(env):
    a, b, ha, hb, eps = two_active_patients(env)
    w = env.w
    for method, path in (
        ("get", "/me"),
        ("get", f"/clinics/{w.clinic_a}/patients"),
        ("get", f"/clinics/{w.clinic_a}/patients/{a}/consents"),
        ("get", f"/clinics/{w.clinic_a}/patients/{a}/account"),
        ("post", f"/clinics/{w.clinic_a}/patients/{a}/account/turn-off"),
        ("get", f"/clinics/{w.clinic_b}/demo-inbox"),
        ("get", f"/clinics/{w.clinic_a}/audit-log/verify"),
    ):
        r = getattr(env.client, method)(path, headers=ha)
        assert r.status_code == 403, (path, r.status_code)
        assert "patient account" in r.json()["detail"], path
    assert env.account(a)["status"] == "active"


def test_a_patient_cannot_claim_staff_membership_even_in_the_database(env):
    pid = env.add_patient()
    env.act(pid, "invite")
    user = env.account(pid)["user_id"]
    with pytest.raises(Exception, match="staff_user_is_patient"):
        env.c.execute("insert into clinic_members (clinic_id, user_id, role) values (%s, %s, "
                      "'owner')", (env.w.clinic_a, user))


# =========================================================================================
# Sessions: how long, and turning access off
# =========================================================================================


def test_a_session_older_than_thirty_days_asks_to_sign_in_again(env):
    pid, headers = invited_session(env)
    assert env.client.get("/patient/account", headers=headers).status_code == 200
    env.c.execute("update auth.sessions set created_at = now() - interval '31 days'")
    patient_auth.reset_cache()
    r = env.client.get("/patient/account", headers=headers)
    assert r.status_code == 401 and "sign in again" in r.json()["detail"]
    env.c.execute("update auth.sessions set created_at = now() - interval '29 days'")
    patient_auth.reset_cache()
    assert env.client.get("/patient/account", headers=headers).status_code == 200


def test_turning_access_off_ends_every_session_and_blocks_at_once(env):
    pid = env.add_patient()
    headers = env.activate(pid, "pat@mail.org")
    second = env.sign_in("pat@mail.org")  # a second device
    for h in (headers, second):  # both are remembered by the api for a few seconds
        assert env.client.get("/patient/account", headers=h).status_code == 200
    assert count(env.c, "auth.sessions") == 2

    r = env.act(pid, "turn-off")
    assert r.status_code == 200 and r.json()["status"] == "off" and r.json()["turned_off_at"]
    assert count(env.c, "auth.sessions") == 0
    assert env.gt.users[str(env.account(pid)["user_id"])]["banned"] is True
    a = env.account(pid)
    assert a["status"] == "off" and str(a["turned_off_by"]) == env.w.user_a
    # The token was remembered for up to 30 seconds; the status check does not care.
    for h in (headers, second):
        r = env.client.get("/messages", headers=h)
        assert r.status_code == 403 and "turned off" in r.json()["detail"]
    patient_auth.reset_cache()
    assert env.client.get("/messages", headers=headers).status_code == 401  # session gone
    env.request_code("pat@mail.org")
    assert env.verify("pat@mail.org", "123456").status_code == 401


def test_turning_off_is_safe_to_repeat_and_reports_when_sessions_could_not_be_ended(
    env, monkeypatch
):
    pid = env.add_patient()
    env.activate(pid, "pat@mail.org")
    real_ban = gotrue.ban
    monkeypatch.setattr(gotrue, "ban", lambda uid: (_ for _ in ()).throw(RuntimeError("down")))
    r = env.act(pid, "turn-off")
    assert r.status_code == 502 and "could not be ended" in r.json()["detail"]
    assert env.account(pid)["status"] == "off"  # the api already refuses them
    monkeypatch.setattr(gotrue, "ban", real_ban)
    assert env.act(pid, "turn-off").status_code == 200
    assert env.audit_types().count("patient_account.access_turned_off") == 1


@pytest.mark.parametrize("state", ["invited", "locked"])
def test_access_can_be_turned_off_for_an_invited_or_locked_account(env, state):
    pid = env.add_patient()
    env.act(pid, "invite")
    if state == "locked":
        env.c.execute("update patient_accounts set status = 'locked', locked_at = now()")
    assert env.act(pid, "turn-off").json()["status"] == "off"


def test_reinviting_starts_the_first_sign_in_again(env):
    pid = env.add_patient()
    old = env.activate(pid, "pat@mail.org")
    env.act(pid, "turn-off")
    sent_before = len(sent_to(env, "pat@mail.org"))
    r = env.act(pid, "reinvite")
    assert r.status_code == 200 and r.json()["status"] == "invited"
    a = env.account(pid)
    assert a["identity_verified_at"] is None and a["turned_off_at"] is not None  # kept: history
    assert a["identity_failed_attempts"] == 0
    assert len(sent_to(env, "pat@mail.org")) == sent_before + 1
    assert env.gt.users[str(a["user_id"])]["banned"] is False
    patient_auth.reset_cache()
    assert env.client.get("/patient/account", headers=old).status_code == 401
    # They sign in with a code again and re-check the date; consent they already gave is kept.
    headers = env.sign_in("pat@mail.org")
    me = env.client.get("/patient/account", headers=headers).json()
    assert me["needs_identity"] and not me["needs_consent"]
    r = env.client.post("/patient/identity", headers=headers,
                        json={"surgery_date": env.surgery_date(pid)})
    assert r.json()["status"] == "active"
    assert count(env.c, "consents") == 2  # not asked twice


def test_reinviting_unlocks_a_locked_account_and_ends_its_sessions(env):
    pid, headers = invited_session(env)
    for _ in range(3):
        env.client.post("/patient/identity", headers=headers, json={"surgery_date": "2001-01-01"})
    assert env.account(pid)["status"] == "locked"
    assert env.act(pid, "reinvite").json()["status"] == "invited"
    patient_auth.reset_cache()
    assert env.client.get("/patient/account", headers=headers).status_code == 401


def test_reinvite_needs_a_locked_or_turned_off_account(env):
    pid = env.add_patient()
    assert env.act(pid, "reinvite").status_code == 409  # no account
    env.act(pid, "invite")
    assert env.act(pid, "reinvite").status_code == 409  # still invited
    assert env.act(pid, "turn-off").status_code == 200
    assert env.act(pid, "turn-off").status_code == 200  # repeated, harmless


# =========================================================================================
# The clinic's actions
# =========================================================================================


def test_inviting_creates_a_patient_user_and_mails_the_invite(env):
    pid = env.add_patient()
    r = env.act(pid, "invite")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "invited" and body["email"] == "pat@mail.org"
    assert body["last_invite_email"]["status"] == "sent"
    user = env.gt.users[str(env.account(pid)["user_id"])]
    assert user["app_metadata"] == {"account_type": "patient"} and user["email"] == "pat@mail.org"
    mail = env.ses.last_to("pat@mail.org")["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "https://app.clinic.example.org/sign-in" in mail and "Lakeview Surgical" in mail
    assert "905-555-0100" in mail
    assert not re.search(r"\b\d{6}\b", mail.replace("905-555-0100", ""))  # an invite has no code
    a = env.account(pid)
    assert str(a["invited_by"]) == env.w.user_a and a["invited_at"]


def test_the_invite_needs_an_email_and_cannot_be_sent_twice(env):
    none = env.add_patient("No Email", None)
    assert env.act(none, "invite").status_code == 422
    pid = env.add_patient()
    assert env.act(pid, "invite").status_code == 200
    r = env.act(pid, "invite")
    assert r.status_code == 409 and "Resend" in r.json()["detail"]
    assert count(env.c, "patient_accounts") == 1


def test_any_member_may_invite_and_resend_but_only_owner_or_clinician_may_do_the_rest(env):
    pid = env.add_patient()
    d = env.delegate
    assert env.act(pid, "invite", by=d).status_code == 200
    assert env.act(pid, "resend", by=d).status_code == 200
    for action in ("turn-off", "reinvite", "fallback-link"):
        assert env.act(pid, action, by=d).status_code == 403, action
    assert env.account(pid)["status"] == "invited"
    assert env.act(pid, "turn-off", by=env.w.user_a).status_code == 200


def test_resend_mails_again_and_follows_an_email_that_was_corrected(env):
    pid = env.add_patient(email="typo@mail.org")
    env.act(pid, "invite")
    r = env.client.put(env.url(pid, "/email"), headers=env.staff(), json={"email": " Pat@Mail.org "})
    assert r.status_code == 200 and r.json()["email"] == "pat@mail.org"
    assert env.act(pid, "resend").status_code == 200
    assert len(sent_to(env, "pat@mail.org")) == 1
    assert env.gt.users[str(env.account(pid)["user_id"])]["email"] == "pat@mail.org"
    env.request_code("typo@mail.org")
    assert env.last_code("typo@mail.org") is None  # the old address gets nothing
    env.request_code("pat@mail.org")
    assert env.last_code("pat@mail.org")
    assert env.audit_types().count("patient_account.invited") == 2
    assert "patient.email_changed" in env.audit_types()


def test_the_email_of_an_active_patient_cannot_be_changed_until_access_is_turned_off(env):
    pid = env.add_patient()
    env.activate(pid, "pat@mail.org")
    r = env.client.put(env.url(pid, "/email"), headers=env.staff(), json={"email": "new@mail.org"})
    assert r.status_code == 409 and "Turn off" in r.json()["detail"]
    with pytest.raises(Exception, match="patient_email_locked"):  # and the database agrees
        env.c.execute("update patients set email = 'sneaky@mail.org' where id = %s", (pid,))
    env.act(pid, "turn-off")
    assert env.client.put(env.url(pid, "/email"), headers=env.staff(),
                          json={"email": "new@mail.org"}).status_code == 200
    env.act(pid, "reinvite")
    assert env.ses.last_to("new@mail.org") is not None
    assert env.gt.users[str(env.account(pid)["user_id"])]["email"] == "new@mail.org"


def test_setting_the_same_email_again_or_a_bad_one(env):
    pid = env.add_patient()
    assert env.client.put(env.url(pid, "/email"), headers=env.staff(),
                          json={"email": "pat@mail.org"}).status_code == 200
    assert "patient.email_changed" not in env.audit_types()
    assert env.client.put(env.url(pid, "/email"), headers=env.staff(),
                          json={"email": "nope"}).status_code == 422
    assert env.client.put(env.url(pid, "/email"), headers=env.staff(),
                          json={"email": None}).json()["email"] is None


def test_two_patients_cannot_share_one_live_account_email(env):
    one = env.add_patient("One", "same@mail.org")
    two = env.add_patient("Two", "same@mail.org")
    assert env.act(one, "invite").status_code == 200
    r = env.act(two, "invite")
    assert r.status_code == 409
    assert count(env.c, "patient_accounts") == 1
    env.act(one, "turn-off")
    assert env.act(two, "invite").status_code == 200  # free again once the first is off


def test_an_email_that_belongs_to_staff_cannot_become_a_patient_account(env):
    staff_email = env.c.execute("select email from auth.users where id = %s",
                                (env.w.user_a,)).fetchone()["email"]
    pid = env.add_patient("Doc", staff_email)
    r = env.act(pid, "invite")
    assert r.status_code == 409 and "staff" in r.json()["detail"]
    assert count(env.c, "patient_accounts") == 0
    assert "create_user" not in env.gt.calls


def test_the_account_view_shows_each_state(env):
    pid = env.add_patient()
    url = env.url(pid, "/account")
    assert env.client.get(url, headers=env.staff()).json() == {
        "status": "none", "email": "pat@mail.org", "invited_at": None, "activated_at": None,
        "turned_off_at": None, "locked_at": None, "last_sign_in_at": None,
        "last_invite_email": None, "fallback_link": None}
    env.activate(pid, "pat@mail.org")
    body = env.client.get(url, headers=env.staff(env.delegate)).json()
    assert body["status"] == "active" and body["activated_at"] and body["last_sign_in_at"]
    assert body["last_invite_email"]["status"] == "sent"
    other = env.add_patient("Elsewhere", "e@mail.org", clinic=env.w.clinic_b)
    assert env.client.get(env.url(other, "/account"), headers=env.staff()).status_code in (403, 404)
    assert env.client.get(env.url(other, "/account"), headers=env.staff()).status_code != 200


def test_a_patient_of_another_clinic_cannot_be_reached(env):
    other = env.add_patient("Elsewhere", "e@mail.org", clinic=env.w.clinic_b)
    r = env.client.post(env.url(other, "/account/invite", env.w.clinic_a), headers=env.staff())
    assert r.status_code == 404
    assert count(env.c, "patient_accounts") == 0


# =========================================================================================
# The single-use fallback link
# =========================================================================================


def issue_link(env, pid, by=None):
    r = env.act(pid, "fallback-link", by=by)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith("https://app.clinic.example.org/sign-in/link#")
    return url.split("#", 1)[1], r.json()


def redeem(env, token, date_):
    return env.client.post("/patient-auth/link", json={"token": token, "surgery_date": date_})


def test_a_link_for_a_patient_without_email_creates_the_account_and_is_shown_once(env):
    pid = env.add_patient("No Email", None)
    token, body = issue_link(env, pid)
    a = env.account(pid)
    assert a["status"] == "invited"
    assert env.gt.users[str(a["user_id"])]["email"].endswith("@no-email.invalid")
    assert env.ses.sent == []  # nothing is mailed, to a placeholder least of all
    row = env.c.execute("select * from patient_sign_in_links").fetchone()
    assert row["token_hash"] != token and len(row["token_hash"]) == 64
    assert token not in json.dumps([dict(r) for r in env.c.execute(
        "select * from patient_sign_in_links").fetchall()], default=str)
    delta = row["expires_at"] - row["issued_at"]
    assert delta == timedelta(hours=24)
    assert env.client.get(env.url(pid, "/account"), headers=env.staff()).json()["fallback_link"][
        "used_at"] is None


def test_the_link_needs_the_surgery_date_signs_in_once_and_then_the_first_steps_continue(env):
    pid = env.add_patient("No Email", None)
    token, _ = issue_link(env, pid)
    date_ = env.surgery_date(pid)
    r = redeem(env, token, "2001-01-01")
    assert r.status_code == 422 and r.json()["attempts_left"] == 2
    assert count(env.c, "patient_sign_in_links", "used_at is not null") == 0  # not used up
    r = redeem(env, token, date_)
    assert r.status_code == 200
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert redeem(env, token, date_).status_code == 401  # once
    me = env.client.get("/patient/account", headers=headers).json()
    assert me["status"] == "invited" and not me["needs_identity"] and me["needs_consent"]
    r = env.client.post("/patient/consent", headers=headers,
                        json={"consent_text_version": VERSION, "consents": BOTH})
    assert r.json()["status"] == "active"
    assert env.client.get("/episode", headers=headers).status_code == 200


def test_the_link_asks_for_the_date_even_when_the_patient_is_already_active(env):
    pid = env.add_patient("Active", "act@mail.org")
    env.activate(pid, "act@mail.org")
    token, _ = issue_link(env, pid)
    assert redeem(env, token, "2001-01-01").status_code == 422
    assert redeem(env, token, env.surgery_date(pid)).status_code == 200


def test_three_wrong_dates_on_a_link_lock_the_account(env):
    pid = env.add_patient("No Email", None)
    token, _ = issue_link(env, pid)
    for _ in range(2):
        assert redeem(env, token, "2001-01-01").status_code == 422
    r = redeem(env, token, "2001-01-01")
    assert r.status_code == 403 and r.json()["locked"] is True
    assert env.account(pid)["status"] == "locked"
    assert redeem(env, token, env.surgery_date(pid)).status_code == 401  # locked accounts: no
    assert "patient_account.identity_locked" in env.audit_types()


def test_a_newer_link_replaces_the_older_one_and_an_old_one_expires(env):
    pid = env.add_patient("No Email", None)
    first, _ = issue_link(env, pid)
    second, _ = issue_link(env, pid)
    date_ = env.surgery_date(pid)
    assert redeem(env, first, date_).status_code == 401
    env.c.execute("alter table patient_sign_in_links disable trigger patient_sign_in_links_guard")
    env.c.execute("update patient_sign_in_links set issued_at = now() - interval '25 hours', "
                  "expires_at = now() - interval '1 hour' where revoked_at is null")
    env.c.execute("alter table patient_sign_in_links enable trigger patient_sign_in_links_guard")
    assert redeem(env, second, date_).status_code == 401
    assert redeem(env, "x" * 43, date_).status_code == 401  # never issued


def test_a_link_for_a_locked_or_turned_off_account_needs_a_reinvite_first(env):
    pid = env.add_patient()
    env.act(pid, "invite")
    env.act(pid, "turn-off")
    assert env.act(pid, "fallback-link").status_code == 409
    env.act(pid, "reinvite")
    assert env.act(pid, "fallback-link").status_code == 200


def test_turning_access_off_voids_an_unused_link(env):
    pid = env.add_patient("No Email", None)
    token, _ = issue_link(env, pid)
    env.act(pid, "turn-off")
    assert redeem(env, token, env.surgery_date(pid)).status_code == 401


def test_the_link_needs_the_web_address_to_build_a_url(env):
    env.settings.web_base_url = ""
    env.settings.environment = "production"
    pid = env.add_patient("No Email", None)
    assert env.act(pid, "fallback-link").status_code == 503
    assert count(env.c, "patient_accounts") == 0


# =========================================================================================
# Audit: first, then change; nothing sensitive; reconcile
# =========================================================================================


def test_a_full_lifecycle_is_audited_and_reconcile_and_the_chain_are_clean(env):
    pid = env.add_patient()
    env.activate(pid, "pat@mail.org")
    env.act(pid, "turn-off")
    env.act(pid, "reinvite")
    nm = env.add_patient("No Email", None)
    token, _ = issue_link(env, nm)
    redeem(env, token, "2001-01-01")
    redeem(env, token, env.surgery_date(nm))
    for _ in range(3):
        redeem(env, issue_link(env, nm)[0], "2001-01-01")

    types = set(env.audit_types())
    assert {
        "patient_account.invited", "patient_account.sign_in_code_sent",
        "patient_account.activated", "patient_account.identity_locked", "consent.granted",
        "patient_account.access_turned_off", "patient_account.fallback_link_issued",
        "patient_account.fallback_link_used",
    } <= types
    report = reconcile.reconcile(env.pg, clinic_id=env.w.clinic_a)
    mine = [g for g in report.unaudited if g.event_type.startswith("patient_account")
            or g.event_type == "consent.granted"]
    assert mine == []
    assert [d for d in report.dangling if d.event_type.startswith("patient_account")] == []
    rows = audit.fetch_chain(env.pg, env.w.clinic_a)
    assert audit.verify_chain(rows, audit.trusted_public_keys())["ok"]


def test_audit_metadata_holds_ids_only(env):
    pid = env.add_patient()
    env.activate(pid, "pat@mail.org")
    token, _ = issue_link(env, env.add_patient("No Email", None))
    env.act(pid, "turn-off")
    code = None
    for row in env.c.execute("select event_type, metadata from audit_log").fetchall():
        text = json.dumps(row["metadata"])
        assert "@" not in text and "mail.org" not in text, row["event_type"]
        assert token not in text
        assert not re.search(r"\b\d{6}\b", re.sub(r'"[0-9a-f-]{36}"|[0-9a-f]{16,}', "", text)), text
        assert set(row["metadata"]) <= {"ref_type", "ref_id", "actor", "patient_id", "salt"}
    assert code is None


@pytest.mark.parametrize("event,setup,act", [
    ("patient_account.invited", lambda e, p: None, lambda e, p: e.act(p, "invite")),
    ("patient_account.access_turned_off", lambda e, p: e.act(p, "invite"),
     lambda e, p: e.act(p, "turn-off")),
    ("patient_account.invited", lambda e, p: (e.act(p, "invite"), e.act(p, "turn-off")),
     lambda e, p: e.act(p, "reinvite")),
    ("patient_account.fallback_link_issued", lambda e, p: e.act(p, "invite"),
     lambda e, p: e.act(p, "fallback-link")),
])
def test_when_the_audit_cannot_be_written_the_change_is_not_made(env, monkeypatch, event, setup,
                                                                 act):
    pid = env.add_patient()
    setup(env, pid)
    before = (count(env.c, "patient_accounts"), env.c.execute(
        "select coalesce(string_agg(status, ','), '') as s from patient_accounts").fetchone()["s"],
        count(env.c, "patient_sign_in_links"), len(env.gt.calls))
    real = patient_auth.record_event

    def failing(supabase, **kw):
        if kw["event_type"] == event:
            raise AuditWriteError("down")
        return real(supabase, **kw)

    monkeypatch.setattr(patient_auth, "record_event", failing)
    r = act(env, pid)
    assert r.status_code == 500 and "nothing was changed" in r.json()["detail"]
    after = (count(env.c, "patient_accounts"), env.c.execute(
        "select coalesce(string_agg(status, ','), '') as s from patient_accounts").fetchone()["s"],
        count(env.c, "patient_sign_in_links"), len(env.gt.calls))
    assert after == before


def test_when_the_audit_cannot_be_written_a_code_is_not_made_and_the_account_does_not_lock(
    env, monkeypatch
):
    pid, headers = invited_session(env)
    real = patient_auth.record_event

    def failing(supabase, **kw):
        if kw["event_type"] in ("patient_account.sign_in_code_sent",
                                "patient_account.identity_locked",
                                "patient_account.activated", "consent.granted"):
            raise AuditWriteError("down")
        return real(supabase, **kw)

    monkeypatch.setattr(patient_auth, "record_event", failing)
    before = env.gt.calls.count("generate_link")
    assert env.request_code("pat@mail.org").status_code == 202
    assert env.gt.calls.count("generate_link") == before
    for _ in range(3):
        r = env.client.post("/patient/identity", headers=headers, json={"surgery_date": "2001-01-01"})
        assert r.status_code in (422, 500)
    assert env.account(pid)["status"] == "invited"
    monkeypatch.setattr(patient_auth, "record_event", real)
    env.client.post("/patient/identity", headers=headers, json={"surgery_date": env.surgery_date(pid)})
    monkeypatch.setattr(patient_auth, "record_event", failing)
    r = env.client.post("/patient/consent", headers=headers,
                        json={"consent_text_version": VERSION, "consents": BOTH})
    assert r.status_code == 500 and count(env.c, "consents") == 0
    assert env.account(pid)["status"] == "invited"


def test_a_clock_is_available_for_the_session_check():
    assert clock_module.now().tzinfo is not None


_ = (Jsonb, patient_msg)  # imports kept for readers extending these tests


def test_the_client_address_comes_from_our_own_proxy_not_from_the_caller(env):
    env.settings.patient_auth_per_ip_per_minute = 2
    env.settings.trusted_proxy_hops = 1
    spoof = "9.9.9.9, 1.1.1.1"  # the caller wrote "9.9.9.9"; our proxy appended the real 1.1.1.1

    def ask(header):
        return env.client.post("/patient-auth/request-code", json={"email": "x@mail.org"},
                               headers={"x-forwarded-for": header})

    assert [ask(spoof).status_code for _ in range(2)] == [202, 202]
    assert ask("8.8.8.8, 1.1.1.1").status_code == 429  # same real address, new forged one
    assert ask("1.1.1.1, 2.2.2.2").status_code == 202  # a different real address
    env.settings.trusted_proxy_hops = 0  # not behind a proxy: the header means nothing
    assert [ask(f"7.7.7.{n}").status_code for n in range(3)] == [202, 202, 429]
