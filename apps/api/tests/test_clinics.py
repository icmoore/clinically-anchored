"""/me and the patient list/create: auth, tenant scoping, audit. Creating a patient creates
their episode too (the clinic enters the surgery date and procedure)."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.core import episodes as episodes_core
from clinically_anchored_api.main import app
from tests.fakes import (
    AUTH,
    CLINIC_A,
    CLINIC_B,
    NOW,
    PROC_A,
    USER,
    V2_ROW,
    FakeSupabase,
    install,
    pin_clock,
)

A, B = CLINIC_A, CLINIC_B
NEW = {"full_name": "  Test Person ", "contact": " 555-0100 ", "surgery_date": "2026-10-01",
       "procedure_id": PROC_A}


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase(role="owner")
    f.tables["patients"] = [
        {"id": "p1", "clinic_id": A, "full_name": "Zed", "contact": None,
         "created_at": "2026-01-01T00:00:00Z"},
        {"id": "p0", "clinic_id": A, "full_name": "Amy", "contact": "amy@example.com",
         "created_at": "2026-01-01T00:00:00Z"},
        {"id": "pb", "clinic_id": B, "full_name": "Other", "contact": None,
         "created_at": "2026-01-01T00:00:00Z"},
    ]
    pin_clock(monkeypatch)
    f.audits = install(monkeypatch, f)
    return f


@pytest.fixture
def client(fake):
    return TestClient(app)


def test_me_lists_only_my_clinics(client):
    body = client.get("/me", headers=AUTH).json()
    assert body["email"] == "u@example.com"
    assert body["clinics"] == [{"id": A, "name": "A", "role": "owner", "is_demo": False}]


def test_me_requires_auth(client):
    assert client.get("/me").status_code == 401


def test_patient_endpoints_are_tenant_scoped(client):
    assert client.get(f"/clinics/{B}/patients", headers=AUTH).status_code == 403
    assert client.get(f"/clinics/{B}/patients/pb", headers=AUTH).status_code == 403
    assert client.post(f"/clinics/{B}/patients", headers=AUTH, json=NEW).status_code == 403


def _page(fake, items=(), total=None):
    """Register a canned `list_patients_page` (what the database does is tested in
    tests/db/test_patient_list.py); returns the list the api's calls are recorded in."""
    calls = []

    def handler(params):
        calls.append(params)
        return {"items": list(items), "total": len(items) if total is None else total}

    fake.rpc_handlers["list_patients_page"] = handler
    return calls


def _row(patient, episode=None, account_status="none"):
    return {"id": patient["id"], "full_name": patient["full_name"], "contact": patient["contact"],
            "email": patient.get("email"), "created_at": patient["created_at"],
            "account_status": account_status, "episode": episode}


def test_the_list_is_one_database_page_with_state_computed_only_for_that_page(
    client, fake, monkeypatch
):
    mine = fake.add_episode(id="e-new", patient="p0", surgery_date="2026-10-01",
                            checkin_completed_at="2026-10-02T12:00:00+00:00")
    fake.add_episode(id="e-other", patient="p1", surgery_date="2026-09-01")  # not on the page
    page = [_row(fake.tables["patients"][1], mine, "locked")]
    calls = _page(fake, page, total=48)
    seen = []
    real = episodes_core.states_for
    monkeypatch.setattr(
        episodes_core, "states_for",
        lambda sb, eps, *a, **k: seen.append([e["id"] for e in eps]) or real(sb, eps, *a, **k),
    )

    body = client.get(f"/clinics/{A}/patients", headers=AUTH).json()

    assert (body["total"], body["limit"], body["offset"]) == (48, 5, 0)
    (item,) = body["items"]
    assert (item["full_name"], item["account_status"]) == ("Amy", "locked")
    ep = item["episode"]
    assert ep["id"] == "e-new" and ep["timezone"] == "America/Toronto"
    assert (ep["state"]["phase"], ep["state"]["interval_no"], ep["state"]["post_op_day"]) == (
        "open", 1, 2,
    )
    assert seen == [["e-new"]]
    assert calls == [{
        "p_clinic_id": A, "p_now": NOW.isoformat(), "p_status": "active", "p_surgery_from": None,
        "p_surgery_to": None, "p_q": None, "p_limit": 5, "p_offset": 0,
    }]


