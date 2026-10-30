"""Migration 18 against a real Postgres: what a patient's Supabase Auth user can reach directly in
the database (nothing), the rules of the account tables, and the atomic steps the api relies on."""

import pytest
from psycopg import errors

from tests.db.conftest import build_world, check_in, clinician_msg, make_episode, migration_files

pytestmark = pytest.mark.db


def raises_text(text: str):
    return pytest.raises(errors.RaiseException, match=text)


def as_user(c, role, sub):
    c.execute(f"set role {role}")
    c.execute("select set_config('request.jwt.claim.sub', %s, false)", (sub,))


def user(c, email):
    return str(c.execute("insert into auth.users (email) values (%s) returning id", (email,)
                         ).fetchone()["id"])


def account(c, w, patient=None, u=None, status="invited", **extra):
    cols = {"clinic_id": w.clinic_a, "patient_id": patient or w.patient_a,
            "user_id": u or user(c, f"p{abs(hash(str(extra)+status))}@x.org"),
            "status": status, "invited_by": w.user_a, **extra}
    return str(c.execute(
        f"insert into patient_accounts ({', '.join(cols)}) values "
        f"({', '.join(['%s'] * len(cols))}) returning id", list(cols.values())).fetchone()["id"])


def call(c, sql, *params):
    return c.execute(f"select {sql} as r", params).fetchone()["r"]


# --- patients.email ------------------------------------------------------------------------


def test_existing_contacts_that_are_email_addresses_become_the_patient_email(db_pre18):
    c = db_pre18.conn
    w = build_world(c)
    rows = {}
    for contact in ("Pat@Mail.ORG ", "905-555-0100", "not an email", "a@b", "x@y.z", None,
                    "two@@mail.org", "spaces in@mail.org"):
        rows[contact] = str(c.execute(
            "insert into patients (clinic_id, full_name, contact) values (%s, 'P', %s) "
            "returning id", (w.clinic_a, contact)).fetchone()["id"])
    migration = [f for f in migration_files(18) if f.name.startswith("00000000000018")][0]
    c.execute(migration.read_text())
    got = {k: c.execute("select email from patients where id = %s", (v,)).fetchone()["email"]
           for k, v in rows.items()}
    assert got == {"Pat@Mail.ORG ": "pat@mail.org", "905-555-0100": None, "not an email": None,
                   "a@b": None, "x@y.z": "x@y.z", None: None, "two@@mail.org": None,
                   "spaces in@mail.org": None}


def test_an_email_must_be_normalised(db):
    c = db.conn
    w = build_world(c)
    for bad in ("Upper@x.org", " pad@x.org", "ab", "x" * 250 + "@y.org"):
        with pytest.raises(errors.CheckViolation):
            c.execute("update patients set email = %s where id = %s", (bad, w.patient_a))
    c.execute("update patients set email = 'ok@x.org' where id = %s", (w.patient_a,))


# --- who can see what: a patient's Auth user ---------------------------------------------------


def populate(c, w):
    """A row in every table a patient could plausibly be tempted by."""
    e = make_episode(c, w)
    check_in(c, w, e)
    clinician_msg(c, w, e)
    c.execute("insert into consents (clinic_id, patient_id, consent_type, consent_text_version) "
              "values (%s, %s, 'messaging', 'v1')", (w.clinic_a, w.patient_a))
    c.execute("insert into touchpoints (clinic_id, patient_id, kind, source) "
              "values (%s, %s, 'call', 'manual')", (w.clinic_a, w.patient_a))
    pu = user(c, "pat@x.org")
    a = account(c, w, u=pu)
    c.execute("insert into patient_sign_in_links (clinic_id, account_id, token_hash, issued_by, "
              "expires_at) values (%s, %s, repeat('a', 64), %s, now() + interval '1 day')",
              (w.clinic_a, a, w.user_a))
    c.execute("insert into email_outbox (clinic_id, patient_id, recipient, template, status, "
              "rendered_subject, rendered_text, rendered_html) values "
              "(%s, %s, 'pat@x.org', 'sign_in_code', 'demo', 's', 'code 123456', 'h')",
              (w.clinic_a, w.patient_a))
    c.execute("insert into audit_log (clinic_id, event_type, payload_hash, row_hash, signature, "
              "key_id) values (%s, 'x', 'p', 'r', 's', 'k')", (w.clinic_a,))
    return pu, e


