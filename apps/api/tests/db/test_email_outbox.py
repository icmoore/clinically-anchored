"""Migration 17 against a real Postgres: clinics.is_demo can be set only by SQL, and the email
outbox holds a rendered email only for a demo clinic, settles once, is never deleted and is closed
to every authenticated user. Then the api end to end: send, store, refuse, and the demo inbox."""

import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from clinically_anchored_api.core import mailer
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.conftest import build_world, migration_files
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install

pytestmark = pytest.mark.db

DEMO_CLINIC_ID = "8a757d96-b69e-46c3-9693-7b5e604ed809"


def raises_text(text: str):
    return pytest.raises(errors.RaiseException, match=text)


def queued(c, w, **kw):
    cols = {"clinic_id": w.clinic_a, "patient_id": w.patient_a, "recipient": "p@mail.org",
            "template": "invite", "status": "queued", **kw}
    return c.execute(
        f"insert into email_outbox ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
        "returning id",
        list(cols.values()),
    ).fetchone()["id"]


def as_role(c, role, sub=None):
    c.execute(f"set role {role}")
    if sub:
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (sub,))


# --- clinics.is_demo ---------------------------------------------------------------


def test_the_migration_marks_the_seeded_demo_clinic_and_nothing_else(db_pre17):
    c = db_pre17.conn
    c.execute("insert into clinics (id, name) values (%s, 'Demo clinic')", (DEMO_CLINIC_ID,))
    c.execute("insert into clinics (name) values ('Real clinic')")
    migration = [f for f in migration_files(17) if f.name.startswith("00000000000017")][0]
    c.execute(migration.read_text())
    rows = {r["name"]: r["is_demo"] for r in c.execute("select name, is_demo from clinics")}
    assert rows["Demo clinic"] is True
    assert rows["Real clinic"] is False
    assert all(v is False for k, v in rows.items() if k not in ("Demo clinic",))


def test_a_new_clinic_is_not_a_demo_clinic(db):
    w = build_world(db.conn)
    assert db.conn.execute("select is_demo from clinics where id = %s", (w.clinic_a,)
                           ).fetchone()["is_demo"] is False


def test_the_api_role_can_never_set_is_demo_but_the_table_owner_can(db):
    c = db.conn
    w = build_world(c)
    as_role(c, "service_role")
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("update clinics set is_demo = true where id = %s", (w.clinic_a,))
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("insert into clinics (name, is_demo) values ('x', true)")
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("insert into clinics (name, is_demo) values ('x', false)")
        # What the api and the seed script do keeps working.
        c.execute("update clinics set office_phone = '905-555-0100', name = 'Renamed' "
                  "where id = %s", (w.clinic_a,))
        c.execute("insert into clinics (name) values ('New one')")
        c.execute("insert into clinics (name, office_phone) values ('New two', '905-555-0101')")
    finally:
        c.execute("reset role")
    assert c.execute("select is_demo from clinics where name = 'New one'"
                     ).fetchone()["is_demo"] is False
    c.execute("update clinics set is_demo = true where id = %s", (w.clinic_a,))  # psql as owner
    assert c.execute("select is_demo from clinics where id = %s", (w.clinic_a,)
                     ).fetchone()["is_demo"] is True


@pytest.mark.parametrize("role", ["authenticated", "anon"])
def test_signed_in_users_cannot_write_clinics_at_all(db, role):
    c = db.conn
    w = build_world(c)
    as_role(c, role, w.user_a)
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("update clinics set name = 'x' where id = %s", (w.clinic_a,))
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("insert into clinics (name) values ('x')")
    finally:
        c.execute("reset role")


# --- the outbox's own rules --------------------------------------------------------


