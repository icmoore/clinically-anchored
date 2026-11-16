"""Go-live checklist: PASS or FAIL for each thing that must be true before real patients use an
environment. READ-ONLY: it only selects from the database and reads Supabase Auth's public
settings; it writes nothing and sends no email. Run it through `scripts/golive_check`.

It checks whichever environment the api's `.env` (or real environment variables) point at, and
says which that is first. Some checks only mean something in production (demo clinics and the
"(dev)" name are expected on staging); on staging they pass with a note, and when the database
doesn't say which environment it is they are applied as if it were production.

Options: --no-env-file reads only real environment variables (what `railway run` provides) and
ignores apps/api/.env, so a local file can't mask a missing production variable.

Exit status: 0 if every check passed, 1 if any failed, 2 if it could not even connect.
"""

import importlib.machinery
import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from clinically_anchored_api.core import mailer
from clinically_anchored_api.core.config import Settings, get_settings
from clinically_anchored_api.core.environment import EnvironmentMismatch, read_database_environment

REPO = Path(__file__).resolve().parents[4]
LEGAL_SCRIPT = REPO / "scripts" / "check_legal_docs"


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str


class ReadOnlyClient:
    """The only database access the checks get: `.table(name).select(...)` and nothing else, so a
    check cannot write even by mistake (no insert, update, delete, rpc or auth)."""

    class _Table:
        def __init__(self, table):
            self._table = table

        def select(self, *args, **kwargs):
            return self._table.select(*args, **kwargs)

    def __init__(self, client):
        self._client = client

    def table(self, name: str) -> "ReadOnlyClient._Table":
        return self._Table(self._client.table(name))


def _rows(db, table: str, columns: str) -> list[dict]:
    return db.table(table).select(columns).execute().data


def _names(rows: list[dict]) -> str:
    return ", ".join(repr(r["name"]) for r in rows)


