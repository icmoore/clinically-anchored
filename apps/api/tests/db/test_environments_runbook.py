"""The SQL in docs/environments.md, run for real.

Each tested block in the runbook is tagged `<!-- sql:name -->` above its ```sql fence. This test
pulls the blocks out of the document and runs them with psql against scratch databases, so the
runbook cannot drift from what the schema allows: the cutover (demo clinics, patient logins,
rename, production row), adding a clinician, and removing the smoke-test patient."""

import re
import subprocess

import pytest

from tests.db.conftest import (
    REPO,
    SCRIPTS,
    build_world,
    check_in,
    make_episode,
    new_patient,
    patient_msg,
)

pytestmark = pytest.mark.db

DOC = REPO / "docs" / "environments.md"
DEV_CLINIC = "00000000-0000-0000-0000-0000000000c1"
BLOCK = re.compile(r"<!-- sql:(\w+) -->\n(\s*)```sql\n(.*?)\n\s*```", re.S)


def block(name: str) -> str:
    for m in BLOCK.finditer(DOC.read_text()):
        if m.group(1) == name:
            indent = m.group(2)
            return "\n".join(
                line[len(indent):] if line.startswith(indent) else line
                for line in m.group(3).splitlines()
            ) + "\n"
    raise AssertionError(f"no sql:{name} block in {DOC.name}")


def run(db, tmp_path, text: str):
    f = tmp_path / "block.sql"
    f.write_text(text)
    return subprocess.run(
        ["psql", db.conninfo, "-X", "-f", str(f)], capture_output=True, text=True
    )


def run_ok(db, tmp_path, text: str) -> str:
    out = run(db, tmp_path, text)
    assert out.returncode == 0, out.stderr
    return out.stdout


def reset_sql(db):
    return subprocess.run(
        ["psql", db.conninfo, "-X", "-v", "ON_ERROR_STOP=1", "-v", "execute=true", "-f",
         str(SCRIPTS / "reset_trial_data.sql")],
        capture_output=True, text=True,
    )


def names(conn, table="clinics"):
    return {r["name"] for r in conn.execute(f"select name from {table}")}


def emails(conn):
    return {r["email"] for r in conn.execute("select email from auth.users")}


def test_every_tagged_block_in_the_runbook_is_found():
    found = {m.group(1) for m in BLOCK.finditer(DOC.read_text())}
    assert found >= {
        "staging_row", "staging_demo_clinic", "cutover_look", "cutover_demo_clinics",
        "cutover_patient_users_look", "cutover_patient_users_delete", "cutover_rename",
        "cutover_production_row", "cutover_find_test_patient", "cutover_remove_test_patient",
        "add_clinician",
    }


def test_staging_setup_blocks(db, tmp_path):
    run_ok(db, tmp_path, block("staging_row"))
    assert db.conn.execute("select environment from app_settings").fetchone() == {
        "environment": "staging"
    }
    run_ok(db, tmp_path, block("staging_demo_clinic"))
    run_ok(db, tmp_path, block("staging_demo_clinic"))  # re-running adds nothing
    demos = db.conn.execute(
        "select count(*) as n from clinics where is_demo and name = 'Major Mackenzie Surgery (demo)'"
    ).fetchone()["n"]
    assert demos == 1