def test_each_status_needs_the_right_fields(db):
    c = db.conn
    w = build_world(c)
    queued(c, w)
    queued(c, w, status="sent", provider_message_id="m-1", sent_at="2026-10-01T00:00:00Z")
    queued(c, w, status="failed", error="MessageRejected")
    queued(c, w, status="refused", error="reserved_domain")
    queued(c, w, status="demo", rendered_subject="s", rendered_text="t", rendered_html="h",
           patient_id=None)
    bad = [
        dict(status="sent"),  # no sent_at
        dict(status="queued", sent_at="2026-10-01T00:00:00Z"),
        dict(status="queued", provider_message_id="m"),  # an id only once sent
        dict(status="failed"),  # a failure says why
        dict(status="refused"),
        dict(status="queued", error="x"),
        dict(status="sent", sent_at="2026-10-01T00:00:00Z", error="x"),
        dict(status="demo"),  # a demo row IS its content
        dict(status="demo", rendered_subject="s", rendered_text="t"),
        dict(status="sent", sent_at="2026-10-01T00:00:00Z", rendered_text="t",
             rendered_subject="s", rendered_html="h"),  # only a demo row carries a body
        dict(status="queued", rendered_text="a code: 123456"),
        dict(status="failed", error="e", rendered_html="<p>123456</p>"),
        dict(status="weird"),
        dict(template="newsletter"),
        dict(recipient="a"),
    ]
    for fields in bad:
        with pytest.raises(errors.CheckViolation):
            queued(c, w, **fields)


def test_a_queued_row_settles_once_and_nothing_else_changes_it(db):
    c = db.conn
    w = build_world(c)
    rid = queued(c, w)
    with raises_text("email_outbox_immutable"):
        c.execute("update email_outbox set recipient = 'x@y.org', status = 'sent', "
                  "sent_at = now() where id = %s", (rid,))
    with raises_text("email_outbox_immutable"):
        c.execute("update email_outbox set clinic_id = %s, status = 'failed', error = 'e' "
                  "where id = %s", (w.clinic_b, rid))
    with raises_text("email_outbox_settled"):
        c.execute("update email_outbox set status = 'refused', error = 'e' where id = %s", (rid,))
    c.execute("update email_outbox set status = 'sent', provider_message_id = 'm', "
              "sent_at = now() where id = %s", (rid,))
    with raises_text("email_outbox_settled"):
        c.execute("update email_outbox set status = 'failed', error = 'e' where id = %s", (rid,))
    other = queued(c, w, status="demo", rendered_subject="s", rendered_text="t",
                   rendered_html="h")
    with raises_text("email_outbox_settled"):
        c.execute("update email_outbox set rendered_text = 'other' where id = %s", (other,))


def test_rows_are_never_deleted(db):
    c = db.conn
    w = build_world(c)
    rid = queued(c, w)
    with raises_text("email_outbox_retained"):
        c.execute("delete from email_outbox where id = %s", (rid,))
    c.execute("truncate email_outbox")  # the reset script's way, which no api role can do
    assert c.execute("select count(*) as n from email_outbox").fetchone()["n"] == 0


def test_a_row_cannot_name_another_clinics_patient(db):
    c = db.conn
    w = build_world(c)
    with pytest.raises(errors.ForeignKeyViolation):
        queued(c, w, patient_id=w.patient_b)
    queued(c, w, patient_id=None)  # an email to no one in particular is fine


# --- who can touch it --------------------------------------------------------------


@pytest.mark.parametrize("role", ["authenticated", "anon"])
def test_no_signed_in_user_can_read_or_write_the_outbox_not_even_a_clinic_member(db, role):
    c = db.conn
    w = build_world(c)
    queued(c, w, status="demo", rendered_subject="s", rendered_text="code 123456",
           rendered_html="h")
    as_role(c, role, w.user_a)  # a member of the clinic whose mail it is
    try:
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("select count(*) from email_outbox")
        with pytest.raises(errors.InsufficientPrivilege):
            queued(c, w)
    finally:
        c.execute("reset role")


def test_the_api_role_reads_writes_and_settles(db):
    c = db.conn
    w = build_world(c)
    as_role(c, "service_role")
    try:
        rid = queued(c, w)
        c.execute("update email_outbox set status = 'failed', error = 'e' where id = %s", (rid,))
        assert c.execute("select count(*) as n from email_outbox").fetchone()["n"] == 1
        with raises_text("email_outbox_retained"):
            c.execute("delete from email_outbox where id = %s", (rid,))
    finally:
        c.execute("reset role")