def public_tables(c):
    return [r["tablename"] for r in c.execute(
        "select tablename from pg_tables where schemaname = 'public' order by 1").fetchall()]


def visible(c, table):
    """How many rows the current role can read; a table it has no privilege on shows nothing."""
    try:
        return c.execute(f"select count(*) as n from public.{table}").fetchone()["n"]
    except errors.InsufficientPrivilege:
        return 0


def test_every_table_has_row_level_security_so_a_new_one_cannot_be_forgotten(db):
    off = [r["relname"] for r in db.conn.execute(
        "select relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity").fetchall()]
    assert off == []


def test_a_patient_user_reads_nothing_directly_from_any_table(db):
    c = db.conn
    w = build_world(c)
    pu, _ = populate(c, w)
    tables = public_tables(c)
    assert "patient_accounts" in tables and "email_outbox" in tables

    # Control: the clinic's own staff do read these (so a zero below means something).
    as_user(c, "authenticated", w.user_a)
    staff = {t: visible(c, t) for t in tables}
    c.execute("reset role")
    for t in ("clinics", "clinic_members", "patients", "episodes", "check_ins", "messages",
              "consents", "touchpoints", "patient_accounts", "audit_log", "message_schedules"):
        assert staff[t] > 0, t
    assert staff["email_outbox"] == 0 and staff["patient_sign_in_links"] == 0  # not even staff

    as_user(c, "authenticated", pu)
    try:
        assert {t: visible(c, t) for t in tables if visible(c, t)} == {}
    finally:
        c.execute("reset role")


def test_nor_can_a_user_with_no_role_at_all_or_the_anon_key(db):
    c = db.conn
    w = build_world(c)
    populate(c, w)
    stranger = user(c, "stranger@x.org")  # signed up through the public anon key
    for role, sub in (("authenticated", stranger), ("anon", "")):
        as_user(c, role, sub)
        try:
            assert [t for t in public_tables(c) if visible(c, t)] == [], role
        finally:
            c.execute("reset role")


def test_schedules_are_for_clinic_staff_only(db):
    c = db.conn
    w = build_world(c)
    stranger = user(c, "stranger@x.org")
    for sub, expected in ((w.user_a, True), (w.user_b, True), (stranger, False)):
        as_user(c, "authenticated", sub)
        try:
            assert (visible(c, "message_schedules") > 0) is expected
        finally:
            c.execute("reset role")


def test_a_patient_user_cannot_write_anything_directly(db):
    c = db.conn
    w = build_world(c)
    pu, e = populate(c, w)
    tables = public_tables(c)
    before = {t: c.execute(f"select count(*) as n from public.{t}").fetchone()["n"] for t in tables}

    as_user(c, "authenticated", pu)
    try:
        attempts = [
            "insert into clinic_members (clinic_id, user_id, role) values ('{a}', '{u}', 'owner')",
            "insert into patient_accounts (clinic_id, patient_id, user_id, status, invited_by) "
            "values ('{a}', '{p}', '{u}', 'active', '{u}')",
            "insert into patients (clinic_id, full_name) values ('{a}', 'x')",
            "insert into messages (clinic_id, patient_id, sender, body) "
            "values ('{a}', '{p}', 'clinician', 'x')",
            "insert into consents (clinic_id, patient_id, consent_type, consent_text_version) "
            "values ('{a}', '{p}', 'x', 'v')",
            "insert into clinics (name) values ('x')",
            "insert into email_outbox (clinic_id, recipient, template, status) "
            "values ('{a}', 'x@y.org', 'invite', 'queued')",
            "insert into audit_log (clinic_id, event_type, payload_hash, row_hash, signature, "
            "key_id) values ('{a}', 'x', 'p', 'r2', 's', 'k')",
            "update clinic_members set role = 'owner'",
            "update patient_accounts set status = 'active'",
            "update patients set full_name = 'x'",
            "update clinics set name = 'x'",
            "delete from patient_accounts",
            "delete from clinic_members",
            "delete from messages",
            "truncate patient_accounts",
            "truncate audit_log",
        ]
        for sql in attempts:
            sql = sql.format(a=w.clinic_a, p=w.patient_a, u=pu)
            try:
                c.execute(sql)
            except (errors.InsufficientPrivilege, errors.RaiseException):
                continue
            # allowed by the database, so it must have touched no row (checked below)
    finally:
        c.execute("reset role")
    after = {t: c.execute(f"select count(*) as n from public.{t}").fetchone()["n"] for t in tables}
    assert after == before
    assert c.execute("select status from patient_accounts").fetchone()["status"] == "invited"
    assert c.execute("select count(*) as n from clinic_members where user_id = %s", (pu,)
                     ).fetchone()["n"] == 0


