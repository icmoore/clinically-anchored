"""Changing the clinic's office phone against a real Postgres: the value is stored in its one form,
the change is audited under the clinic's own chain (which still verifies), and the database's own
length check backs the api's validation."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient
from psycopg import errors

from clinically_anchored_api.core import audit
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.conftest import build_world
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install

pytestmark = pytest.mark.db


@pytest.fixture
def env(db, monkeypatch):
    w = build_world(db.conn)
    pg = PgSupabase(db.conn)
    install(monkeypatch, pg, audit=False)
    key = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    settings = get_settings()
    monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(key).decode())
    monkeypatch.setattr(settings, "audit_key_id", "test-1")
    return w, pg, TestClient(app)


def as_user(user):
    return {"Authorization": f"Bearer user:{user}"}


def test_owner_sets_changes_and_clears_it_and_the_chain_verifies(env, db):
    w, pg, client = env
    url = f"/clinics/{w.clinic_a}/office-phone"
    # build_world makes its members clinicians: make this one an owner.
    db.conn.execute("update clinic_members set role = 'owner' where user_id = %s", (w.user_a,))
    for sent in ("(905) 555-0100", "+1 416 555 0188", None):
        r = client.put(url, headers=as_user(w.user_a), json={"office_phone": sent})
        assert r.status_code == 200, r.text
    stored = db.conn.execute("select office_phone from clinics where id = %s", (w.clinic_a,))
    assert stored.fetchone()["office_phone"] is None  # cleared last

    rows = [r for r in audit.fetch_chain(pg, w.clinic_a) if r["event_type"] == "clinic.office_phone_changed"]
    assert [r["metadata"]["action"] for r in rows] == ["set", "changed", "cleared"]
    assert all(r["metadata"]["ref_id"] == w.clinic_a for r in rows)
    assert audit.verify_chain(audit.fetch_chain(pg, w.clinic_a), audit.trusted_public_keys())["ok"]


def test_a_clinician_cannot_and_nothing_is_written(env, db):
    w, _pg, client = env  # build_world's members are clinicians
    r = client.put(
        f"/clinics/{w.clinic_a}/office-phone", headers=as_user(w.user_a),
        json={"office_phone": "416-555-0188"},
    )
    assert r.status_code == 403
    assert db.conn.execute("select office_phone from clinics where id = %s",
                           (w.clinic_a,)).fetchone()["office_phone"] is None
    assert db.conn.execute("select count(*) as n from audit_log").fetchone()["n"] == 0


def test_the_database_rejects_an_absurd_value_even_if_the_api_were_bypassed(db):
    w = build_world(db.conn)
    with pytest.raises(errors.CheckViolation):
        db.conn.execute("update clinics set office_phone = 'x' where id = %s", (w.clinic_a,))