# --- the api, end to end -----------------------------------------------------------


class FakeSes:
    def __init__(self):
        self.sent = []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "ses-1"}


def test_send_store_refuse_and_the_demo_inbox_against_the_real_schema(db, monkeypatch):
    c = db.conn
    w = build_world(c)
    c.execute("update clinic_members set role = 'owner'")
    c.execute("update clinics set name = 'Real Clinic', office_phone = '905-555-0100' "
              "where id = %s", (w.clinic_a,))
    c.execute("update clinics set name = 'Demo Clinic', is_demo = true where id = %s",
              (w.clinic_b,))
    pg = PgSupabase(c)
    install(monkeypatch, pg, audit=False)
    ses = FakeSes()
    monkeypatch.setattr(mailer.boto3, "client", lambda *a, **k: ses)
    mailer._client.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "ses_from_address", "no-reply@clinic.example.org")
    monkeypatch.setattr(settings, "web_base_url", "https://app.clinic.example.org")

    # A real clinic: sent, recorded without the body.
    sent = mailer.send_email(pg, clinic_id=w.clinic_a, patient_id=w.patient_a, to="Pat@Mail.org",
                             template="sign_in_code", code="482913")
    assert sent["status"] == "sent" and sent["provider_message_id"] == "ses-1"
    assert "482913" in ses.sent[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
    # A reserved domain is refused, a malformed address too.
    refused = mailer.send_email(pg, clinic_id=w.clinic_a, patient_id=w.patient_a,
                                to="pat@example.com", template="invite")
    assert refused["status"] == "refused" and len(ses.sent) == 1
    mailer.send_email(pg, clinic_id=w.clinic_a, patient_id=w.patient_a, to="nope",
                      template="invite")
    for junk in ("", " ", "x" * 400):  # the outbox's length check must not turn these into a 500
        row = mailer.send_email(pg, clinic_id=w.clinic_a, patient_id=w.patient_a, to=junk,
                                template="invite")
        assert row["status"] == "refused" and row["recipient"] == "(invalid address)"
    # A demo clinic: nothing sent, the email (and its code) kept for the inbox.
    mailer.send_email(pg, clinic_id=w.clinic_b, patient_id=w.patient_b, to="pat@example.com",
                      template="sign_in_code", code="777000")
    mailer.send_email(pg, clinic_id=w.clinic_b, patient_id=w.patient_b, to="pat@example.com",
                      template="invite")
    assert len(ses.sent) == 1

    rows = c.execute("select template, status, recipient, rendered_text from email_outbox "
                     "order by created_at, id").fetchall()
    assert [r["status"] for r in rows] == (
        ["sent"] + ["refused"] * 5 + ["demo", "demo"])
    assert all(r["rendered_text"] is None for r in rows if r["status"] != "demo")
    assert not any("482913" in str(r) for r in rows)  # a real code is stored nowhere

    client = TestClient(app)
    user_b = f"Bearer user:{w.user_b}"
    inbox = client.get(f"/clinics/{w.clinic_b}/demo-inbox", headers={"Authorization": user_b})
    assert inbox.status_code == 200
    body = inbox.json()
    assert [e["template"] for e in body] == ["invite", "sign_in_code"]  # newest first
    assert "777000" in body[1]["text"] and "Demo Clinic" in body[1]["subject"]
    assert "https://app.clinic.example.org/sign-in" in body[0]["text"]
    # The real clinic's owner has no demo inbox; the demo clinic's is closed to the other clinic.
    user_a = f"Bearer user:{w.user_a}"
    assert client.get(f"/clinics/{w.clinic_a}/demo-inbox",
                      headers={"Authorization": user_a}).status_code == 404
    assert client.get(f"/clinics/{w.clinic_b}/demo-inbox",
                      headers={"Authorization": user_a}).status_code == 403
    c.execute("update clinic_members set role = 'clinician' where user_id = %s", (w.user_b,))
    assert client.get(f"/clinics/{w.clinic_b}/demo-inbox",
                      headers={"Authorization": user_b}).status_code == 403

