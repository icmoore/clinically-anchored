"""POST /auth-hooks/send-email: Supabase Auth's email hook. Verified, then dropped."""

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api.api.auth_hooks import verify_signature
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app

SECRET_BYTES = b"0123456789abcdef0123456789abcdef"
SECRET = "v1,whsec_" + base64.b64encode(SECRET_BYTES).decode()
BODY = json.dumps({"user": {"email": "pat@mail.org"},
                   "email_data": {"email_action_type": "magiclink", "token": "123456"}}).encode()


def sign(body=BODY, msg_id="msg_1", ts=None, key=SECRET_BYTES):
    ts = str(int(ts if ts is not None else time.time()))
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return {"webhook-id": msg_id, "webhook-timestamp": ts,
            "webhook-signature": "v1," + base64.b64encode(mac).decode()}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "send_email_hook_secret", SECRET)
    return TestClient(app)


def test_a_signed_request_is_accepted_and_nothing_is_sent(client, caplog):
    r = client.post("/auth-hooks/send-email", content=BODY, headers=sign())
    assert r.status_code == 200 and r.json() == {}
    logged = " ".join(m for m in caplog.messages)
    assert "magiclink" in logged
    assert "pat@mail.org" not in logged and "123456" not in logged  # no address, no code


@pytest.mark.parametrize("headers", [
    {},
    {**sign(), "webhook-signature": "v1,AAAA"},
    sign(key=b"another secret entirely 0123456789"),
    sign(ts=time.time() - 3600),  # too old (replay)
    sign(ts=time.time() + 3600),
    {**sign(), "webhook-id": "other"},  # signed for a different id
    {**sign(), "webhook-timestamp": "soon"},
])
def test_anything_else_is_refused(client, headers):
    assert client.post("/auth-hooks/send-email", content=BODY, headers=headers).status_code == 401


def test_a_changed_body_is_refused(client):
    assert client.post("/auth-hooks/send-email", content=BODY + b" ", headers=sign()
                       ).status_code == 401


def test_without_a_configured_secret_it_answers_503(monkeypatch):
    monkeypatch.setattr(get_settings(), "send_email_hook_secret", "")
    r = TestClient(app).post("/auth-hooks/send-email", content=BODY, headers=sign())
    assert r.status_code == 503


def test_any_of_several_signatures_may_match_and_the_secret_prefix_is_optional():
    h = sign()
    good = h["webhook-signature"]
    multi = {**h, "webhook-signature": "v1,AAAA " + good + " v2,BBBB"}
    assert verify_signature(SECRET, multi, BODY)
    assert verify_signature(base64.b64encode(SECRET_BYTES).decode(), h, BODY)
    assert not verify_signature(SECRET, {**h, "webhook-signature": "v2," + good[3:]}, BODY)
    assert not verify_signature("v1,whsec_%%%", h, BODY)
