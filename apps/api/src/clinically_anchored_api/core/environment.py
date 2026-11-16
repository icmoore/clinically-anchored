"""Which environment this deployment is, and whether the database agrees.

The database holds one row, `app_settings.environment` ('production' or 'staging'; migration 21,
set by hand in each Supabase project). The service has `APP_ENV`. They must be equal, or the
service doesn't start: a staging api pointed at the production database (or the reverse) is the
mistake this exists to stop. Checked once, at startup (main.py's lifespan)."""

from clinically_anchored_api.core.config import Settings


class EnvironmentMismatch(RuntimeError):
    """Startup must stop: the configured environment and the database don't agree."""


def read_database_environment(supabase) -> str | None:
    """The environment recorded in the database, or None when there is no row. Raises
    EnvironmentMismatch when the table can't be read (migration 21 not applied, or a wrong
    database)."""
    try:
        rows = supabase.table("app_settings").select("environment").execute().data
    except Exception as exc:
        raise EnvironmentMismatch(
            "Could not read app_settings from the database. Apply migration 21 "
            f"(supabase/migrations/00000000000021_app_settings_environment.sql) to this project. "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    return rows[0]["environment"] if rows else None


def check_environment(supabase, settings: Settings) -> str:
    """Return the verified environment, or raise EnvironmentMismatch with what to fix."""
    if settings.app_env not in ("production", "staging"):
        raise EnvironmentMismatch(
            "APP_ENV is not set. Set APP_ENV to 'production' or 'staging' to match the "
            "database this service uses (apps/api/.env locally, Railway variables when deployed)."
        )
    recorded = read_database_environment(supabase)
    if recorded is None:
        raise EnvironmentMismatch(
            "The database's app_settings table has no row, so it doesn't say which environment it "
            f"is. If this really is the {settings.app_env} project, insert it as the table owner: "
            f"insert into app_settings (environment) values ('{settings.app_env}');"
        )
    if recorded != settings.app_env:
        raise EnvironmentMismatch(
            f"APP_ENV is {settings.app_env!r} but the database says it is {recorded!r}. "
            "This service is pointed at the wrong Supabase project (check SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY), or APP_ENV is wrong. Refusing to start."
        )
    return recorded


def require_staging(supabase, settings: Settings) -> None:
    """For tooling that bulk-creates data (the seed): proceed only when APP_ENV says staging AND
    the database says staging. Anything else (production, no row, a mismatch, APP_ENV unset)
    raises EnvironmentMismatch. Stricter than the SQL scripts on purpose: a seed has no reason to
    run on a project whose environment isn't recorded."""
    if check_environment(supabase, settings) != "staging":
        raise EnvironmentMismatch(
            "Refusing to write synthetic data: this is the PRODUCTION environment."
        )
