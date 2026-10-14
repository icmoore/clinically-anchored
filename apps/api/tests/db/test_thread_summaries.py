"""Migration 10 against a real Postgres: saved summaries are append-only, same-clinic, and the api
saves and reads them back end to end (real audit writes, `reconcile` clean)."""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient
from psycopg import errors

from clinically_anchored_api.core import ai, audit, clock, reconcile
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.conftest import build_world, clinician_msg, make_episode
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install

pytestmark = pytest.mark.db

LINES = json.dumps([{"text": "Reports a red incision.", "citations": ["m1"]}])


def save(conn, clinic, patient, user, **extra):
    return conn.execute(
        "insert into thread_summaries (id, clinic_id, patient_id, lines, covers_messages, truncated, "
        "model_id, prompt_version, generated_by, generated_at) values "
        "(gen_random_uuid(), %s, %s, %s, 1, false, 'm', 'summary_v4', %s, now()) returning id",
        (clinic, patient, LINES, user),
    ).fetchone()["id"]


def test_a_saved_summary_is_never_changed_or_deleted(db):
    w = build_world(db.conn)
    sid = save(db.conn, w.clinic_a, w.patient_a, w.user_a)
    with pytest.raises(Exception, match="thread_summaries_retained"):
        db.conn.execute("update thread_summaries set lines = '[]' where id = %s", (sid,))
    with pytest.raises(Exception, match="thread_summaries_retained"):
        db.conn.execute("delete from thread_summaries where id = %s", (sid,))


def test_a_summary_cannot_name_another_clinics_patient(db):
    w = build_world(db.conn)
    with pytest.raises(errors.ForeignKeyViolation):
        save(db.conn, w.clinic_a, w.patient_b, w.user_a)


def test_members_read_their_clinics_summaries_but_cannot_write(db):
    c = db.conn
    w = build_world(c)
    save(c, w.clinic_a, w.patient_a, w.user_a)
    c.execute("set role authenticated")
    try:
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_a,))
        assert c.execute("select count(*) as n from thread_summaries").fetchone()["n"] == 1
        with pytest.raises(errors.InsufficientPrivilege):
            save(c, w.clinic_a, w.patient_a, w.user_a)
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_b,))
        assert c.execute("select count(*) as n from thread_summaries").fetchone()["n"] == 0
    finally:
        c.execute("reset role")


def test_the_api_saves_each_summary_and_reads_the_newest_back(db, monkeypatch):
    c = db.conn
    w = build_world(c)
    episode = make_episode(c, w)
    first = clinician_msg(c, w, episode, body="How is the pain?")
    pg = PgSupabase(c)
    install(monkeypatch, pg, audit=False)
    settings = get_settings()
    key = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(key).decode())
    monkeypatch.setattr(settings, "audit_key_id", "test-1")

    answers = iter(["- The clinic asked about pain. [M1]", "- Pain was asked about. [M1]"])
    monkeypatch.setattr(
        "clinically_anchored_api.api.summaries.ai.generate",
        lambda **kw: ai.AIResult(
            text=next(answers), model_id="m", prompt_version=kw["prompt_version"],
            usage=ai.TokenUsage(input_tokens=5, output_tokens=5), stop_reason="end_turn",
        ),
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer user:{w.user_a}"}
    url = f"/clinics/{w.clinic_a}/patients/{w.patient_a}/summaries"
    assert client.get(f"{url}/latest", headers=headers).json() is None

    one = client.post(url, headers=headers).json()
    two = client.post(url, headers=headers).json()
    assert one["lines"][0]["citations"] == [str(first["id"])]
    rows = c.execute("select id::text, generated_by::text from thread_summaries order by generated_at").fetchall()
    assert [r["id"] for r in rows] == [one["summary_id"], two["summary_id"]]  # both kept
    assert {r["generated_by"] for r in rows} == {w.user_a}

    latest = client.get(f"{url}/latest", headers=headers).json()
    assert latest == two  # exactly what was shown, read back

    # Row and audit event tie together by id; reconcile and the chain are clean.
    report = reconcile.reconcile(pg, clinic_id=w.clinic_a)
    unaudited = [g for g in report.unaudited if g.table == "thread_summaries"]
    assert unaudited == [] and report.checked["summary.generated"] == 2
    chain = audit.verify_chain(audit.fetch_chain(pg, w.clinic_a), audit.trusted_public_keys())
    assert chain["ok"]
    assert clock.now  # (the clock is not pinned here: nothing in this flow depends on it)
