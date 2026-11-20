"""CORS per environment: production is an exact list; staging may also accept this project's Vercel
preview URLs through ALLOWED_ORIGIN_REGEX, which production refuses to start with."""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import ValidationError

from clinically_anchored_api import main
from clinically_anchored_api.core.config import Settings

PROD_ORIGIN = "https://clinicallyanchored.ai"
STAGING_ORIGIN = "https://staging.clinicallyanchored.ai"
PREVIEW = "https://clinically-anchored-git-feature-x-acme.vercel.app"
PREVIEW_REGEX = r"https://clinically-anchored-[a-z0-9-]+-acme\.vercel\.app"
SECRETS = {
    "audit_signing_key": "k",
    "supabase_url": "https://x.supabase.co",
    "supabase_service_role_key": "r",
}


def _production(**kw) -> Settings:
    return Settings(
        _env_file=None, app_env="production", environment="production", **SECRETS,
        allowed_origins=PROD_ORIGIN, **kw,
    )


def _staging(**kw) -> Settings:
    return Settings(
        _env_file=None, app_env="staging", environment="production", **SECRETS,
        allowed_origins=STAGING_ORIGIN, **kw,
    )


def _allowed(settings: Settings, origin: str) -> bool:
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **settings.cors_options)

    @app.get("/x")
    def x():
        return {}

    response = TestClient(app).get("/x", headers={"Origin": origin})
    return response.headers.get("access-control-allow-origin") == origin


def test_the_real_app_is_wired_to_the_settings_options():
    cors = next(m for m in main.app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs == main.settings.cors_options


def test_production_accepts_only_the_exact_list():
    s = _production()
    assert s.cors_options["allow_origin_regex"] is None
    assert _allowed(s, PROD_ORIGIN)
    assert not _allowed(s, PREVIEW)
    assert not _allowed(s, STAGING_ORIGIN)


def test_production_refuses_to_start_with_the_regex_set():
    with pytest.raises(ValidationError, match="ALLOWED_ORIGIN_REGEX is only allowed"):
        _production(allowed_origin_regex=PREVIEW_REGEX)


def test_an_unset_app_env_refuses_the_regex_too():
    with pytest.raises(ValidationError, match="APP_ENV is unset"):
        Settings(_env_file=None, allowed_origin_regex=PREVIEW_REGEX)


def test_staging_accepts_its_exact_origin_and_matching_previews():
    s = _staging(allowed_origin_regex=PREVIEW_REGEX)
    assert _allowed(s, STAGING_ORIGIN)
    assert _allowed(s, PREVIEW)


@pytest.mark.parametrize(
    "origin",
    [
        "https://clinically-anchored-x-other-team.vercel.app",  # another team's project
        "https://clinically-anchored-x-acme.vercel.app.evil.example",  # suffix trick
        "http://clinically-anchored-x-acme.vercel.app",  # wrong scheme
        PROD_ORIGIN,  # not on the staging list
    ],
)
def test_staging_rejects_origins_the_regex_does_not_fully_match(origin):
    assert not _allowed(_staging(allowed_origin_regex=PREVIEW_REGEX), origin)


def test_staging_without_the_regex_is_an_exact_list():
    s = _staging()
    assert _allowed(s, STAGING_ORIGIN)
    assert not _allowed(s, PREVIEW)


def test_staging_rejects_an_invalid_regex():
    with pytest.raises(ValidationError, match="not a valid regular expression"):
        _staging(allowed_origin_regex="https://(unclosed")