def test_a_patient_with_no_care_period_comes_back_without_an_episode(client, fake):
    _page(fake, [_row(fake.tables["patients"][0])])
    (item,) = client.get(f"/clinics/{A}/patients?status=all", headers=AUTH).json()["items"]
    assert item["episode"] is None and item["email"] is None


def test_filters_are_passed_to_the_database(client, fake):
    calls = _page(fake)
    r = client.get(
        f"/clinics/{A}/patients?status=ended&surgery_from=2026-09-01&surgery_to=2026-09-30"
        "&q=%20%20am%20%20&limit=50&offset=100", headers=AUTH)
    assert r.status_code == 200
    (params,) = calls
    assert (params["p_status"], params["p_surgery_from"], params["p_surgery_to"], params["p_q"],
            params["p_limit"], params["p_offset"]) == (
        "ended", "2026-09-01", "2026-09-30", "am", 50, 100)
    assert (r.json()["surgery_from"], r.json()["surgery_to"]) == ("2026-09-01", "2026-09-30")


@pytest.mark.parametrize(
    "query, from_, to",
    [
        ("surgery_range=this_week", "2026-09-28", "2026-10-04"),  # NOW is Saturday 3 Oct, Toronto
        ("surgery_range=last_week", "2026-09-21", "2026-09-27"),
        ("surgery_range=this_month", "2026-10-01", "2026-10-31"),
        ("surgery_range=last_month", "2026-09-01", "2026-09-30"),
        ("surgery_month=2026-02", "2026-02-01", "2026-02-28"),
        ("surgery_month=2028-02", "2028-02-01", "2028-02-29"),
        ("surgery_from=2026-09-01", "2026-09-01", None),
        ("surgery_to=2026-09-30", None, "2026-09-30"),
    ],
)
def test_the_surgery_filter_resolves_to_inclusive_dates(client, fake, query, from_, to):
    calls = _page(fake)
    assert client.get(f"/clinics/{A}/patients?{query}", headers=AUTH).status_code == 200
    assert (calls[0]["p_surgery_from"], calls[0]["p_surgery_to"]) == (from_, to)


@pytest.mark.parametrize(
    "query",
    [
        "limit=51", "limit=0", "limit=-1", "limit=abc", "offset=-1", "offset=abc",
        "status=bogus", "status=", "surgery_range=next_week", "surgery_month=2026-13",
        "surgery_month=2026-00", "surgery_month=0000-01", "surgery_month=2026-1",
        "surgery_from=yesterday", "surgery_to=2026-02-30", "q=" + "x" * 101,
        "surgery_range=this_week&surgery_month=2026-02",
        "surgery_range=this_week&surgery_from=2026-09-01",
        "surgery_month=2026-02&surgery_to=2026-09-01",
        "surgery_from=2026-10-02&surgery_to=2026-10-01",
    ],
)
def test_a_bad_parameter_is_refused_before_the_database_is_asked(client, fake, query):
    calls = _page(fake)
    assert client.get(f"/clinics/{A}/patients?{query}", headers=AUTH).status_code == 422
    assert calls == []


def test_a_blank_search_means_no_search(client, fake):
    calls = _page(fake)
    client.get(f"/clinics/{A}/patients?q=%20%20", headers=AUTH)
    assert calls[0]["p_q"] is None


def test_get_one_patient_carries_the_latest_episode_and_account_status(client, fake):
    fake.add_episode(id="e-old", patient="p0", surgery_date="2026-08-01",
                     created_at="2026-08-01T00:00:00+00:00",
                     closed_at="2026-09-20T12:00:00+00:00", close_reason="schedule_complete")
    fake.add_episode(id="e-new", patient="p0", surgery_date="2026-10-01",
                     checkin_completed_at="2026-10-02T12:00:00+00:00",
                     created_at="2026-10-01T00:00:00+00:00")
    fake.tables["patients"][1]["email"] = "amy@mail.org"
    fake.tables["patient_accounts"].append(
        {"id": "a1", "clinic_id": A, "patient_id": "p0", "user_id": USER, "status": "locked"})
    body = client.get(f"/clinics/{A}/patients/p0", headers=AUTH).json()
    assert (body["full_name"], body["email"], body["account_status"]) == (
        "Amy", "amy@mail.org", "locked")
    assert body["episode"]["id"] == "e-new" and body["episode"]["state"]["phase"] == "open"
    zed = client.get(f"/clinics/{A}/patients/p1", headers=AUTH).json()
    assert zed["episode"] is None and zed["account_status"] == "none"


