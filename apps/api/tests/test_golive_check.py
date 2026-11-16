"""The go-live checklist: each check's PASS and FAIL, per environment, and that it is read-only."""

import pytest

from clinically_anchored_api import golive_check as gc
from clinically_anchored_api.core.config import Settings
from tests.fakes import FakeSupabase

CLEAN = lambda: []  # noqa: E731  no legal placeholders
SIGNUPS_OFF = lambda url, key: {"disable_signup": True}  # noqa: E731


def settings(**kw) -> Settings:
    base = dict(
        app_env="production",
        environment="production",
        audit_signing_key="k",
        supabase_url="https://x.supabase.co",
        supabase_service_role_key="r",
        supabase_anon_key="anon",
        ses_from_address="no-reply@clinicallyanchored.ai",
        web_base_url="https://clinicallyanchored.ai",
    )
    return Settings(_env_file=None, **{**base, **kw})


def db(env="production", *, clinics=None, patients=2) -> gc.ReadOnlyClient:
    f = FakeSupabase()
    f.tables["app_settings"] = [] if env is None else [{"environment": env}]
    f.tables["clinics"] = (
        clinics
        if clinics is not None
        else [{"id": "c1", "name": "Major Mackenzie Surgery", "is_demo": False}]
    )
    f.tables["patients"] = [{"id": f"p{i}"} for i in range(patients)]
    return gc.ReadOnlyClient(f)


def run(client=None, cfg=None, **kw):
    results = gc.run_checks(
        client or db(),
        cfg or settings(),
        **{"placeholders": CLEAN, "auth_settings": SIGNUPS_OFF, **kw},
    )
    return {r.name: r for r in results}


def test_a_ready_production_passes_everything():
    out = run()
    assert [n for n, r in out.items() if not r.ok] == []
    assert out["Patient count"].detail == "2 patient(s)"
    assert "production" in out["Environment row"].detail


def test_every_check_is_named_in_the_report():
    assert set(run()) == {
        "Environment row",
        "APP_ENV matches",
        "No demo clinics",
        "Clinic name has no '(dev)'",
        "Patient count",
        "Legal documents",
        "Bedrock model",
        "SES_FROM_ADDRESS",
        "Sign-ups off",
    }


def test_no_environment_row_fails_the_row_and_the_match():
    out = run(db(None))
    assert not out["Environment row"].ok and not out["APP_ENV matches"].ok


def test_a_mismatch_between_app_env_and_the_database_fails():
    out = run(db("staging"), settings(app_env="production"))
    assert out["Environment row"].ok and not out["APP_ENV matches"].ok
    assert "staging" in out["APP_ENV matches"].detail


def test_an_unset_app_env_fails_the_match():
    assert not run(cfg=settings(app_env="", environment="development"))["APP_ENV matches"].ok


def test_demo_clinics_fail_in_production_and_name_them():
    clinics = [
        {"id": "c1", "name": "Real", "is_demo": False},
        {"id": "c2", "name": "Demo", "is_demo": True},
    ]
    out = run(db(clinics=clinics))
    assert not out["No demo clinics"].ok and "'Demo'" in out["No demo clinics"].detail


def test_a_dev_clinic_name_fails_in_production():
    clinics = [{"id": "c1", "name": "Major Mackenzie Surgery (dev)", "is_demo": False}]
    out = run(db(clinics=clinics))
    assert not out["Clinic name has no '(dev)'"].ok


def test_when_the_environment_is_unknown_the_production_checks_still_apply():
    clinics = [{"id": "c2", "name": "Demo (dev)", "is_demo": True}]
    out = run(db(None, clinics=clinics))
    assert not out["No demo clinics"].ok and not out["Clinic name has no '(dev)'"].ok


def test_staging_expects_demo_clinics_and_the_dev_name():
    clinics = [
        {"id": "c1", "name": "Major Mackenzie Surgery (dev)", "is_demo": False},
        {"id": "c2", "name": "Major Mackenzie Surgery (demo)", "is_demo": True},
    ]
    out = run(
        db("staging", clinics=clinics),
        settings(
            app_env="staging", environment="development", ses_from_address="", web_base_url=""
        ),
    )
    assert out["No demo clinics"].ok and "staging" in out["No demo clinics"].detail
    assert out["Clinic name has no '(dev)'"].ok
    assert out["SES_FROM_ADDRESS"].ok and "staging" in out["SES_FROM_ADDRESS"].detail
    assert all(r.ok for r in out.values())


