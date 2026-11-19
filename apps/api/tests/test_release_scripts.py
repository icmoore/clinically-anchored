"""scripts/db_migrate and scripts/release_check, run for real against stub psql / supabase / curl /
railway programs in a scratch git repository. Nothing here touches a database or the network."""

import os
import pty
import select
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
STAGING_REF = "abcdefghijklmnopqrst"
PROD_REF = "cdaswsjirkkdhvhegwco"

PSQL_STUB = r"""#!/usr/bin/env bash
echo "psql $*" >> "$STUB_LOG"
echo "PGPASSWORD=${PGPASSWORD:-unset}" >> "$STUB_LOG"
q="${*: -1}"
case "$q" in
  *"to_regclass('public.app_settings')"*) echo "${STUB_HAS_SETTINGS:-t}" ;;
  *"select coalesce((select environment"*) echo "${STUB_ENV:-production}" ;;
  *"to_regclass('supabase_migrations.schema_migrations')"*) echo "t" ;;
  *"select version from supabase_migrations"*) cat "$STUB_RECORDED" ;;
  *) echo "unexpected query: $q" >&2; exit 1 ;;
esac
"""
SUPABASE_STUB = r"""#!/usr/bin/env bash
echo "supabase $*" >> "$STUB_LOG"
echo "supabase PGPASSWORD=${PGPASSWORD:-unset}" >> "$STUB_LOG"
"""
CURL_STUB = r"""#!/usr/bin/env bash
case "${*: -1}" in
  *api-staging*) echo "{\"status\":\"ok\",\"environment\":\"${STUB_HEALTH_STAGING:-staging}\"}" ;;
  *api.clinicallyanchored*)
    echo "{\"status\":\"ok\",\"environment\":\"${STUB_HEALTH_PROD:-production}\"}" ;;
  *) exit 22 ;;
esac
"""
RAILWAY_STUB = r"""#!/usr/bin/env bash
echo "railway $*" >> "$STUB_LOG"
echo "Go-live check against db.x (APP_ENV=${STUB_GOLIVE_ENV:-production}). Read-only."
exit "${STUB_GOLIVE_EXIT:-0}"
"""


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, msg):
    _git(cwd, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", msg)


@pytest.fixture
def sandbox(tmp_path):
    """A scratch clone of the scripts with a local 'origin', and stubs first on PATH."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "remote", "add", "origin", str(origin))
    scripts = work / "scripts"
    shutil.copytree(REPO / "scripts" / "lib", scripts / "lib")
    for name in ("db_migrate", "release_check"):
        shutil.copy(REPO / "scripts" / name, scripts / name)
    env_file = scripts / "lib" / "environments.sh"
    env_file.write_text(
        env_file.read_text().replace("REPLACE_WITH_STAGING_PROJECT_REF", STAGING_REF)
    )
    (work / "apps" / "api").mkdir(parents=True)
    mig = work / "supabase" / "migrations"
    mig.mkdir(parents=True)
    for n in (1, 2, 3):
        (mig / f"{n:014d}_m{n}.sql").write_text("select 1;")
    _git(work, "add", "-A")
    _commit(work, "main")
    _git(work, "push", "-q", "origin", "main")
    # develop has one more migration
    _git(work, "checkout", "-q", "-b", "develop")
    (mig / f"{4:014d}_m4.sql").write_text("select 1;")
    _git(work, "add", "-A")
    _commit(work, "add m4")
    _git(work, "push", "-q", "origin", "develop")
    _git(work, "checkout", "-q", "main")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("psql", PSQL_STUB),
        ("supabase", SUPABASE_STUB),
        ("curl", CURL_STUB),
        ("railway", RAILWAY_STUB),
    ):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    recorded = tmp_path / "recorded"
    recorded.write_text("\n".join(f"{n:014d}" for n in (1, 2, 3)) + "\n")
    log = tmp_path / "log"
    log.write_text("")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
        "STUB_RECORDED": str(recorded),
    }
    return type(
        "Box",
        (),
        {"work": work, "env": env, "recorded": recorded, "log": log, "mig": mig, "origin": origin},
    )


def _run_plain(box, script, *args, stdin="pw\n", extra_env=None):
    return subprocess.run(
        ["bash", str(box.work / "scripts" / script), *args],
        cwd=box.work,
        env={**box.env, **(extra_env or {})},
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_tty(box, args, answers, extra_env=None):
    """Run db_migrate with a real terminal on stdin; send each answer when its prompt appears."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(
        ["bash", str(box.work / "scripts" / "db_migrate"), *args],
        cwd=box.work,
        env={**box.env, **(extra_env or {})},
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    os.close(slave)
    out, pending = "", list(answers)
    deadline = time.time() + 30
    while time.time() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.2)
        if ready:
            chunk = os.read(proc.stdout.fileno(), 4096).decode()
            if not chunk:
                break
            out += chunk
            if pending and out.rstrip().endswith(pending[0][0]):
                os.write(master, (pending.pop(0)[1] + "\n").encode())
        elif proc.poll() is not None:
            break
    proc.wait(timeout=10)
    os.close(master)
    return proc.returncode, out