def test_no_function_that_changes_anything_is_callable_by_a_signed_in_user(db):
    rows = db.conn.execute(
        "select p.proname, p.prorettype::regtype::text as ret, "
        "has_function_privilege('authenticated', p.oid, 'execute') as auth_ok, "
        "has_function_privilege('anon', p.oid, 'execute') as anon_ok "
        "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.prorettype <> 'trigger'::regtype "
        # not an extension's own functions (pgcrypto's land in public on a plain Postgres; on
        # Supabase they live in the `extensions` schema)
        "and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')"
    ).fetchall()
    harmless = {"is_clinic_member", "is_any_clinic_member", "schedule_offsets_ok"}
    callable_ = sorted(r["proname"] for r in rows if (r["auth_ok"] or r["anon_ok"])
                       and r["proname"] not in harmless)
    assert callable_ == []
    assert {"issue_patient_code", "end_patient_sessions", "find_auth_user_by_email"} <= {
        r["proname"] for r in rows}


# --- account rules ---------------------------------------------------------------------------


def test_one_account_per_patient_and_one_live_account_per_user(db):
    c = db.conn
    w = build_world(c)
    u = user(c, "p@x.org")
    account(c, w, u=u)
    with pytest.raises(errors.UniqueViolation):
        account(c, w, u=user(c, "q@x.org"))  # the same patient again
    with pytest.raises(errors.UniqueViolation):
        account(c, w, patient=w.patient_a2, u=u)  # the same user for another patient
    c.execute("update patient_accounts set status = 'off', turned_off_at = now(), "
              "turned_off_by = %s", (w.user_a,))
    account(c, w, patient=w.patient_a2, u=u)  # free again once the first is off


def test_an_account_cannot_name_another_clinics_patient(db):
    c = db.conn
    w = build_world(c)
    with pytest.raises(errors.ForeignKeyViolation):
        account(c, w, patient=w.patient_b)


def test_statuses_come_with_their_facts(db):
    c = db.conn
    w = build_world(c)
    with pytest.raises(errors.CheckViolation):
        account(c, w, status="active")  # no activation, no identity check
    with pytest.raises(errors.CheckViolation):
        account(c, w, status="off")
    with pytest.raises(errors.CheckViolation):
        account(c, w, status="locked")
    with pytest.raises(errors.CheckViolation):
        account(c, w, status="weird")
    account(c, w, status="active", activated_at="2026-10-01T00:00:00Z",
            identity_verified_at="2026-10-01T00:00:00Z")


def test_staff_and_patients_are_never_the_same_user(db):
    c = db.conn
    w = build_world(c)
    with raises_text("patient_account_user_is_staff"):
        account(c, w, u=w.user_a)
    pu = user(c, "p@x.org")
    account(c, w, u=pu)
    with raises_text("staff_user_is_patient"):
        c.execute("insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'owner')",
                  (w.clinic_a, pu))
    with raises_text("staff_user_is_patient"):
        c.execute("update clinic_members set user_id = %s where user_id = %s", (pu, w.user_a))