def test_legal_placeholders_fail_and_are_listed():
    hits = [
        ("terms-v1.0.md", 7, "[Clinic legal name]"),
        ("privacy-v1.0.md", 11, "[Who can see it]"),
    ]
    out = run(placeholders=lambda: hits)
    assert not out["Legal documents"].ok
    assert (
        "2 placeholder(s)" in out["Legal documents"].detail
        and "[Clinic legal name]" in out["Legal documents"].detail
    )


def test_a_legal_check_that_cannot_run_fails_rather_than_passes():
    def broken():
        raise FileNotFoundError("config.json")

    assert not run(placeholders=broken)["Legal documents"].ok


def test_the_real_legal_script_is_loadable_and_reports_what_the_script_reports():
    hits = gc.legal_placeholders()
    assert isinstance(hits, list) and all(len(h) == 3 for h in hits)


def test_the_bedrock_model_and_cap_are_shown():
    out = run(cfg=settings(bedrock_model_id="m-1", ai_daily_call_cap_per_clinic=0))
    assert out["Bedrock model"].ok and "m-1" in out["Bedrock model"].detail
    assert "blocked" in out["Bedrock model"].detail
    assert not run(cfg=settings(bedrock_model_id=" "))["Bedrock model"].ok


@pytest.mark.parametrize(
    ("sender", "web", "ok"),
    [
        ("no-reply@clinicallyanchored.ai", "https://clinicallyanchored.ai", True),
        ("no-reply@clinicallyanchored.ai", "https://www.clinicallyanchored.ai", True),
        ("", "https://clinicallyanchored.ai", False),
        ("not-an-address", "https://clinicallyanchored.ai", False),
        ("someone@gmail.com", "https://clinicallyanchored.ai", False),
        ("no-reply@clinicallyanchored.ai", "", False),
    ],
)
def test_production_ses_sender(sender, web, ok):
    assert run(cfg=settings(ses_from_address=sender, web_base_url=web))["SES_FROM_ADDRESS"].ok is ok


def test_signups_must_be_disabled():
    assert not run(auth_settings=lambda u, k: {"disable_signup": False})["Sign-ups off"].ok
    assert not run(auth_settings=lambda u, k: {})["Sign-ups off"].ok

    def down(u, k):
        raise RuntimeError("network")

    assert not run(auth_settings=down)["Sign-ups off"].ok
    assert not run(cfg=settings(supabase_anon_key=""))["Sign-ups off"].ok


def test_an_unreadable_table_fails_that_check_and_the_rest_still_run():
    class Broken(gc.ReadOnlyClient):
        def table(self, name):
            if name == "patients":
                raise RuntimeError("boom")
            return super().table(name)

    out = run(Broken(db()._client))
    assert not out["Patient count"].ok and out["Environment row"].ok


def test_the_client_the_checks_get_cannot_write():
    t = db().table("patients")
    for method in ("insert", "update", "delete", "upsert"):
        assert not hasattr(t, method)
    assert not hasattr(db(), "rpc") and not hasattr(db(), "auth")


def test_a_full_run_writes_nothing():
    f = FakeSupabase()
    f.tables["app_settings"] = [{"environment": "production"}]
    f.tables["clinics"] = [{"id": "c1", "name": "A", "is_demo": False}]
    f.tables["patients"] = [{"id": "p1"}]
    before = {k: [dict(r) for r in v] for k, v in f.tables.items()}
    f.rpc_handlers.clear()
    gc.run_checks(gc.ReadOnlyClient(f), settings(), placeholders=CLEAN, auth_settings=SIGNUPS_OFF)
    assert f.rpc_calls == []
    assert {k: v for k, v in f.tables.items() if k in before and v} == {
        k: v for k, v in before.items() if v
    }


def test_the_report_has_pass_or_fail_on_every_line_and_a_total():
    out = gc.run_checks(db(), settings(app_env=""), placeholders=CLEAN, auth_settings=SIGNUPS_OFF)
    text = gc.format_report(out, "header line")
    body = [ln for ln in text.splitlines()[2:-2]]
    assert all(ln.startswith(("PASS", "FAIL")) for ln in body) and len(body) == len(out)
    assert text.splitlines()[0] == "header line" and text.splitlines()[-1].endswith("failed")
    assert "x.supabase.co" not in text and "anon" not in text  # no keys, no URL beyond the header


def test_main_exits_2_without_a_connection_and_rejects_unknown_arguments(monkeypatch, capsys):
    from clinically_anchored_api.core import config

    monkeypatch.setattr(
        config.Settings, "model_config", {**config.Settings.model_config, "env_file": None}
    )
    config.get_settings.cache_clear()
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert gc.main(["--no-env-file"]) == 2
    assert "not set" in capsys.readouterr().out
    assert gc.main(["--bogus"]) == 2
    config.get_settings.cache_clear()