PW = ("(input hidden):", "sekret")


def _log(box):
    return box.log.read_text()


def _pushed(box):
    """True if the supabase CLI was ever run (psql lines mention supabase.com: match whole lines)."""
    return any(line.startswith("supabase ") for line in _log(box).splitlines())


# ---- db_migrate: refusals that happen before anything connects ---------------------------------


def test_db_migrate_needs_a_known_environment(sandbox):
    r = _run_plain(sandbox, "db_migrate", "prod")
    assert r.returncode == 2 and "Usage" in r.stderr


def test_db_migrate_takes_no_project_from_arguments(sandbox):
    r = _run_plain(sandbox, "db_migrate", "staging", "--ref=zzzzzzzzzzzzzzzzzzzz")
    assert r.returncode == 2 and "Unknown option" in r.stderr


def test_db_migrate_refuses_while_the_staging_ref_is_a_placeholder(sandbox):
    path = sandbox.work / "scripts" / "lib" / "environments.sh"
    path.write_text(path.read_text().replace(STAGING_REF, "REPLACE_WITH_STAGING_PROJECT_REF"))
    r = _run_plain(sandbox, "db_migrate", "staging")
    assert r.returncode == 1 and "not filled in" in r.stderr


def test_db_migrate_refuses_in_ci(sandbox):
    r = _run_plain(sandbox, "db_migrate", "staging", extra_env={"CI": "true"})
    assert r.returncode == 1 and "never run in CI" in r.stderr


def test_production_refuses_off_main(sandbox):
    _git(sandbox.work, "checkout", "-q", "develop")
    r = _run_plain(sandbox, "db_migrate", "production")
    assert r.returncode == 1 and "only from main (you are on 'develop')" in r.stderr
    assert "psql" not in _log(sandbox)


def test_production_refuses_when_main_is_behind_origin(sandbox):
    _git(sandbox.work, "checkout", "-q", "-b", "other", "main")
    (sandbox.mig / f"{9:014d}_x.sql").write_text("select 1;")
    _git(sandbox.work, "add", "-A")
    _commit(sandbox.work, "ahead")
    _git(sandbox.work, "push", "-q", "origin", "other:main")
    _git(sandbox.work, "checkout", "-q", "main")  # local main is now one commit behind
    r = _run_plain(sandbox, "db_migrate", "production")
    assert r.returncode == 1 and "not equal to origin/main" in r.stderr


def test_production_refuses_without_a_terminal(sandbox):
    r = _run_plain(sandbox, "db_migrate", "production")  # on main and up to date
    assert r.returncode == 1 and "interactive terminal" in r.stderr
    assert "psql" not in _log(sandbox)


# ---- db_migrate: with a terminal ---------------------------------------------------------------


def test_dry_run_lists_pending_and_pushes_nothing(sandbox):
    sandbox.recorded.write_text("00000000000001\n00000000000002\n")
    code, out = _run_tty(sandbox, ["staging", "--dry-run"], [PW], {"STUB_ENV": "staging"})
    assert code == 0, out
    assert f"Project     : {STAGING_REF}" in out
    assert (
        "00000000000003_m3.sql" in out
        and "00000000000002_m2.sql" not in out.split("Migrations still")[1]
    )
    assert "Dry run" in out
    assert not _pushed(sandbox)


def test_nothing_pending_is_a_clean_no_op(sandbox):
    code, out = _run_tty(sandbox, ["staging"], [PW], {"STUB_ENV": "staging"})
    assert code == 0 and "Nothing to apply" in out
    assert not _pushed(sandbox)


def test_a_project_marked_as_the_other_environment_is_refused(sandbox):
    code, out = _run_tty(sandbox, ["staging"], [PW], {"STUB_ENV": "production"})
    assert code == 1 and "says it is 'production', not 'staging'" in out
    assert not _pushed(sandbox)


