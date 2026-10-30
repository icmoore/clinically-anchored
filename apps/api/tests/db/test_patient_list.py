"""Migration 19 and the paged patient list against a real Postgres: every filter, the search, the
sort order, paging and the count, the limit cap, clinic scoping, week and month boundaries in
Toronto time (clock-change weeks included), and Active / Ended at the schedule's end. The last
section holds the SQL helpers to core/schedule.py for every state the trial seed makes."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from clinically_anchored_api.core import clock, episodes
from clinically_anchored_api.core.schedule import Schedule, episode_end
from clinically_anchored_api.main import app
from tests.db.conftest import (
    ask_extension,
    build_world,
    check_in,
    decide_extension,
    end_by_schedule,
    make_episode,
    new_patient,
)
from tests.db.pg_supabase import PgSupabase
from tests.db.test_seed import NOW as SEED_NOW
from tests.db.test_seed import env as seed_env  # noqa: F401  (the seed's own fixture)
from tests.db.test_seed import run_seed
from tests.fakes import install

pytestmark = pytest.mark.db

# Wednesday 14 Oct 2026, 15:30 in Toronto. Schedule v2 ends on day 59 at 07:00 Toronto, so a
# surgery on 17 Aug is still running (ends 15 Oct) and one on 16 Aug ended this morning.
NOW = datetime(2026, 10, 14, 19, 30, tzinfo=UTC)


@pytest.fixture
def lenv(db, monkeypatch):
    conn = db.conn
    w = build_world(conn)
    conn.execute("delete from patients")  # the tests add their own
    pg = PgSupabase(conn)
    install(monkeypatch, pg)
    now = [NOW]
    monkeypatch.setattr(clock, "now", lambda: now[0])

    class Env:
        pass

    e = Env()
    e.conn, e.w, e.pg, e.now, e.client = conn, w, pg, now, TestClient(app)
    e.auth = {"Authorization": f"Bearer user:{w.user_a}"}
    e.url = f"/clinics/{w.clinic_a}/patients"

    def add(name, surgery=None, *, clinic="a", version=2, discharged=False, settled=False):
        clinic_id, proc, user = (
            (w.clinic_a, w.proc_a, w.user_a) if clinic == "a" else (w.clinic_b, w.proc_b, w.user_b)
        )
        patient = str(conn.execute(
            "insert into patients (clinic_id, full_name) values (%s, %s) returning id",
            (clinic_id, name),
        ).fetchone()["id"])
        if surgery:
            episode = make_episode(
                conn, w, patient=patient, clinic=clinic_id, procedure=proc, user=user,
                surgery_date=date.fromisoformat(surgery), version=version,
            )
            if discharged:
                conn.execute(
                    "select close_episode(%s, %s, 'discharged', %s)", (clinic_id, episode, user)
                )
            if settled:
                end_by_schedule(conn, w, episode)
        return patient

    def get(query="", expect=200, headers=None):
        r = e.client.get(f"{e.url}?{query}", headers=headers or e.auth)
        assert r.status_code == expect, r.text
        return r.json()

    def names(query=""):
        return [p["full_name"] for p in get(query)["items"]]

    e.add, e.get, e.names = add, get, names
    return e


def populate(e):
    """A clinic with every kind of patient. Active means a care period still running."""
    e.add("Alice Adams", "2026-10-14")
    e.add("bob brown", "2026-10-13")
    e.add("Carol Clark", "2026-10-13")  # same surgery date as bob: name decides (case-insensitive)
    e.add("Dan Dole", "2026-10-06")  # last week (Mon 5 - Sun 11 Oct)
    e.add("Eve Evans", "2026-09-30")  # last month
    e.add("Frank Fox", "2026-08-17")  # ends 15 Oct 07:00: still active
    e.add("Gina Gray", "2026-08-16")  # ended 14 Oct 07:00 by the schedule, never settled
    e.add("Hank Hill", "2026-10-10", discharged=True)
    e.add("Sam Settled", "2026-07-01", settled=True)
    e.add("Iris Ives")  # no care period
    e.add("Alice Elsewhere", "2026-10-14", clinic="b")


ACTIVE = ["Alice Adams", "bob brown", "Carol Clark", "Dan Dole", "Eve Evans", "Frank Fox"]
ENDED = ["Hank Hill", "Gina Gray", "Sam Settled"]


# --- status ---------------------------------------------------------------------------------------


def test_active_is_the_default_and_means_still_in_follow_up(lenv):
    populate(lenv)
    assert lenv.names("limit=50") == ACTIVE
    assert lenv.get("limit=50")["total"] == 6
    assert lenv.names("limit=50&status=active") == ACTIVE


def test_ended_has_discharged_and_schedule_ended_care_periods(lenv):
    populate(lenv)
    page = lenv.get("limit=50&status=ended")
    # Discharged (10 Oct), then the two that ran out, newest surgery first; settled or not.
    assert [p["full_name"] for p in page["items"]] == ENDED and page["total"] == 3
    assert {p["full_name"]: p["episode"]["close_reason"] for p in page["items"]} == {
        "Hank Hill": "discharged", "Gina Gray": None, "Sam Settled": "schedule_complete",
    }
    assert all(p["episode"]["state"]["phase"] == "closed" for p in page["items"])
    assert all(p["episode"]["state"]["phase"] != "closed" for p in lenv.get("limit=50")["items"])


def test_a_patient_with_no_care_period_is_under_all_only(lenv):
    populate(lenv)
    assert "Iris Ives" not in lenv.names("limit=50&status=active")
    assert "Iris Ives" not in lenv.names("limit=50&status=ended")
    everyone = lenv.get("limit=50&status=all")
    assert everyone["total"] == 10 and everyone["items"][-1]["full_name"] == "Iris Ives"
    assert everyone["items"][-1]["episode"] is None


def test_status_follows_the_patients_latest_care_period(lenv):
    # An earlier period that ended, then a newer one that is running: the patient is active.
    patient = lenv.add("Repeat Patient")
    insert = (
        "insert into episodes (clinic_id, patient_id, procedure_id, surgery_date, "
        "schedule_version, created_by, created_at) values (%s, %s, %s, %s, 2, %s, %s) returning id"
    )
    first = lenv.conn.execute(insert, (
        lenv.w.clinic_a, patient, lenv.w.proc_a, "2026-03-01", lenv.w.user_a, "2026-03-01T12:00Z",
    )).fetchone()["id"]
    end_by_schedule(lenv.conn, lenv.w, str(first), "2026-05-01T12:00:00+00:00")
    lenv.conn.execute(insert, (
        lenv.w.clinic_a, patient, lenv.w.proc_a, "2026-10-01", lenv.w.user_a, "2026-10-01T12:00Z",
    ))
    assert lenv.names("status=active") == ["Repeat Patient"]
    assert lenv.names("status=ended") == []
    assert lenv.get("status=all")["items"][0]["episode"]["surgery_date"] == "2026-10-01"


# --- surgery date -----------------------------------------------------------------------------------


def test_named_ranges_resolve_in_clinic_time(lenv):
    populate(lenv)
    this_week = lenv.get("limit=50&status=all&surgery_range=this_week")
    assert [p["full_name"] for p in this_week["items"]] == [
        "Alice Adams", "bob brown", "Carol Clark"]
    assert (this_week["surgery_from"], this_week["surgery_to"]) == ("2026-10-12", "2026-10-18")
    assert lenv.names("limit=50&status=all&surgery_range=last_week") == ["Hank Hill", "Dan Dole"]
    assert lenv.names("limit=50&status=all&surgery_range=this_month") == [
        "Alice Adams", "bob brown", "Carol Clark", "Hank Hill", "Dan Dole"]
    assert lenv.names("limit=50&status=all&surgery_range=last_month") == ["Eve Evans"]


def test_a_picked_month_and_explicit_dates_are_inclusive(lenv):
    populate(lenv)
    assert lenv.names("limit=50&status=all&surgery_month=2026-08") == ["Frank Fox", "Gina Gray"]
    assert lenv.names("limit=50&status=all&surgery_month=2026-07") == ["Sam Settled"]
    assert lenv.names("limit=50&status=all&surgery_month=2025-01") == []
    both_ends = "limit=50&status=all&surgery_from=2026-10-06&surgery_to=2026-10-13"
    assert lenv.names(both_ends) == ["bob brown", "Carol Clark", "Hank Hill", "Dan Dole"]
    assert lenv.names("limit=50&status=all&surgery_from=2026-10-14") == ["Alice Adams"]
    assert lenv.names("limit=50&status=all&surgery_to=2026-07-01") == ["Sam Settled"]


def test_a_surgery_filter_leaves_out_patients_with_no_care_period(lenv):
    populate(lenv)
    assert "Iris Ives" not in lenv.names("limit=50&status=all&surgery_from=1900-01-01")
    assert "Iris Ives" not in lenv.names("limit=50&status=all&surgery_to=2999-01-01")


def test_filters_combine(lenv):
    populate(lenv)
    assert lenv.names("status=ended&surgery_range=last_week") == ["Hank Hill"]
    assert lenv.names("status=active&surgery_range=this_week&q=CAR") == ["Carol Clark"]
    assert lenv.get("status=active&surgery_range=last_month&q=zzz")["total"] == 0


# Week and month edges, and the two clock-change weeks. Surgery dates are dates and "today" is the
# clinic's calendar date, so 23:30 on Sunday in Toronto is still Sunday although UTC says Monday.
# Each case: now (UTC), the range, the dates it must hold and the neighbours it must not.
def _edge(now, name, inside, outside):
    return pytest.param(now, name, inside, outside, id=f"{now:%Y-%m-%dT%H:%MZ}-{name}")


def _utc(*a):
    return datetime(*a, tzinfo=UTC)


EDGES = [
    _edge(_utc(2026, 10, 19, 3, 30), "this_week", ["2026-10-18", "2026-10-12"],
          ["2026-10-11", "2026-10-19"]),  # Sunday 23:30 EDT
    _edge(_utc(2026, 10, 19, 4, 30), "this_week", ["2026-10-19", "2026-10-25"],
          ["2026-10-18", "2026-10-26"]),  # Monday 00:30 EDT
    _edge(_utc(2026, 10, 19, 4, 30), "last_week", ["2026-10-18", "2026-10-12"],
          ["2026-10-11", "2026-10-19"]),
    _edge(_utc(2026, 11, 1, 3, 30), "this_month", ["2026-10-31", "2026-10-01"],
          ["2026-09-30", "2026-11-01"]),  # 31 Oct 23:30 EDT
    _edge(_utc(2026, 11, 1, 4, 30), "this_month", ["2026-11-01", "2026-11-30"],
          ["2026-10-31", "2026-12-01"]),  # 1 Nov 00:30 EDT
    _edge(_utc(2026, 11, 1, 4, 30), "last_month", ["2026-10-31", "2026-10-01"],
          ["2026-09-30", "2026-11-01"]),
    _edge(_utc(2027, 2, 1, 4, 30), "this_month", ["2027-01-31", "2027-01-01"],
          ["2026-12-31", "2027-02-01"]),  # 31 Jan 23:30 EST
    _edge(_utc(2027, 2, 1, 5, 30), "this_month", ["2027-02-01", "2027-02-28"],
          ["2027-01-31", "2027-03-01"]),  # 1 Feb 00:30 EST
    # Spring forward, Sunday 9 March 2025: the week is still seven calendar days
    _edge(_utc(2025, 3, 10, 3, 30), "this_week", ["2025-03-09", "2025-03-03"],
          ["2025-03-02", "2025-03-10"]),
    _edge(_utc(2025, 3, 10, 4, 30), "this_week", ["2025-03-10", "2025-03-16"],
          ["2025-03-09", "2025-03-17"]),
    _edge(_utc(2025, 3, 10, 4, 30), "last_week", ["2025-03-09", "2025-03-03"],
          ["2025-03-02", "2025-03-10"]),
    _edge(_utc(2025, 3, 6, 17, 0), "this_week", ["2025-03-03", "2025-03-09"],
          ["2025-03-02", "2025-03-10"]),  # midweek, before the change
    # Fall back, Sunday 2 November 2025
    _edge(_utc(2025, 11, 3, 4, 30), "this_week", ["2025-11-02", "2025-10-27"],
          ["2025-10-26", "2025-11-03"]),
    _edge(_utc(2025, 11, 3, 5, 30), "this_week", ["2025-11-03", "2025-11-09"],
          ["2025-11-02", "2025-11-10"]),
    _edge(_utc(2025, 11, 3, 5, 30), "last_week", ["2025-11-02", "2025-10-27"],
          ["2025-10-26", "2025-11-03"]),
]


@pytest.mark.parametrize("now, name, inside, outside", EDGES)
def test_week_and_month_edges_in_toronto_time(lenv, now, name, inside, outside):
    for d in [*inside, *outside]:  # one patient per date, named by it (status=all: long past)
        lenv.add(d, d)
    lenv.now[0] = now
    page = lenv.get(f"status=all&limit=50&surgery_range={name}")
    assert [p["full_name"] for p in page["items"]] == sorted(inside, reverse=True)
    assert page["total"] == len(inside)


# --- search -----------------------------------------------------------------------------------------


def test_search_is_case_insensitive_and_partial(lenv):
    populate(lenv)
    assert lenv.names("status=all&limit=50&q=alice") == ["Alice Adams"]
    assert lenv.names("status=all&limit=50&q=BROWN") == ["bob brown"]
    assert lenv.names("status=all&limit=50&q=ar") == ["Carol Clark"]
    assert lenv.names("status=all&limit=50&q=%20%20e%20%20")[0] == "Alice Adams"  # trimmed
    assert lenv.get("status=all&q=nobody")["total"] == 0


def test_search_treats_percent_underscore_and_backslash_as_plain_characters(lenv):
    for name in ("Q_Underscore", "Q%Percent", "QxOther", "Back\\slash"):
        lenv.add(name)
    assert lenv.names("status=all&q=q_") == ["Q_Underscore"]
    assert lenv.names("status=all&q=q%25") == ["Q%Percent"]
    assert lenv.names("status=all&q=%25") == ["Q%Percent"]
    assert lenv.names("status=all&q=%5C") == ["Back\\slash"]
    assert sorted(lenv.names("status=all&limit=50&q=q")) == ["Q%Percent", "Q_Underscore", "QxOther"]


def test_the_search_only_matches_the_name(lenv):
    patient = lenv.add("Plain Name", "2026-10-10")
    lenv.conn.execute(
        "update patients set contact = '555-0100', email = 'secret@mail.org' where id = %s",
        (patient,),
    )
    assert lenv.names("q=secret") == [] and lenv.names("q=555") == []


# --- sort and paging --------------------------------------------------------------------------------


def test_newest_surgery_first_then_name_and_no_care_period_last(lenv):
    populate(lenv)
    assert lenv.names("status=all&limit=50") == [
        "Alice Adams",  # 14 Oct
        "bob brown", "Carol Clark",  # 13 Oct, by name regardless of case
        "Hank Hill",  # 10 Oct
        "Dan Dole",  # 6 Oct
        "Eve Evans",  # 30 Sep
        "Frank Fox",  # 17 Aug
        "Gina Gray",  # 16 Aug
        "Sam Settled",  # 1 Jul
        "Iris Ives",  # none
    ]


def test_paging_walks_the_whole_list_with_a_steady_total(lenv):
    populate(lenv)
    seen = []
    for offset in (0, 4, 8, 12):
        page = lenv.get(f"status=all&limit=4&offset={offset}")
        assert page["total"] == 10 and (page["limit"], page["offset"]) == (4, offset)
        seen += [p["full_name"] for p in page["items"]]
    assert seen == lenv.names("status=all&limit=50") and len(seen) == 10
    assert lenv.get("status=all&limit=4&offset=8")["items"][-1]["full_name"] == "Iris Ives"


def test_a_page_past_the_end_is_empty_but_the_total_is_still_right(lenv):
    populate(lenv)
    page = lenv.get("status=all&limit=5&offset=500")
    assert page["items"] == [] and page["total"] == 10


def test_the_default_page_is_five(lenv):
    populate(lenv)
    page = lenv.get("")
    assert [p["full_name"] for p in page["items"]] == ACTIVE[:5]
    assert (page["total"], page["limit"], page["offset"]) == (6, 5, 0)


def test_equal_names_and_dates_page_without_repeats_or_gaps(lenv):
    for _ in range(7):
        lenv.add("Same Name", "2026-10-10")
    ids = []
    for offset in (0, 3, 6):
        ids += [p["id"] for p in lenv.get(f"limit=3&offset={offset}")["items"]]
    assert len(ids) == len(set(ids)) == 7


def test_the_page_limit_is_capped_at_fifty(lenv):
    populate(lenv)
    assert lenv.get("limit=50")["limit"] == 50
    for q in ("limit=51", "limit=0", "limit=-5", "limit=1000", "offset=-1"):
        lenv.get(q, expect=422)


def test_the_items_keep_the_shape_the_ui_uses(lenv):
    patient = lenv.add("Shape Check", "2026-10-10")
    lenv.conn.execute(
        "update patients set contact = '555-0100', email = 'shape@mail.org' where id = %s",
        (patient,),
    )
    (item,) = lenv.get("")["items"]
    assert item["id"] == patient
    assert (item["contact"], item["email"], item["account_status"]) == (
        "555-0100", "shape@mail.org", "none")
    ep = item["episode"]
    assert (ep["surgery_date"], ep["schedule_version"], ep["procedure_name"]) == (
        "2026-10-10", 2, "Proc")
    assert ep["state"]["phase"] == "locked_checkin" and ep["state"]["post_op_day"] == 4
    assert ep["state"]["episode_closes_at"].startswith("2026-12-08T12:00")  # day 59, 07:00 EST


def test_account_status_is_carried_per_patient(lenv):
    locked = lenv.add("Locked One", "2026-10-10")
    lenv.add("Plain One", "2026-10-09")
    user = lenv.conn.execute(
        "insert into auth.users (email) values ('l@example.com') returning id").fetchone()["id"]
    lenv.conn.execute(
        "insert into patient_accounts (clinic_id, patient_id, user_id, status, invited_by, "
        "locked_at) values (%s, %s, %s, 'locked', %s, now())",
        (lenv.w.clinic_a, locked, user, lenv.w.user_a),
    )
    status = {p["full_name"]: p["account_status"] for p in lenv.get("")["items"]}
    assert status == {"Locked One": "locked", "Plain One": "none"}


def test_state_is_computed_only_for_the_patients_on_the_page(lenv, monkeypatch):
    populate(lenv)
    seen = []
    real = episodes.states_for
    monkeypatch.setattr(
        episodes, "states_for",
        lambda sb, eps, *a, **k: seen.append([e["id"] for e in eps]) or real(sb, eps, *a, **k),
    )
    page = lenv.get("status=all&limit=3&offset=3")
    assert len(seen) == 1 and sorted(seen[0]) == sorted(p["episode"]["id"] for p in page["items"])
    assert len(seen[0]) == 3  # not the clinic's nine care periods


# --- clinic scoping ---------------------------------------------------------------------------------


def test_only_this_clinics_patients_are_listed_and_counted(lenv):
    populate(lenv)
    page = lenv.get("status=all&limit=50")
    assert "Alice Elsewhere" not in [p["full_name"] for p in page["items"]]
    assert page["total"] == 10
    other = lenv.client.get(
        f"/clinics/{lenv.w.clinic_b}/patients?status=all", headers={
            "Authorization": f"Bearer user:{lenv.w.user_b}"})
    assert [p["full_name"] for p in other.json()["items"]] == ["Alice Elsewhere"]
    assert other.json()["total"] == 1


def test_a_member_of_one_clinic_cannot_list_another(lenv):
    populate(lenv)
    other = f"/clinics/{lenv.w.clinic_b}/patients"
    assert lenv.client.get(other, headers=lenv.auth).status_code == 403
    assert lenv.client.get(lenv.url).status_code == 401


def test_the_database_function_never_crosses_clinics_either(lenv):
    populate(lenv)
    row = lenv.conn.execute(
        "select list_patients_page(%s, %s, 'all', null, null, null, 50, 0) as r",
        (lenv.w.clinic_b, NOW),
    ).fetchone()["r"]
    assert [i["full_name"] for i in row["items"]] == ["Alice Elsewhere"] and row["total"] == 1


def test_get_one_patient_is_clinic_scoped_and_matches_a_list_row(lenv):
    populate(lenv)
    listed = {p["full_name"]: p for p in lenv.get("status=all&limit=50")["items"]}
    mine = listed["Dan Dole"]
    one = lenv.client.get(f"{lenv.url}/{mine['id']}", headers=lenv.auth)
    assert one.status_code == 200
    got = one.json()
    # (the test adapter prints timestamps with and without trailing zeros: compare the rest)
    for key in ("id", "full_name", "contact", "email", "account_status"):
        assert got[key] == mine[key]
    assert got["episode"]["id"] == mine["episode"]["id"]
    assert got["episode"]["state"] == mine["episode"]["state"]
    assert got["episode"]["surgery_date"] == mine["episode"]["surgery_date"]
    theirs = str(lenv.conn.execute(
        "select id from patients where full_name = 'Alice Elsewhere'").fetchone()["id"])
    assert lenv.client.get(f"{lenv.url}/{theirs}", headers=lenv.auth).status_code == 404
    gone = lenv.client.get(f"{lenv.url}/00000000-0000-0000-0000-000000000000", headers=lenv.auth)
    assert gone.status_code == 404


# --- Active / Ended at the schedule's end -----------------------------------------------------------

# Surgery Tuesday 4 March 2025. Schedule v2 ends on day 59, 2 May 2025 at 07:00 EDT = 11:00 UTC;
# v1 on day 49, 22 April at 07:00 EDT. The clocks went forward on 9 March, inside the period.
SURGERY = "2025-03-04"
END_V2 = datetime(2025, 5, 2, 11, 0, tzinfo=UTC)
END_V1 = datetime(2025, 4, 22, 11, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)


def test_a_care_period_ends_at_the_instant_its_schedule_does(lenv):
    lenv.add("On Schedule V2", SURGERY, version=2)
    lenv.add("On Schedule V1", SURGERY, version=1)
    cases = [
        (END_V1 - SECOND, ["On Schedule V1", "On Schedule V2"], []),
        (END_V1, ["On Schedule V2"], ["On Schedule V1"]),  # v1's last window just closed
        (END_V2 - SECOND, ["On Schedule V2"], ["On Schedule V1"]),
        (END_V2, [], ["On Schedule V1", "On Schedule V2"]),
        (END_V2 + SECOND, [], ["On Schedule V1", "On Schedule V2"]),
    ]
    for now, active, ended in cases:
        lenv.now[0] = now
        assert lenv.names("status=active") == active, now
        assert lenv.names("status=ended") == ended, now
        assert lenv.get("status=all")["total"] == 2
        for p in lenv.get("status=all")["items"]:  # and the api's own state says the same
            assert (p["episode"]["state"]["phase"] == "closed") == (p["full_name"] in ended), now


def test_the_ending_hour_is_wall_clock_across_a_clock_change(lenv):
    # Day 59 lands on Sunday 2 Nov 2025, the day the clocks went back: 07:00 is EST, 12:00 UTC.
    lenv.add("Fall Back", "2025-09-04")
    end = datetime(2025, 11, 2, 12, 0, tzinfo=UTC)
    # And one that ends on 9 March 2025 itself, 07:00 EDT = 11:00 UTC (after the 02:00 jump).
    lenv.add("Spring Forward", "2025-01-09")
    spring = datetime(2025, 3, 9, 11, 0, tzinfo=UTC)
    for now, active in (
        (spring - SECOND, ["Fall Back", "Spring Forward"]), (spring, ["Fall Back"]),
        (end - SECOND, ["Fall Back"]), (end, []),
    ):
        lenv.now[0] = now
        assert lenv.names("status=active") == active, now


def test_a_discharge_ends_the_care_period_even_before_the_schedule_does(lenv):
    lenv.add("Sent Home Early", SURGERY, discharged=True)
    lenv.now[0] = datetime(2025, 3, 5, 12, 0, tzinfo=UTC)
    assert lenv.names("status=active") == [] and lenv.names("status=ended") == ["Sent Home Early"]


def test_a_follow_up_extension_counts_from_when_it_was_accepted(lenv):
    patient = lenv.add("Extended", SURGERY, version=2)
    episode = str(lenv.conn.execute(
        "select id from episodes where patient_id = %s", (patient,)).fetchone()["id"])
    check_in(lenv.conn, lenv.w, episode)
    end_by_schedule(lenv.conn, lenv.w, episode, "2025-05-02T11:00:00+00:00")
    lenv.conn.execute("update message_schedules set active = true where version = 3")
    req = ask_extension(lenv.conn, lenv.w, episode)["request"]["id"]
    decide_extension(lenv.conn, lenv.w, req, "accepted")
    ext = lenv.conn.execute("select * from episodes where kind = 'extension'").fetchone()
    sched = lenv.conn.execute(
        "select * from message_schedules where version = %s", (ext["schedule_version"],)
    ).fetchone()
    # The engine's answer: counted from the day it was accepted, not from the surgery date.
    end = episode_end(ext["surgery_date"], Schedule.from_row(sched), ext["started_at"])
    for now, active in ((end - SECOND, ["Extended"]), (end, [])):
        lenv.now[0] = now
        assert lenv.names("status=active") == active
        assert lenv.names("status=ended") == ([] if active else ["Extended"])


# --- the SQL helpers against core/schedule.py ----------------------------------------------------------


def test_helpers_agree_with_the_schedule_engine_for_every_seeded_state(seed_env):  # noqa: F811
    e = seed_env
    clinic_id, _plans = run_seed(e)
    # Boundary episodes on both sides of a clock change, to go with the seed's own.
    w = build_world(e.conn)
    for d in ("2025-01-09", "2025-09-04", "2024-12-20", "2025-03-04"):
        for version in (1, 2):
            patient = str(e.conn.execute(
                "insert into patients (clinic_id, full_name) values (%s, 'Edge') returning id",
                (w.clinic_a,)).fetchone()["id"])
            make_episode(e.conn, w, patient=patient, surgery_date=date.fromisoformat(d),
                         version=version)
    rows = e.pg.table("episodes").select(episodes.COLUMNS).execute().data
    assert len(rows) >= 17 + 8
    schedules = episodes.schedules_for(e.pg, {r["schedule_version"] for r in rows})
    checked = 0
    for r in rows:
        schedule = schedules[r["schedule_version"]]
        started = episodes.parse_ts(r["started_at"])
        end = episode_end(episodes.parse_date(r["surgery_date"]), schedule, started)
        sql_end = e.conn.execute(
            "select episode_schedule_end(%s, %s, %s, %s) as t",
            (r["kind"], r["surgery_date"], r["started_at"], r["schedule_version"]),
        ).fetchone()["t"]
        assert sql_end == end, r["id"]
        for now in (SEED_NOW, end - SECOND, end, end + SECOND, end - timedelta(days=1),
                    end + timedelta(days=1)):
            want = episodes.states_for(e.pg, [r], now)[r["id"]].closed
            got = e.conn.execute(
                "select episode_is_ended(%s, %s, %s, %s, %s, %s) as t",
                (r["closed_at"], r["kind"], r["surgery_date"], r["started_at"],
                 r["schedule_version"], now),
            ).fetchone()["t"]
            assert got == want, (r["id"], now)
            checked += 1
    assert checked == len(rows) * 6


def test_an_extension_end_counts_from_the_local_date_it_was_accepted(db):
    # Accepted at 22:30 Toronto on 31 May is already 1 June in UTC: the local date wins. Also an
    # acceptance either side of the 9 March 2025 clock change and of the 2 November one.
    c = db.conn
    sched = Schedule.from_row(
        c.execute("select * from message_schedules where version = 3").fetchone())
    for started in (
        datetime(2025, 6, 1, 2, 30, tzinfo=UTC), datetime(2025, 6, 1, 3, 59, tzinfo=UTC),
        datetime(2025, 6, 1, 4, 0, tzinfo=UTC), datetime(2025, 3, 9, 6, 59, tzinfo=UTC),
        datetime(2025, 3, 9, 7, 0, tzinfo=UTC), datetime(2025, 11, 3, 4, 59, tzinfo=UTC),
        datetime(2025, 11, 3, 5, 0, tzinfo=UTC), datetime(2025, 12, 31, 23, 59, tzinfo=UTC),
    ):
        want = episode_end(date(2000, 1, 1), sched, started)  # an extension ignores the surgery
        got = c.execute(
            "select episode_schedule_end('extension', date '2000-01-01', %s, 3) as t", (started,)
        ).fetchone()["t"]
        assert got == want, started
        for now in (want - SECOND, want):
            assert c.execute(
                "select episode_is_ended(null, 'extension', date '2000-01-01', %s, 3, %s) as t",
                (started, now),
            ).fetchone()["t"] == (now >= want)


def test_the_list_agrees_with_the_engine_for_the_seeded_clinic_at_any_time(
    seed_env, monkeypatch  # noqa: F811
):
    e = seed_env
    clinic_id, plans = run_seed(e)
    e.conn.execute(
        "insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'owner') "
        "on conflict do nothing", (clinic_id, e.user_id),
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer user:{e.user_id}"}
    latest = {}
    rows = (
        e.pg.table("episodes").select(episodes.COLUMNS).eq("clinic_id", clinic_id)
        .order("created_at", desc=True).execute().data
    )
    for r in rows:
        latest.setdefault(r["patient_id"], r)
    ends = sorted(
        episode_end(
            episodes.parse_date(r["surgery_date"]),
            episodes.schedules_for(e.pg, {r["schedule_version"]})[r["schedule_version"]],
            episodes.parse_ts(r["started_at"]),
        ) for r in latest.values()
    )
    instants = [SEED_NOW, *[t + d for t in ends[:4] for d in (-SECOND, timedelta(0), SECOND)],
                SEED_NOW + timedelta(days=90)]
    for now in instants:
        monkeypatch.setattr(clock, "now", lambda now=now: now)
        want_active = {
            pid for pid, r in latest.items()
            if not episodes.states_for(e.pg, [r], now)[r["id"]].closed
        }
        by_status = {}
        for status in ("active", "ended", "all"):
            body = client.get(
                f"/clinics/{clinic_id}/patients?status={status}&limit=50", headers=headers
            ).json()
            by_status[status] = {p["id"] for p in body["items"]}
            assert body["total"] == len(body["items"]) == len(by_status[status])
            if status == "ended":  # the api's own state agrees with the filter
                assert all(p["episode"]["state"]["phase"] == "closed" for p in body["items"])
        assert by_status["active"] == want_active, now
        assert by_status["ended"] == set(latest) - want_active, now
        assert by_status["all"] == set(latest) and len(latest) == 17


# --- the migration itself ---------------------------------------------------------------------------


def test_19_only_the_service_role_can_call_the_list_functions(db):
    c = db.conn
    w = build_world(c)
    for role in ("anon", "authenticated"):
        c.execute(f"set role {role}")
        try:
            for sql in (
                "select list_patients_page(%s, now(), 'all', null, null, null, 5, 0)",
                "select episode_is_ended(null, 'main', current_date, null, 2, now())",
                "select episode_schedule_end('main', current_date, null, 2)",
            ):
                with pytest.raises(errors.InsufficientPrivilege):
                    c.execute(sql, (w.clinic_a,) if "%s" in sql else ())
        finally:
            c.execute("reset role")
    c.execute("set role service_role")
    try:
        assert c.execute(
            "select list_patients_page(%s, now()) as r", (w.clinic_a,)).fetchone()["r"]["total"] == 0
    finally:
        c.execute("reset role")


@pytest.mark.parametrize(
    "args, message",
    [
        ("'bogus'", "patient_list_bad_status"),
        ("'all', null, null, null, 51, 0", "patient_list_bad_page"),
        ("'all', null, null, null, 0, 0", "patient_list_bad_page"),
        ("'all', null, null, null, 5, -1", "patient_list_bad_page"),
        ("'all', '2026-10-02', '2026-10-01'", "patient_list_bad_dates"),
    ],
)
def test_19_the_function_refuses_bad_arguments_itself(db, args, message):
    w = build_world(db.conn)
    with pytest.raises(Exception, match=message):
        db.conn.execute(f"select list_patients_page('{w.clinic_a}', now(), {args})")


def test_19_an_unknown_schedule_version_is_never_ended_by_the_helpers(db):
    # Cannot happen (episodes reference a version), but the helper must not invent an end.
    c = db.conn
    assert c.execute(
        "select episode_schedule_end('main', current_date, null, 999) as t").fetchone()["t"] is None


def test_19_the_latest_episode_lookup_has_its_index(db):
    c = db.conn
    assert c.execute(
        "select indexdef from pg_indexes where indexname = 'episodes_patient_latest_idx'"
    ).fetchone()
    w = build_world(c)
    for patient in (w.patient_a, w.patient_a2, new_patient(c, w)):
        make_episode(c, w, patient=patient)
    c.execute("set enable_seqscan = off")
    plan = "\n".join(r["QUERY PLAN"] for r in c.execute(
        "explain select * from episodes where patient_id = %s order by created_at desc, id desc "
        "limit 1", (w.patient_a,)).fetchall())
    assert "episodes_patient_latest_idx" in plan, plan