def legal_placeholders() -> list[tuple[str, int, str]]:
    """What scripts/check_legal_docs reports (loaded from that script: it has no .py suffix)."""
    loader = importlib.machinery.SourceFileLoader("check_legal_docs", str(LEGAL_SCRIPT))
    spec = importlib.util.spec_from_loader("check_legal_docs", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module.placeholders()


def public_auth_settings(supabase_url: str, anon_key: str) -> dict:
    """Supabase Auth's public settings document (the same one the dashboard's JS client reads)."""
    import httpx

    response = httpx.get(
        f"{supabase_url.rstrip('/')}/auth/v1/settings", headers={"apikey": anon_key}, timeout=15
    )
    response.raise_for_status()
    return response.json()


def run_checks(
    db: ReadOnlyClient,
    settings: Settings,
    *,
    placeholders: Callable[[], list[tuple[str, int, str]]] = legal_placeholders,
    auth_settings: Callable[[str, str], dict] = public_auth_settings,
) -> list[Result]:
    results: list[Result] = []

    def add(name: str, ok: bool, detail: str) -> None:
        results.append(Result(name, ok, detail))

    # 1. The database says which environment it is.
    recorded: str | None = None
    try:
        recorded = read_database_environment(db)
    except EnvironmentMismatch as exc:
        add("Environment row", False, str(exc))
    else:
        if recorded is None:
            add(
                "Environment row",
                False,
                "app_settings has no row (insert 'staging' or 'production')",
            )
        else:
            add("Environment row", True, f"the database says {recorded}")

    # 2. ... and the service agrees.
    if recorded is None:
        add(
            "APP_ENV matches",
            False,
            f"APP_ENV is {settings.app_env or 'unset'}; nothing to compare",
        )
    elif settings.app_env == recorded:
        add("APP_ENV matches", True, f"APP_ENV={settings.app_env}")
    else:
        add(
            "APP_ENV matches",
            False,
            f"APP_ENV is {settings.app_env or 'unset'} but the database says {recorded}",
        )

    on_staging = recorded == "staging"

    # 3. No demo clinics (production only).
    try:
        clinics = _rows(db, "clinics", "id, name, is_demo")
    except Exception as exc:
        add("No demo clinics", False, f"could not read clinics: {exc}")
        add("Clinic name has no '(dev)'", False, f"could not read clinics: {exc}")
    else:
        demos = [c for c in clinics if c.get("is_demo")]
        if on_staging:
            add(
                "No demo clinics",
                True,
                f"not checked on staging ({len(demos)} demo clinic(s) expected)",
            )
        elif demos:
            add("No demo clinics", False, f"{len(demos)} demo clinic(s): {_names(demos)}")
        else:
            add("No demo clinics", True, "none")

        # 5. No "(dev)" in a clinic name (production only).
        dev = [c for c in clinics if "(dev)" in (c.get("name") or "").lower()]
        if on_staging:
            add(
                "Clinic name has no '(dev)'",
                True,
                "not checked on staging (the dev clinic is named so)",
            )
        elif dev:
            add("Clinic name has no '(dev)'", False, f"still named so: {_names(dev)}")
        else:
            add(
                "Clinic name has no '(dev)'",
                True,
                ", ".join(repr(c["name"]) for c in clinics) or "no clinics",
            )

    # 4. How many patients are in it.
    try:
        count = len(_rows(db, "patients", "id"))
        add("Patient count", True, f"{count} patient(s)")
    except Exception as exc:
        add("Patient count", False, f"could not read patients: {exc}")

    # 6. The legal documents have no [placeholders] left.
    try:
        hits = placeholders()
    except Exception as exc:
        add("Legal documents", False, f"could not check them: {exc}")
    else:
        if hits:
            shown = "; ".join(f"{name}:{line} {text}" for name, line, text in hits[:6])
            more = f" (+{len(hits) - 6} more)" if len(hits) > 6 else ""
            add("Legal documents", False, f"{len(hits)} placeholder(s) left: {shown}{more}")
        else:
            add("Legal documents", True, "no [placeholders] left (scripts/check_legal_docs)")

    # 7. The Bedrock model in use (and the AI call cap, since AI is synthetic-data-only for now).
    model = settings.bedrock_model_id.strip()
    cap = settings.ai_daily_call_cap_per_clinic
    cap_text = "AI calls blocked (cap 0)" if cap == 0 else f"AI cap {cap} calls/clinic/day"
    add("Bedrock model", bool(model), f"{model or 'BEDROCK_MODEL_ID is empty'}; {cap_text}")

    # 8. The email sender.
    sender = mailer.normalize_address(settings.ses_from_address)
    web_host = (urlparse(settings.web_base_url).hostname or "").lower()
    if not sender:
        if on_staging:
            add("SES_FROM_ADDRESS", True, "not set (staging sends no real email)")
        else:
            add("SES_FROM_ADDRESS", False, "not set: patient emails would be recorded as failed")
    elif not mailer.is_valid_address(sender):
        add("SES_FROM_ADDRESS", False, f"{sender!r} is not a valid address")
    elif on_staging:
        add("SES_FROM_ADDRESS", True, sender)
    else:
        domain = sender.rpartition("@")[2]
        if not web_host:
            add(
                "SES_FROM_ADDRESS",
                False,
                f"{sender}, but WEB_BASE_URL is not set to compare its domain with",
            )
        elif web_host != domain and not web_host.endswith("." + domain):
            add("SES_FROM_ADDRESS", False, f"{sender} is not on the web app's domain ({web_host})")
        else:
            add("SES_FROM_ADDRESS", True, sender)

    # 9. Nobody can sign themselves up.
    if not settings.supabase_url or not settings.supabase_anon_key:
        add("Sign-ups off", False, "needs SUPABASE_URL and SUPABASE_ANON_KEY to ask Supabase Auth")
    else:
        try:
            disabled = auth_settings(settings.supabase_url, settings.supabase_anon_key).get(
                "disable_signup"
            )
        except Exception as exc:
            add(
                "Sign-ups off",
                False,
                f"could not read Supabase Auth's settings: {type(exc).__name__}",
            )
        else:
            add(
                "Sign-ups off",
                disabled is True,
                "sign-ups are disabled"
                if disabled is True
                else "Authentication -> Sign In / Providers: turn off 'Allow new users to sign up'",
            )

    return results


def format_report(results: list[Result], header: str) -> str:
    width = max(len(r.name) for r in results)
    lines = [header, ""]
    for r in results:
        lines.append(f"{'PASS' if r.ok else 'FAIL'}  {r.name.ljust(width)}  {r.detail}")
    failed = sum(1 for r in results if not r.ok)
    lines += ["", f"{len(results) - failed} passed, {failed} failed"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--no-env-file" in args:
        # Use real environment variables only (e.g. `railway run`), so a local .env can never fill a
        # gap in what is being checked.
        Settings.model_config["env_file"] = None
        get_settings.cache_clear()
    elif args:
        print(f"Unknown argument(s): {' '.join(args)}", file=sys.stderr)
        return 2
    settings = get_settings()
    host = urlparse(settings.supabase_url).hostname
    if not host or not settings.supabase_service_role_key:
        print(
            "FAIL  Connection  SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set "
            "(run it through scripts/golive_check, which runs from apps/api where the .env is)"
        )
        return 2
    from clinically_anchored_api.core.db import get_supabase

    db = ReadOnlyClient(get_supabase())
    results = run_checks(db, settings)
    print(
        format_report(
            results,
            f"Go-live check against {host} (APP_ENV={settings.app_env or 'unset'}). Read-only.",
        )
    )
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