def test_get_one_patient_of_another_clinic_is_not_found(client):
    assert client.get(f"/clinics/{A}/patients/pb", headers=AUTH).status_code == 404
    assert client.get(f"/clinics/{A}/patients/nobody", headers=AUTH).status_code == 404


def test_create_patient_also_creates_the_episode_and_audits_both(client, fake):
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert r.status_code == 200
    body = r.json()
    assert (body["full_name"], body["contact"]) == ("Test Person", "555-0100")
    (episode,) = fake.tables["episodes"]
    assert (episode["patient_id"], episode["surgery_date"], episode["procedure_id"],
            episode["schedule_version"], episode["created_by"]) == (
        body["id"], "2026-10-01", PROC_A, 2, USER,  # the newest active schedule: v2
    )
    assert body["episode"]["id"] == episode["id"]
    assert body["episode"]["state"]["phase"] == "locked_checkin"

    patient_event, episode_event = fake.audits
    assert patient_event["event_type"] == "patient.created"
    assert "Test Person" not in str(patient_event["metadata"])
    assert "555-0100" not in str(patient_event["metadata"])
    assert patient_event["metadata"]["actor"]["role"] == "owner"
    assert episode_event["event_type"] == "episode.created"
    assert episode_event["metadata"]["ref_id"] == episode["id"]
    assert episode_event["metadata"]["schedule_version"] == 2
    snapshot = episode_event["payload"]["schedule"]  # what the episode was created under
    assert snapshot["offsets_days"] == [2, 5, 10, 17, 24, 31, 38, 45, 52]
    assert snapshot["close_after_days"] == 7
    assert snapshot["opens_at_local"] == "07:00:00" and snapshot["timezone"] == "America/Toronto"


def _version(fake):
    return fake.tables["episodes"][-1]["schedule_version"]


def test_a_new_patient_gets_the_newest_active_schedule(client, fake):
    client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert _version(fake) == 2


def test_a_newer_version_that_is_not_active_is_skipped(client, fake):
    # Staged for review: inserted with active = false, so it is not the default yet.
    fake.tables["message_schedules"].append({**V2_ROW, "version": 3, "active": False})
    client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert _version(fake) == 2
    fake.tables["message_schedules"][-1]["active"] = True  # flipped on: now it is the default
    client.post(f"/clinics/{A}/patients", headers=AUTH, json={**NEW, "full_name": "Second"})
    assert _version(fake) == 3


def test_retiring_the_newest_version_falls_back_to_the_one_before(client, fake):
    fake.tables["message_schedules"][1]["active"] = False  # v2 off
    client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert _version(fake) == 1


def test_no_active_schedule_is_a_clear_error_and_writes_no_episode(client, fake):
    for row in fake.tables["message_schedules"]:
        row["active"] = False
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert r.status_code == 503 and "schedule" in r.json()["detail"]
    assert fake.tables["episodes"] == []


def test_an_existing_episode_keeps_the_version_it_recorded(client, fake):
    # A v1 episode made before v2 existed, and a patient added now (v2), side by side.
    fake.add_episode(id="e-v1", patient="p0", surgery_date="2026-10-01", schedule_version=1,
                     checkin_completed_at="2026-10-02T12:00:00+00:00")
    created = client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW).json()
    old = client.get(f"/clinics/{A}/patients/p0", headers=AUTH).json()["episode"]
    new = client.get(f"/clinics/{A}/patients/{created['id']}", headers=AUTH).json()["episode"]
    assert (old["schedule_version"], new["schedule_version"]) == (1, 2)
    # NOW is Saturday 3 Oct 07:30 Toronto, day 2 after Thursday 1 Oct surgery. Under v1 that is
    # window 1 (opened 3 Oct 07:00, closes 4 Oct); under v2 window 1 runs to day 5, 6 Oct.
    assert old["state"]["interval_closes_at"].startswith("2026-10-04T11:00")
    assert new["state"]["intervals_total"] == old["state"]["intervals_total"] == 9
    assert new["state"]["episode_closes_at"].startswith("2026-11-29T12:00")  # day 59
    assert old["state"]["episode_closes_at"].startswith("2026-11-19T12:00")  # v1's day 49


