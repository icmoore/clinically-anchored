"""The startup guard: APP_ENV must equal the database's app_settings row."""

import pytest
from fastapi.testclient import TestClient

from clinically_anchored_api import main
from clinically_anchored_api.core.config import Settings
from clinically_anchored_api.core.environment import (
    EnvironmentMismatch,
    check_environment,
    read_database_environment,
    require_staging,
)
from tests.fakes import FakeSupabase


def _settings(app_env: str) -> Settings:
    if app_env == "production":  # production can't run as ENVIRONMENT=development
        return Settings(
            _env_file=None, app_env=app_env, environment="production", audit_signing_key="k",
            supabase_url="https://x.supabase.co", supabase_service_role_key="r",
        )
    return Settings(_env_file=None, app_env=app_env)


def _db(environment: str | None) -> FakeSupabase:
    db = FakeSupabase()
    db.tables["app_settings"] = [] if environment is None else [{"environment": environment}]
    return db


@pytest.mark.parametrize("env", ["staging", "production"])
def test_a_matching_environment_starts(env):
    assert check_environment(_db(env), _settings(env)) == env


def test_a_staging_api_on_the_production_database_refuses_to_start():
    with pytest.raises(EnvironmentMismatch, match="'staging' but the database says .*'production'"):
        check_environment(_db("production"), _settings("staging"))


def test_a_production_api_on_the_staging_database_refuses_to_start():
    with pytest.raises(EnvironmentMismatch, match="'production' but the database says .*'staging'"):
        check_environment(_db("staging"), _settings("production"))


def test_a_missing_row_refuses_to_start_and_says_what_to_insert():
    with pytest.raises(EnvironmentMismatch, match="insert into app_settings"):
        check_environment(_db(None), _settings("staging"))


def test_an_unset_app_env_refuses_to_start_without_asking_the_database():
    class Explodes:
        def table(self, _name):
            raise AssertionError("the database must not be consulted")

    with pytest.raises(EnvironmentMismatch, match="APP_ENV is not set"):
        check_environment(Explodes(), _settings(""))


def test_an_unreadable_table_points_at_migration_21():
    class NoTable:
        def table(self, _name):
            raise RuntimeError("relation app_settings does not exist")

    with pytest.raises(EnvironmentMismatch, match="migration 21"):
        read_database_environment(NoTable())


def test_the_app_refuses_to_start_on_a_mismatch(monkeypatch):
    monkeypatch.setattr(main, "get_supabase", lambda: _db("production"))
    monkeypatch.setattr(main.settings, "app_env", "staging")
    with pytest.raises(EnvironmentMismatch):
        with TestClient(main.app):
            pass


def test_the_app_starts_when_they_match(monkeypatch):
    monkeypatch.setattr(main, "get_supabase", lambda: _db("staging"))
    monkeypatch.setattr(main.settings, "app_env", "staging")
    with TestClient(main.app) as c:
        assert c.get("/health").json()["status"] == "ok"


# --- the seed -------------------------------------------------------------------------------

def test_require_staging_allows_staging_only():
    require_staging(_db("staging"), _settings("staging"))
    with pytest.raises(EnvironmentMismatch, match="PRODUCTION"):
        require_staging(_db("production"), _settings("production"))
    with pytest.raises(EnvironmentMismatch):
        require_staging(_db(None), _settings("staging"))  # no row: not recorded, so not seeded
    with pytest.raises(EnvironmentMismatch):
        require_staging(_db("production"), _settings("staging"))  # mismatch


@pytest.mark.parametrize(
    ("app_env", "row"), [("production", "production"), ("staging", "production"), ("", None)]
)
def test_the_seed_refuses_before_writing_anything(monkeypatch, app_env, row):
    from clinically_anchored_api import seed_trial_patients as seed
    from clinically_anchored_api.core import config, db

    fake = _db(row)
    before = {k: list(v) for k, v in fake.tables.items()}
    monkeypatch.setattr(db, "get_supabase", lambda: fake)
    monkeypatch.setattr(config, "get_settings", lambda: _settings(app_env))
    with pytest.raises(SystemExit, match="Not seeding"):
        seed.main(["--email", "doctor@example.com"])
    assert {k: v for k, v in fake.tables.items() if v} == {k: v for k, v in before.items() if v}


def test_the_seed_dry_run_needs_no_database(monkeypatch):
    from clinically_anchored_api import seed_trial_patients as seed
    from clinically_anchored_api.core import db

    monkeypatch.setattr(db, "get_supabase", lambda: (_ for _ in ()).throw(AssertionError("db")))
    assert seed.main(["--dry-run"]) == 0