def test_the_whole_cutover_in_order(db, tmp_path):
    c = db.conn
    w = build_world(c)  # clinic_a, clinic_b (made a demo clinic below), clinicians user_a, user_b
    c.execute("update clinics set is_demo = true where id = %s", (w.clinic_b,))
    c.execute(  # the old dev clinic (migration 2) keeps its fixed id and its "(dev)" name
        "insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'owner')",
        (DEV_CLINIC, w.user_a),
    )
    patient_user = c.execute(
        "insert into auth.users (email, raw_app_meta_data) values "
        "('trial-patient-1.demo@example.com', '{\"account_type\": \"patient\"}') returning id"
    ).fetchone()["id"]
    c.execute(
        "insert into patient_accounts (clinic_id, patient_id, user_id, status, invited_by) "
        "values (%s, %s, %s, 'invited', %s)", (w.clinic_a, w.patient_a, patient_user, w.user_a),
    )
    c.execute(
        "insert into audit_log (clinic_id, event_type, payload_hash, prev_hash, row_hash, "
        "signature, key_id) values (%s, 'patient.created', 'p', null, 'r', 's', 'dev-1')",
        (w.clinic_a,),
    )

    # Before the reset a demo clinic with patients can't be deleted: the block says so.
    refused = run(db, tmp_path, block("cutover_demo_clinics"))
    assert refused.returncode != 0 and "Run the reset" in refused.stderr
    assert "Clinic b" in names(c)

    # b. look, then reset (the project has no row yet: allowed)
    out = run_ok(db, tmp_path, block("cutover_look"))
    assert "trial-patient-1.demo@example.com" in out and "a@example.com" in out
    done = reset_sql(db)
    assert done.returncode == 0, done.stderr

    # c. demo clinics
    run_ok(db, tmp_path, block("cutover_demo_clinics"))
    assert "Clinic b" not in names(c) and "Clinic a" in names(c)

    # d. patient logins: only the patient's goes; both clinicians stay (user_b lost its
    # demo-clinic membership with c and now sits in neither list, which the look block shows).
    look = run_ok(db, tmp_path, block("cutover_patient_users_look"))
    assert "trial-patient-1.demo@example.com" in look
    run_ok(db, tmp_path, block("cutover_patient_users_delete"))
    assert "trial-patient-1.demo@example.com" not in emails(c)
    assert {"a@example.com", "b@example.com"} <= emails(c)

    # e. rename
    out = run_ok(db, tmp_path, block("cutover_rename"))
    assert "UPDATE 1" in out
    assert "Major Mackenzie Surgery" in names(c) and "Major Mackenzie Surgery (dev)" not in names(c)

    # f. production row, then every guard is on
    run_ok(db, tmp_path, block("cutover_production_row"))
    assert c.execute("select environment from app_settings").fetchone()["environment"] == "production"
    again = reset_sql(db)
    assert again.returncode != 0 and "PRODUCTION" in again.stderr
    with pytest.raises(Exception, match="demo clinic refused"):
        c.execute("insert into clinics (name, is_demo) values ('Demo', true)")


def test_adding_a_clinician(db, tmp_path):
    c = db.conn
    c.execute("insert into auth.users (email) values ('doctor@example.org')")
    out = run_ok(db, tmp_path, block("add_clinician"))
    assert "INSERT 0 1" in out
    row = c.execute(
        "select m.role from clinic_members m join auth.users u on u.id = m.user_id "
        "where u.email = 'doctor@example.org' and m.clinic_id = %s", (DEV_CLINIC,)
    ).fetchone()
    assert row == {"role": "clinician"}
    assert "INSERT 0 0" in run_ok(db, tmp_path, block("add_clinician"))  # again: nothing new
    # An email that matches nobody inserts nothing, and says so.
    typo = block("add_clinician").replace("doctor@example.org", "nobody@example.org")
    assert "INSERT 0 0" in run_ok(db, tmp_path, typo)


def smoke_patient(c, w, *, name="TEST – do not treat", account=False):
    """A patient as the smoke test leaves one, plus the rows an invited one would have."""
    pid = new_patient(c, w, name)
    episode = make_episode(c, w, patient=pid)
    check_in(c, w, episode)
    patient_msg(c, w, episode, origin="scheduled", interval=1, category="medication", body="hi")
    c.execute(
        "insert into consents (clinic_id, patient_id, consent_type, consent_text_version) "
        "values (%s, %s, 'messaging', 'v1')", (w.clinic_a, pid),
    )
    c.execute(
        "insert into touchpoints (clinic_id, patient_id, kind, source) values (%s, %s, 'call', 'manual')",
        (w.clinic_a, pid),
    )
    user = None
    if account:
        user = c.execute(
            "insert into auth.users (email, raw_app_meta_data) values (%s, "
            "'{\"account_type\": \"patient\"}') returning id", (f"{pid}@example.org",)
        ).fetchone()["id"]
        acct = c.execute(
            "insert into patient_accounts (clinic_id, patient_id, user_id, status, invited_by) "
            "values (%s, %s, %s, 'invited', %s) returning id", (w.clinic_a, pid, user, w.user_a),
        ).fetchone()["id"]
        c.execute(
            "insert into patient_sign_in_links (clinic_id, account_id, token_hash, issued_by, "
            "expires_at) values (%s, %s, repeat('a', 64), %s, now() + interval '1 day')",
            (w.clinic_a, acct, w.user_a),
        )
        c.execute(
            "insert into email_outbox (clinic_id, patient_id, recipient, template, status, "
            "rendered_subject, rendered_text, rendered_html) values "
            "(%s, %s, 'pat@example.org', 'invite', 'demo', 's', 't', '<p>t</p>')",
            (w.clinic_a, pid),
        )
    c.execute(
        "insert into audit_log (clinic_id, event_type, payload_hash, prev_hash, row_hash, "
        "signature, key_id) values (%s, 'patient.created', %s, %s, %s, 's', 'dev-1')",
        (w.clinic_a, f"p-{pid}", f"prev-{pid}", f"r-{pid}"),
    )
    return pid, user


