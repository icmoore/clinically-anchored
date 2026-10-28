"""core/gotrue.py: the few calls the api makes to Supabase Auth, against a recording stand-in."""

import base64
import json

import pytest

from clinically_anchored_api.core import gotrue
from clinically_anchored_api.core.config import get_settings


class Admin:
    def __init__(self):
        self.calls = []

    def create_user(self, attrs):
        self.calls.append(("create_user", attrs))
        return type("R", (), {"user": type("U", (), {"id": "new-id"})()})()

    def update_user_by_id(self, uid, attrs):
        self.calls.append(("update_user_by_id", uid, attrs))
        return type("R", (), {"user": type("U", (), {"id": uid})()})()

    def generate_link(self, params):
        self.calls.append(("generate_link", params))
        props = type("P", (), {"email_otp": "654321"})()
        return type("L", (), {"properties": props})()

    def get_user_by_id(self, uid):
        return type("R", (), {"user": type("U", (), {"email": f"{uid}@x.org"})()})()


class Client:
    def __init__(self, user=None, raises=None):
        self.admin = Admin()
        self._user, self._raises = user, raises

    @property
    def auth(self):
        return self

    def get_user(self, jwt):
        if self._raises:
            raise self._raises
        return type("R", (), {"user": self._user})()


@pytest.fixture
def client(monkeypatch):
    c = Client()
    monkeypatch.setattr(gotrue, "get_supabase", lambda: c)
    return c


def jwt_with(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def test_a_new_patient_user_is_created_confirmed_and_marked(client):
    assert gotrue.ensure_patient_user("a@b.org") == "new-id"
    (name, attrs), = client.admin.calls
    assert name == "create_user"
    assert attrs == {"email": "a@b.org", "email_confirm": True,
                     "app_metadata": {"account_type": "patient"}}  # no ban_duration on a new one


def test_an_existing_user_is_updated_marked_and_unbanned(client):
    assert gotrue.ensure_patient_user("a@b.org", user_id="u-1") == "u-1"
    (name, uid, attrs), = client.admin.calls
    assert (name, uid) == ("update_user_by_id", "u-1")
    assert attrs["ban_duration"] == "none" and attrs["email"] == "a@b.org"
    assert attrs["app_metadata"] == {"account_type": "patient"} and attrs["email_confirm"] is True


def test_ban_is_effectively_forever(client):
    gotrue.ban("u-1")
    assert client.admin.calls == [("update_user_by_id", "u-1", {"ban_duration": "876000h"})]


def test_a_code_is_made_by_generate_link_which_sends_nothing(client):
    assert gotrue.generate_code("a@b.org") == "654321"
    assert client.admin.calls == [("generate_link", {"type": "magiclink", "email": "a@b.org"})]


def test_the_token_says_who_and_which_session(client, monkeypatch):
    user = type("U", (), {"id": "u-1", "email": "a@b.org",
                          "app_metadata": {"account_type": "patient"}})()
    monkeypatch.setattr(gotrue, "get_supabase", lambda: Client(user))
    got = gotrue.user_for_token(jwt_with({"session_id": "s-1"}))
    assert (got.id, got.email, got.account_type, got.session_id) == (
        "u-1", "a@b.org", "patient", "s-1")
    staff = type("U", (), {"id": "u-2", "email": None, "app_metadata": {}})()
    monkeypatch.setattr(gotrue, "get_supabase", lambda: Client(staff))
    assert gotrue.user_for_token("not-a-jwt").account_type is None


def test_a_token_supabase_rejects_is_an_invalid_session(monkeypatch):
    monkeypatch.setattr(gotrue, "get_supabase", lambda: Client(raises=ValueError("bad")))
    with pytest.raises(gotrue.InvalidSession):
        gotrue.user_for_token("x")
    monkeypatch.setattr(gotrue, "get_supabase", lambda: Client(user=None))
    with pytest.raises(gotrue.InvalidSession):
        gotrue.user_for_token("x")


def test_a_code_needs_the_anon_key_and_a_refusal_is_one_exception(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "supabase_url", "http://auth.invalid")
    monkeypatch.setattr(settings, "supabase_anon_key", "")
    with pytest.raises(gotrue.NotConfigured):
        gotrue.verify_code("a@b.org", "123456")
    monkeypatch.setattr(settings, "supabase_anon_key", "anon")

    class Refusing:
        auth = type("A", (), {"verify_otp": staticmethod(
            lambda p: (_ for _ in ()).throw(RuntimeError("otp_expired")))})()

    monkeypatch.setattr(gotrue, "_anon_client", lambda: Refusing())
    with pytest.raises(gotrue.CodeRejected):
        gotrue.verify_code("a@b.org", "123456")

    class NoSession:
        auth = type("A", (), {"verify_otp": staticmethod(
            lambda p: type("R", (), {"session": None, "user": None})())})()

    monkeypatch.setattr(gotrue, "_anon_client", lambda: NoSession())
    with pytest.raises(gotrue.CodeRejected):
        gotrue.verify_code("a@b.org", "123456")


def test_the_placeholder_email_can_never_receive_mail():
    from clinically_anchored_api.core.mailer import is_reserved_domain

    a, b = gotrue.placeholder_email(), gotrue.placeholder_email()
    assert a != b and is_reserved_domain(a)


def test_the_settings_come_from_the_environment(monkeypatch):
    from clinically_anchored_api.core.config import Settings

    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SEND_EMAIL_HOOK_SECRET", "v1,whsec_x")
    monkeypatch.setenv("PATIENT_SESSION_MAX_AGE_DAYS", "14")
    s = Settings(_env_file=None)
    assert (s.supabase_anon_key, s.send_email_hook_secret, s.patient_session_max_age_days) == (
        "anon", "v1,whsec_x", 14)
    d = Settings(_env_file=None)
    monkeypatch.delenv("PATIENT_SESSION_MAX_AGE_DAYS")
    assert Settings(_env_file=None).patient_session_max_age_days == 30
    assert (d.patient_code_ttl_seconds, d.patient_code_max_attempts,
            d.patient_identity_max_attempts, d.patient_fallback_link_ttl_hours) == (600, 5, 3, 24)