def test_the_email_is_frozen_while_the_account_is_active(db):
    c = db.conn
    w = build_world(c)
    c.execute("update patients set email = 'a@x.org' where id = %s", (w.patient_a,))
    account(c, w, status="active", activated_at="2026-10-01T00:00:00Z",
            identity_verified_at="2026-10-01T00:00:00Z")
    with raises_text("patient_email_locked"):
        c.execute("update patients set email = 'b@x.org' where id = %s", (w.patient_a,))
    c.execute("update patients set full_name = 'Renamed' where id = %s", (w.patient_a,))  # fine
    c.execute("update patient_accounts set status = 'off', turned_off_at = now(), "
              "turned_off_by = %s", (w.user_a,))
    c.execute("update patients set email = 'b@x.org' where id = %s", (w.patient_a,))


def test_accounts_are_kept_not_deleted_with_their_patient_or_user(db):
    c = db.conn
    w = build_world(c)
    u = user(c, "p@x.org")
    account(c, w, u=u)
    with pytest.raises(errors.ForeignKeyViolation):
        c.execute("delete from patients where id = %s", (w.patient_a,))
    with pytest.raises(errors.ForeignKeyViolation):
        c.execute("delete from auth.users where id = %s", (u,))


def test_a_fallback_link_is_stored_as_a_hash_and_settles_once(db):
    c = db.conn
    w = build_world(c)
    a = account(c, w)

    def link(h="b" * 64, **kw):
        cols = {"clinic_id": w.clinic_a, "account_id": a, "token_hash": h, "issued_by": w.user_a,
                "expires_at": "2099-01-01T00:00:00Z", **kw}
        return str(c.execute(
            f"insert into patient_sign_in_links ({', '.join(cols)}) values "
            f"({', '.join(['%s'] * len(cols))}) returning id", list(cols.values())
        ).fetchone()["id"])

    with pytest.raises(errors.CheckViolation):
        link("not-a-hash")
    with pytest.raises(errors.CheckViolation):
        link("c" * 64, expires_at="2000-01-01T00:00:00Z")
    lid = link()
    with pytest.raises(errors.UniqueViolation):
        link()
    with raises_text("patient_sign_in_links_immutable"):
        c.execute("update patient_sign_in_links set expires_at = '2100-01-01' where id = %s", (lid,))
    with raises_text("patient_sign_in_links_retained"):
        c.execute("delete from patient_sign_in_links where id = %s", (lid,))
    c.execute("update patient_sign_in_links set used_at = now() where id = %s", (lid,))
    with raises_text("patient_sign_in_links_settled"):
        c.execute("update patient_sign_in_links set used_at = now() + interval '1 hour' "
                  "where id = %s", (lid,))
    with pytest.raises(errors.CheckViolation):  # used AND revoked is not a thing
        c.execute("update patient_sign_in_links set revoked_at = now() where id = %s", (lid,))


# --- the atomic steps -------------------------------------------------------------------------


def test_a_code_allows_five_tries_ten_minutes_and_starts_over_with_a_new_code(db):
    c = db.conn
    w = build_world(c)
    a = account(c, w)

    def claim():
        return call(c, "claim_patient_code_attempt(%s, 5, 600)", a)["outcome"]

    assert claim() == "expired"  # no code has been issued
    assert call(c, "issue_patient_code(%s)", a) == {"ok": True}
    assert [claim() for _ in range(7)] == ["ok"] * 5 + ["too_many"] * 2
    call(c, "issue_patient_code(%s)", a)  # a new code: the count restarts
    assert claim() == "ok"
    c.execute("update patient_accounts set code_sent_at = now() - interval '601 seconds'")
    assert claim() == "expired"
    call(c, "finish_patient_code(%s)", a)
    row = c.execute("select * from patient_accounts").fetchone()
    assert row["code_sent_at"] is None and row["code_failed_attempts"] == 0
    assert row["last_sign_in_at"] is not None
    assert claim() == "expired"


def test_codes_are_for_live_accounts_only(db):
    c = db.conn
    w = build_world(c)
    a = account(c, w, status="off", turned_off_at="2026-10-01T00:00:00Z", turned_off_by=w.user_a)
    assert call(c, "issue_patient_code(%s)", a) == {"ok": False}
    assert call(c, "claim_patient_code_attempt(%s, 5, 600)", a)["outcome"] == "no_account"
    assert call(c, "claim_patient_code_attempt(gen_random_uuid(), 5, 600)")["outcome"] == "no_account"