def test_a_project_not_marked_yet_is_allowed(sandbox):
    sandbox.recorded.write_text("")
    code, out = _run_tty(sandbox, ["staging", "--dry-run"], [PW], {"STUB_HAS_SETTINGS": "f"})
    assert code == 0 and "app_settings: NOT SET" in out and "(3)" in out


def test_a_database_with_migrations_this_checkout_lacks_is_refused(sandbox):
    sandbox.recorded.write_text("00000000000001\n00000000000007\n")
    code, out = _run_tty(sandbox, ["staging"], [PW], {"STUB_ENV": "staging"})
    assert code == 1 and "00000000000007" in out and "git pull" in out


def test_a_wrong_confirmation_applies_nothing(sandbox):
    sandbox.recorded.write_text("00000000000001\n")
    code, out = _run_tty(
        sandbox, ["staging"], [PW, ("to continue:", "production")], {"STUB_ENV": "staging"}
    )
    assert code == 1 and "nothing was applied" in out
    assert not _pushed(sandbox)


def test_typing_the_environment_name_pushes_with_db_url_and_no_password_in_it(sandbox):
    sandbox.recorded.write_text("00000000000001\n")
    code, out = _run_tty(
        sandbox, ["staging"], [PW, ("to continue:", "staging")], {"STUB_ENV": "staging"}
    )
    assert code == 0, out
    log = _log(sandbox)
    url = (
        f"postgresql://postgres.{STAGING_REF}@aws-0-ca-central-1.pooler.supabase.com:5432/postgres"
    )
    assert f"supabase db push --db-url {url}" in log
    assert "sekret" not in log.replace("PGPASSWORD=sekret", "")  # only ever in the environment
    assert "supabase PGPASSWORD=sekret" in log


def test_production_from_an_up_to_date_main_works(sandbox):
    sandbox.recorded.write_text("00000000000001\n")
    code, out = _run_tty(
        sandbox, ["production"], [PW, ("to continue:", "production")], {"STUB_ENV": "production"}
    )
    assert code == 0, out
    assert f"Project     : {PROD_REF}" in out
    assert f"db push --db-url postgresql://postgres.{PROD_REF}@" in _log(sandbox)


def test_an_inherited_pgpassword_is_never_reused(sandbox):
    code, out = _run_tty(
        sandbox,
        ["staging", "--dry-run"],
        [PW],
        {"STUB_ENV": "staging", "PGPASSWORD": "other-project"},
    )
    assert code == 0
    assert "other-project" not in _log(sandbox)


# ---- release_check -----------------------------------------------------------------------------


def test_release_check_passes_when_everything_lines_up(sandbox):
    r = _run_plain(sandbox, "release_check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "All checks passed." in r.stdout
    assert "add m4" in r.stdout  # in develop, not in main
    assert "00000000000004_m4.sql" in r.stdout  # the migration arriving with the release
    assert "railway run --service clinically-anchored " in _log(sandbox)


def test_release_check_fails_on_a_migration_gap(sandbox):
    sandbox.recorded.write_text("00000000000001\n00000000000002\n")
    r = _run_plain(sandbox, "release_check")
    assert r.returncode == 1
    assert "FAIL  migrations in main but NOT recorded in production" in r.stdout
    assert "00000000000003_m3.sql" in r.stdout


def test_release_check_fails_when_production_says_staging(sandbox):
    r = _run_plain(sandbox, "release_check", extra_env={"STUB_ENV": "staging"})
    assert r.returncode == 1 and "says it is 'staging', not production" in r.stdout


def test_release_check_fails_on_the_wrong_health_environment(sandbox):
    r = _run_plain(sandbox, "release_check", extra_env={"STUB_HEALTH_STAGING": "production"})
    assert r.returncode == 1
    assert (
        "api-staging.clinicallyanchored.ai/health says environment='production', expected 'staging'"
        in r.stdout
    )


def test_release_check_fails_when_golive_check_fails_or_runs_as_the_wrong_environment(sandbox):
    r = _run_plain(sandbox, "release_check", extra_env={"STUB_GOLIVE_EXIT": "1"})
    assert r.returncode == 1 and "golive_check exited 1" in r.stdout
    r = _run_plain(sandbox, "release_check", extra_env={"STUB_GOLIVE_ENV": "staging"})
    assert r.returncode == 1 and "did not run as APP_ENV=production" in r.stdout


def test_release_check_writes_nothing(sandbox):
    _run_plain(sandbox, "release_check")
    log = _log(sandbox)
    assert not _pushed(sandbox)
    assert (
        "insert" not in log.lower() and "update" not in log.lower() and "delete" not in log.lower()
    )
