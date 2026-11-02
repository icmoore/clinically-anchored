"""Outside development, missing secrets must stop the app at startup rather
than silently falling back to random per-process values."""

import pytest
from pydantic import ValidationError

from clinically_anchored_api.core.config import Settings

FULL = {
    "audit_signing_key": "k",
    "supabase_url": "https://x.supabase.co",
    "supabase_service_role_key": "r",
}


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_development_needs_no_secrets():
    s = _settings(environment="development")
    assert s.audit_signing_key == "" and s.supabase_url == ""


def test_the_retired_link_secret_is_neither_required_nor_read(monkeypatch):
    monkeypatch.setenv("CHECKIN_LINK_SECRET", "left-over-in-railway")
    s = _settings(environment="production", **FULL)  # fine without it, and fine with it set
    assert not hasattr(s, "checkin_link_secret")


def test_production_with_everything_set_is_fine():
    assert _settings(environment="production", **FULL).supabase_url == "https://x.supabase.co"


@pytest.mark.parametrize("missing", list(FULL))
def test_production_refuses_to_start_without_each_secret(missing):
    with pytest.raises(ValidationError, match=missing.upper()):
        _settings(environment="production", **{**FULL, missing: ""})


def test_allowed_origins_can_hold_the_new_and_the_old_web_address():
    s = _settings(allowed_origins="https://new.example.org, https://old.example.net")
    assert s.allowed_origins_list == ["https://new.example.org", "https://old.example.net"]


def test_allowed_origins_forgive_trailing_slashes_blanks_and_repeats():
    s = _settings(allowed_origins=" https://new.example.org/ ,, https://new.example.org,")
    assert s.allowed_origins_list == ["https://new.example.org"]