@pytest.mark.parametrize(
    "change",
    [
        {"full_name": ""},
        {"surgery_date": "not-a-date"},
        {"surgery_date": "2030-01-01"},  # more than a year away
        {"surgery_date": None},
        {"procedure_id": None},
    ],
)
def test_create_patient_validates_input(client, fake, change):
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json={**NEW, **change})
    assert r.status_code == 422
    assert len(fake.tables["patients"]) == 3 and fake.audits == []


def test_a_procedure_of_another_clinic_or_an_inactive_one_is_rejected_before_anything_is_written(
    client, fake
):
    fake.tables["procedures"].append(
        {"id": "pr-b", "clinic_id": B, "name": "x", "category": "other", "active": True}
    )
    fake.tables["procedures"].append(
        {"id": "pr-off", "clinic_id": A, "name": "x", "category": "other", "active": False}
    )
    for proc in ("pr-b", "pr-off", "nope"):
        r = client.post(f"/clinics/{A}/patients", headers=AUTH, json={**NEW, "procedure_id": proc})
        assert r.status_code == 422
    assert len(fake.tables["patients"]) == 3 and fake.audits == []


def test_a_surgery_date_whose_schedule_has_already_ended_is_rejected(client, fake):
    old = {**NEW, "surgery_date": "2026-06-01"}
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json=old)
    assert r.status_code == 422 and "already ended" in r.json()["detail"]
    assert len(fake.tables["patients"]) == 3


def test_if_the_episode_cannot_be_saved_the_patient_is_taken_back_out(client, fake):
    fake.fail_insert["episodes"] = RuntimeError("episodes_one_open_per_patient")
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json=NEW)
    assert r.status_code == 409
    assert [p["id"] for p in fake.tables["patients"]].count("pb") == 1
    assert len(fake.tables["patients"]) == 3  # the new one is gone again
    assert fake.tables["episodes"] == []


def test_a_new_patient_can_come_with_the_email_their_account_invite_goes_to(client, fake):
    r = client.post(f"/clinics/{A}/patients", headers=AUTH,
                    json={**NEW, "email": "  Test.Person@Mail.ORG "})
    assert r.status_code == 200 and r.json()["email"] == "test.person@mail.org"
    assert fake.tables["patients"][-1]["email"] == "test.person@mail.org"
    assert "test.person@mail.org" not in str(fake.audits[0]["metadata"])  # hashed, not stored
    no_email = client.post(f"/clinics/{A}/patients", headers=AUTH, json={**NEW, "email": "  "})
    assert no_email.json()["email"] is None


@pytest.mark.parametrize("bad", ["nope", "a@b", "two words@mail.org", "a@@b.org"])
def test_a_malformed_patient_email_is_refused_before_anything_is_written(client, fake, bad):
    r = client.post(f"/clinics/{A}/patients", headers=AUTH, json={**NEW, "email": bad})
    assert r.status_code == 422
    assert len(fake.tables["patients"]) == 3 and fake.audits == []


def test_a_patient_carries_the_email_their_account_invite_goes_to(client, fake):
    fake.tables["patients"][1]["email"] = "amy@mail.org"
    assert client.get(f"/clinics/{A}/patients/p0", headers=AUTH).json()["email"] == "amy@mail.org"
    assert client.get(f"/clinics/{A}/patients/p1", headers=AUTH).json()["email"] is None


def test_me_says_which_clinic_is_a_demo_clinic(client, fake):
    fake.tables["clinics"][0]["is_demo"] = True
    assert client.get("/me", headers=AUTH).json()["clinics"][0]["is_demo"] is True