def test_three_wrong_dates_lock_and_a_right_one_resets_the_count(db):
    c = db.conn
    w = build_world(c)
    a = account(c, w)

    def check(matches):
        return call(c, "record_patient_identity(%s, %s, 3)", a, matches)

    assert check(False) == {"outcome": "wrong", "attempts_left": 2}
    assert check(True) == {"outcome": "verified"}
    row = c.execute("select * from patient_accounts").fetchone()
    assert row["identity_verified_at"] and row["identity_failed_attempts"] == 0
    assert [check(False)["outcome"] for _ in range(3)] == ["wrong", "wrong", "locked"]
    row = c.execute("select * from patient_accounts").fetchone()
    assert row["status"] == "locked" and row["locked_at"]
    assert check(True) == {"outcome": "not_allowed"}  # the right date no longer helps
    assert check(False) == {"outcome": "not_allowed"}


def test_a_fallback_link_is_consumed_exactly_once_and_only_while_valid(db):
    c = db.conn
    w = build_world(c)
    a = account(c, w)

    def link(h, **kw):
        cols = {"clinic_id": w.clinic_a, "account_id": a, "token_hash": h * 64,
                "issued_by": w.user_a, "expires_at": "2099-01-01T00:00:00Z", **kw}
        c.execute(f"insert into patient_sign_in_links ({', '.join(cols)}) values "
                  f"({', '.join(['%s'] * len(cols))})", list(cols.values()))

    link("a")
    link("b", revoked_at="2026-10-01T00:00:00Z")
    link("c", issued_at="2020-01-01T00:00:00Z", expires_at="2020-01-02T00:00:00Z")
    assert call(c, "consume_patient_link(%s)", "a" * 64)["account_id"] == a
    assert call(c, "consume_patient_link(%s)", "a" * 64) is None  # used
    assert call(c, "consume_patient_link(%s)", "b" * 64) is None  # replaced
    assert call(c, "consume_patient_link(%s)", "c" * 64) is None  # expired
    assert call(c, "consume_patient_link(%s)", "d" * 64) is None  # never issued


def test_auth_helpers_find_end_and_date_sessions(db):
    c = db.conn
    u = user(c, "Find.Me@x.org")
    other = user(c, "other@x.org")
    s1 = str(c.execute("insert into auth.sessions (user_id) values (%s) returning id", (u,)
                       ).fetchone()["id"])
    c.execute("insert into auth.sessions (user_id) values (%s)", (u,))
    c.execute("insert into auth.sessions (user_id) values (%s)", (other,))
    assert call(c, "find_auth_user_by_email(%s)", "find.me@X.org") == {"id": u}
    assert call(c, "find_auth_user_by_email(%s)", "nobody@x.org") is None
    assert call(c, "patient_session_started(%s)", s1)["created_at"]
    assert call(c, "patient_session_started(gen_random_uuid())") is None
    assert call(c, "end_patient_sessions(%s)", u) == {"ended": 2}
    assert c.execute("select count(*) as n from auth.sessions").fetchone()["n"] == 1  # the other's


@pytest.mark.parametrize("role", ["authenticated", "anon"])
def test_the_steps_cannot_be_called_by_a_signed_in_user(db, role):
    c = db.conn
    w = build_world(c)
    a = account(c, w)
    as_user(c, role, w.user_a)
    try:
        for sql in (f"issue_patient_code('{a}')", f"claim_patient_code_attempt('{a}', 5, 600)",
                    f"finish_patient_code('{a}')", f"record_patient_identity('{a}', true, 3)",
                    "consume_patient_link('x')", f"end_patient_sessions('{w.user_a}')",
                    "patient_session_started(gen_random_uuid())",
                    "find_auth_user_by_email('a@b.org')"):
            with pytest.raises(errors.InsufficientPrivilege):
                c.execute(f"select {sql}")
    finally:
        c.execute("reset role")
