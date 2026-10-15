"""Migration 13 against a real Postgres: saved patient briefs are append-only, same-clinic and
same-episode, and the api saves and reads them back end to end (real audit writes, `reconcile`
clean, the chain verifies)."""

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient
from psycopg import errors

from clinically_anchored_api.core import ai, audit, reconcile
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.conftest import build_world, check_in, clinician_msg, make_episode, patient_msg
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install

pytestmark = pytest.mark.db

BULLETS = json.dumps(
    [{"label": "Trends", "text": "x", "sources": [{"kind": "message", "id": "m1"}]}]
)
COVERS = json.dumps({"messages": 1, "check_ins": 0, "touchpoints": 0})


def save(conn, clinic, patient, episode, user):
    return conn.execute(
        "insert into patient_briefs (id, clinic_id, patient_id, episode_id, bullets, covers, "
        "truncated, model_id, prompt_version, generated_by, generated_at) values "
        "(gen_random_uuid(), %s, %s, %s, %s, %s, false, 'm', 'patient_brief_v1', %s, now()) "
        "returning id",
        (clinic, patient, episode, BULLETS, COVERS, user),
    ).fetchone()["id"]


def test_a_saved_brief_is_never_changed_or_deleted(db):
    w = build_world(db.conn)
    e = make_episode(db.conn, w)
    bid = save(db.conn, w.clinic_a, w.patient_a, e, w.user_a)
    with pytest.raises(Exception, match="patient_briefs_retained"):
        db.conn.execute("update patient_briefs set bullets = '[]' where id = %s", (bid,))
    with pytest.raises(Exception, match="patient_briefs_retained"):
        db.conn.execute("delete from patient_briefs where id = %s", (bid,))


def test_a_brief_cannot_name_another_clinics_patient_or_another_patients_episode(db):
    w = build_world(db.conn)
    e = make_episode(db.conn, w)
    with pytest.raises(errors.ForeignKeyViolation):
        save(db.conn, w.clinic_a, w.patient_b, e, w.user_a)
    with pytest.raises(errors.ForeignKeyViolation):
        save(db.conn, w.clinic_a, w.patient_a2, e, w.user_a)  # the episode is patient A's


def test_members_read_their_clinics_briefs_but_cannot_write(db):
    c = db.conn
    w = build_world(c)
    e = make_episode(c, w)
    save(c, w.clinic_a, w.patient_a, e, w.user_a)
    c.execute("set role authenticated")
    try:
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_a,))
        assert c.execute("select count(*) as n from patient_briefs").fetchone()["n"] == 1
        with pytest.raises(errors.InsufficientPrivilege):
            save(c, w.clinic_a, w.patient_a, e, w.user_a)
        c.execute("select set_config('request.jwt.claim.sub', %s, false)", (w.user_b,))
        assert c.execute("select count(*) as n from patient_briefs").fetchone()["n"] == 0
    finally:
        c.execute("reset role")


def test_the_api_saves_each_brief_and_reads_the_newest_back(db, monkeypatch):
    c = db.conn
    w = build_world(c)
    episode = make_episode(c, w)
    check_in(c, w, episode)
    first = clinician_msg(c, w, episode, body="How is the pain?")
    patient_msg(c, w, episode, origin="reply", body="Better, about a 5.")
    c.execute(
        "insert into touchpoints (clinic_id, patient_id, kind, source, note) "
        "values (%s, %s, 'call', 'manual', 'Booked the results visit')",
        (w.clinic_a, w.patient_a),
    )
    touch = c.execute("select id::text from touchpoints").fetchone()["id"]
    cin = c.execute("select id::text from check_ins").fetchone()["id"]

    pg = PgSupabase(c)
    install(monkeypatch, pg, audit=False)
    settings = get_settings()
    key = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(key).decode())
    monkeypatch.setattr(settings, "audit_key_id", "test-1")

    text = (
        "- The picture now: Pain reported at 5 after the clinic asked. [C1, M2]\n"
        "- Trends: Pain 5 in a later message; bleeding not reported. [M2]\n"
        "- Loose ends: Results visit booked by phone. [T1]"
    )
    seen = {}

    def generate(**kw):
        seen.update(kw)
        return ai.AIResult(
            text=text, model_id="m", prompt_version=kw["prompt_version"],
            usage=ai.TokenUsage(input_tokens=5, output_tokens=5), stop_reason="end_turn",
        )

    monkeypatch.setattr("clinically_anchored_api.api.briefs.ai.generate", generate)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer user:{w.user_a}"}
    url = f"/clinics/{w.clinic_a}/patients/{w.patient_a}/patient-brief"
    assert client.get(f"{url}/latest", headers=headers).json() is None

    one = client.post(url, headers=headers)
    assert one.status_code == 200, one.text
    one = one.json()
    two = client.post(url, headers=headers).json()
    assert one["bullets"][0]["sources"] == [
        {"kind": "check_in", "id": cin}, {"kind": "message", "id": seen_message_two(c, episode)},
    ]
    assert one["bullets"][2]["sources"] == [{"kind": "touchpoint", "id": touch}]
    assert first["id"]  # the clinician message is M1 in the prompt
    assert "[M1] clinician: How is the pain?" in seen["prompt"]

    rows = c.execute(
        "select id::text, generated_by::text from patient_briefs order by generated_at"
    ).fetchall()
    assert [r["id"] for r in rows] == [one["brief_id"], two["brief_id"]]  # both kept
    assert {r["generated_by"] for r in rows} == {w.user_a}

    latest = client.get(f"{url}/latest", headers=headers).json()
    assert latest == two  # exactly what was shown, read back

    # a message after the brief shows up as new
    clinician_msg(c, w, episode, body="Anything else?")
    assert client.get(f"{url}/latest", headers=headers).json()["new_since"]["messages"] == 1

    # Row and audit event tie together by id; reconcile and the chain are clean.
    report = reconcile.reconcile(pg, clinic_id=w.clinic_a)
    assert [g for g in report.unaudited if g.table == "patient_briefs"] == []
    assert report.checked["patient_brief.generated"] == 2
    chain = audit.verify_chain(audit.fetch_chain(pg, w.clinic_a), audit.trusted_public_keys())
    assert chain["ok"]


def seen_message_two(conn, episode):
    rows = conn.execute(
        "select id::text from messages where episode_id = %s order by created_at", (episode,)
    ).fetchall()
    return rows[1]["id"]