def remove_block(pid: str) -> str:
    text = block("cutover_remove_test_patient")
    return re.sub(r"\\set pid '<[^>]*>'", f"\\\\set pid '{pid}'", text)


def counts(c, pid):
    tables = ["episodes", "check_ins", "messages", "consents", "touchpoints", "patient_accounts",
              "email_outbox"]
    out = {t: c.execute(f"select count(*) as n from {t} where patient_id = %s", (pid,)).fetchone()["n"]
           for t in tables}
    out["patients"] = c.execute("select count(*) as n from patients where id = %s", (pid,)).fetchone()["n"]
    return out


@pytest.mark.parametrize("account", [False, True])
def test_the_smoke_test_patient_is_removed_and_nobody_else(db, tmp_path, account):
    c = db.conn
    w = build_world(c)
    c.execute("insert into app_settings (environment) values ('production')")
    pid, user = smoke_patient(c, w, account=account)
    other, _ = smoke_patient(c, w, name="Real Person")
    audit_before = c.execute("select count(*) as n from audit_log").fetchone()["n"]

    found = run_ok(db, tmp_path, block("cutover_find_test_patient"))
    assert pid in found and other not in found  # exactly the one test patient

    out = run_ok(db, tmp_path, remove_block(pid))
    assert "DELETE 1" in out
    assert all(n == 0 for n in counts(c, pid).values())
    assert counts(c, other)["patients"] == 1 and counts(c, other)["messages"] >= 1
    if account:
        assert user not in {r["id"] for r in c.execute("select id from auth.users")}
    # The audit log is append-only: nothing was removed from it.
    assert c.execute("select count(*) as n from audit_log").fetchone()["n"] == audit_before


def test_removal_also_clears_the_append_only_rows_and_puts_every_guard_back(db, tmp_path):
    c = db.conn
    w = build_world(c)
    pid, _ = smoke_patient(c, w)
    episode = c.execute("select id from episodes where patient_id = %s", (pid,)).fetchone()["id"]
    c.execute(
        "insert into patient_acceptances (id, clinic_id, patient_id, episode_id, action, "
        "terms_version, privacy_version, consent_version, terms_sha256, privacy_sha256, "
        "consent_sha256) values (gen_random_uuid(), %s, %s, %s, 'accepted', '1.0', '1.0', "
        "'m:1.0', repeat('a', 64), repeat('b', 64), repeat('c', 64))", (w.clinic_a, pid, episode),
    )
    guards_before = enabled_guards(c)
    run_ok(db, tmp_path, remove_block(pid))
    assert counts(c, pid)["patients"] == 0
    assert c.execute("select count(*) as n from patient_acceptances where patient_id = %s", (pid,)
                     ).fetchone()["n"] == 0
    assert enabled_guards(c) == guards_before  # every retention guard is on again


def test_removal_refuses_a_patient_whose_name_does_not_start_with_test(db, tmp_path):
    c = db.conn
    w = build_world(c)
    pid, _ = smoke_patient(c, w, name="Sarah's Real Patient")
    before = counts(c, pid)
    out = run(db, tmp_path, remove_block(pid))
    assert out.returncode != 0 and "starts with TEST" in out.stderr
    assert counts(c, pid) == before


def test_a_failed_removal_rolls_back_the_guards_too(db, tmp_path):
    c = db.conn
    w = build_world(c)
    pid, _ = smoke_patient(c, w)
    guards_before = enabled_guards(c)
    # Break the block part-way (after the guards are off): it must leave everything as it was.
    broken = remove_block(pid).replace("delete from patients where id", "select 1/0; delete from patients where id")
    out = run(db, tmp_path, broken)
    assert out.returncode != 0 and "division by zero" in out.stderr
    assert counts(c, pid)["patients"] == 1 and counts(c, pid)["episodes"] == 1
    assert enabled_guards(c) == guards_before


def enabled_guards(c):
    return {
        (r["tbl"], r["tgname"], r["tgenabled"]) for r in c.execute(
            "select tgrelid::regclass::text as tbl, tgname, tgenabled from pg_trigger "
            "where not tgisinternal"
        )
    }
