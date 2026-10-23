"""The backup reset_trial_data.sh takes can be put back: supabase/scripts/restore_trial_data.{sh,sql}
and backup_common.sh against a real Postgres. The data used has what broke a plain pg_dump
restore: replies (the reply guard), several messages per episode, and a signed audit chain."""

import os
import pty
import re
import select
import shutil
import subprocess
import time

import pytest

from tests.db.conftest import (
    SCRIPTS,
    ask_extension,
    build_world,
    check_in,
    clinician_msg,
    end_by_schedule,
    make_episode,
    new_patient,
    patient_msg,
)

COMMON = SCRIPTS / "backup_common.sh"
RESTORE_SQL = SCRIPTS / "restore_trial_data.sql"
RESTORE_SH = SCRIPTS / "restore_trial_data.sh"
RESET_SH = SCRIPTS / "reset_trial_data.sh"
RESET_SQL = SCRIPTS / "reset_trial_data.sql"
TABLES = ["message_drafts", "thread_summaries", "patient_briefs", "care_provider_summaries",
          "care_provider_summary_edits", "touchpoints", "consents", "extension_requests", "messages",
          "check_ins",
          "episodes", "patients", "audit_log"]

needs_tools = pytest.mark.skipif(
    not (shutil.which("psql") and shutil.which("pg_dump")), reason="psql and pg_dump needed"
)
pytestmark = [pytest.mark.db, needs_tools]


def sh(script: str, *args, env=None):
    return subprocess.run(
        ["bash", "-c", f'source "{COMMON}"; {script}', "_", *args],
        capture_output=True, text=True, env=env,
    )


def fingerprint(conn) -> dict:
    out = {}
    for t in TABLES:
        rows = conn.execute(f"select x::text as r from {t} x order by 1").fetchall()
        out[t] = [r["r"] for r in rows]
    return out


def populate(conn):
    """Two clinics' worth of everything the backup covers, with messages inserted so that replies
    sit in the middle of their episode's thread."""
    w = build_world(conn)
    episode = make_episode(conn, w)
    check_in(conn, w, episode)
    clinician_msg(conn, w, episode, body="how are you")
    patient_msg(conn, w, episode, origin="reply", body="fine")
    patient_msg(conn, w, episode, interval=1, body="scheduled")
    clinician_msg(conn, w, episode, body="again")
    other = make_episode(
        conn, w, patient=w.patient_a2,
    )
    clinician_msg(conn, w, other, body="and you")
    patient_msg(conn, w, other, origin="reply", body="ok")
    conn.execute(
        "insert into message_drafts (clinic_id, patient_id, source_message_id, draft_text, model_id, "
        "prompt_version, created_by) values (%s, %s, (select id from messages limit 1), 'd', 'm', 'v', %s)",
        (w.clinic_a, w.patient_a, w.user_a),
    )
    conn.execute(
        "insert into touchpoints (clinic_id, patient_id, kind, source) values (%s, %s, 'call', 'manual')",
        (w.clinic_a, w.patient_a),
    )
    conn.execute(
        "insert into consents (clinic_id, patient_id, consent_type, consent_text_version) "
        "values (%s, %s, 'messaging', 'v1')",
        (w.clinic_a, w.patient_a),
    )
    conn.execute(
        "insert into thread_summaries (id, clinic_id, patient_id, lines, covers_messages, truncated, "
        "model_id, prompt_version, generated_by, generated_at) values "
        "(gen_random_uuid(), %s, %s, '[\"a\"]', 1, false, 'm', 'summary_v4', %s, now())",
        (w.clinic_a, w.patient_a, w.user_a),
    )
    if conn.execute("select to_regclass('patient_briefs') as t").fetchone()["t"]:  # migration 13
        conn.execute(
            "insert into patient_briefs (id, clinic_id, patient_id, episode_id, bullets, covers, "
            "truncated, model_id, prompt_version, generated_by, generated_at) values "
            "(gen_random_uuid(), %s, %s, %s, '[{\"label\": \"Trends\", \"text\": \"t\", "
            "\"sources\": []}]', '{\"messages\": 1}', false, 'm', 'patient_brief_v1', %s, now())",
            (w.clinic_a, w.patient_a, episode, w.user_a),
        )
    if conn.execute("select to_regclass('care_provider_summaries') as t").fetchone()["t"]:  # 14
        summary_id = conn.execute(
            "insert into care_provider_summaries (id, clinic_id, patient_id, episode_id, "
            "summary_text, covers, truncated, model_id, prompt_version, generated_by, "
            "generated_at) values (gen_random_uuid(), %s, %s, %s, 'text', '{\"messages\": 1}', "
            "false, 'm', 'care_provider_summary_v1', %s, now()) returning id",
            (w.clinic_a, w.patient_a, episode, w.user_a),
        ).fetchone()["id"]
        if conn.execute("select to_regclass('care_provider_summary_edits') as t").fetchone()["t"]:
            conn.execute(  # migration 15
                "insert into care_provider_summary_edits (id, clinic_id, patient_id, summary_id, "
                "edited_text, edited_by, edited_at) values (gen_random_uuid(), %s, %s, %s, "
                "'edited text', %s, now())",
                (w.clinic_a, w.patient_a, summary_id, w.user_a),
            )
    # An ended episode with a pending request, so the new table (and its rows) are in the backup.
    if conn.execute("select to_regclass('extension_requests') as t").fetchone()["t"]:  # 16
        ended = make_episode(conn, w, patient=new_patient(conn, w))
        end_by_schedule(conn, w, ended)
        ask_extension(conn, w, ended)
    prev = None
    for n in range(3):
        row = conn.execute(
            "insert into audit_log (clinic_id, event_type, payload_hash, prev_hash, row_hash, "
            "signature, key_id) values (%s, 'patient.created', %s, %s, %s, 's', 'dev-1') returning row_hash",
            (w.clinic_a, f"p{n}", prev, f"r{n}"),
        ).fetchone()
        prev = row["row_hash"]
    return w, episode


def take(db, tmp_path):
    raw = tmp_path / "raw.sql"
    prepared = tmp_path / "prepared.sql"
    out = sh('take_backup "$1" "$2" && prepare_backup "$2" "$3"', db.conninfo, str(raw), str(prepared))
    assert out.returncode == 0, out.stderr
    return raw, prepared


def tables_csv() -> str:
    return sh("backup_tables_csv").stdout.strip()


def restore(db, prepared, mode):
    return subprocess.run(
        ["psql", db.conninfo, "-X", "-v", "ON_ERROR_STOP=1", "-v", f"mode={mode}", "-v",
         f"tables={tables_csv()}", "-v", f"backup={prepared}", "-f", str(RESTORE_SQL)],
        capture_output=True, text=True,
    )


def reset(db):
    out = subprocess.run(
        ["psql", db.conninfo, "-X", "-v", "ON_ERROR_STOP=1", "-v", "execute=true", "-f", str(RESET_SQL)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr


def raw_restore(db, raw):
    """What a plain `psql -f backup.sql` does, minus the one line an older server cannot parse."""
    text = "".join(
        line for line in raw.read_text().splitlines(keepends=True)
        if not line.startswith("SET transaction_timeout")
    )
    f = raw.with_name("raw_for_restore.sql")
    f.write_text(text)
    return subprocess.run(
        ["psql", db.conninfo, "-X", "-v", "ON_ERROR_STOP=1", "-1", "-f", str(f)],
        capture_output=True, text=True,
    )


def test_a_prepared_backup_restores_everything_exactly(db, tmp_path):
    c = db.conn
    populate(c)
    before = fingerprint(c)
    assert len(before["messages"]) >= 4 and len(before["audit_log"]) == 3
    _, prepared = take(db, tmp_path)

    reset(db)
    assert all(not rows for rows in fingerprint(c).values())

    out = restore(db, prepared, "commit")
    assert out.returncode == 0, out.stderr
    assert "COMMITTED" in out.stdout
    assert fingerprint(c) == before


def test_audit_ids_are_not_reused_after_a_restore(db, tmp_path):
    c = db.conn
    w, _ = populate(c)
    top = c.execute("select max(id) as m from audit_log").fetchone()["m"]
    _, prepared = take(db, tmp_path)
    reset(db)
    assert restore(db, prepared, "commit").returncode == 0
    new = c.execute(
        "insert into audit_log (clinic_id, event_type, payload_hash, prev_hash, row_hash, signature, "
        "key_id) values (%s, 'patient.created', 'x', 'r2', 'r3', 's', 'dev-1') returning id",
        (w.clinic_a,),
    ).fetchone()["id"]
    assert new > top


def test_the_protections_are_still_on_after_a_restore(db, tmp_path):
    """Nothing here disables a trigger, so none can be left disabled."""
    c = db.conn
    populate(c)
    _, prepared = take(db, tmp_path)
    reset(db)
    assert restore(db, prepared, "commit").returncode == 0
    off = c.execute(
        "select tgrelid::regclass::text as t, tgname from pg_trigger "
        "where not tgisinternal and tgenabled <> 'O' and tgrelid::regclass::text = any(%s)",
        (TABLES,),
    ).fetchall()
    assert off == []
    with pytest.raises(Exception, match="message_drafts_retained"):
        c.execute("delete from message_drafts")
    with pytest.raises(Exception, match="reply_target_invalid"):
        c.execute(
            "insert into messages (clinic_id, patient_id, episode_id, sender, body, origin, "
            "reply_to_message_id) select clinic_id, patient_id, episode_id, 'patient', 'x', 'reply', "
            "id from messages where origin = 'scheduled' limit 1"
        )


def test_check_mode_loads_and_rolls_back(db, tmp_path):
    c = db.conn
    populate(c)
    _, prepared = take(db, tmp_path)
    reset(db)
    out = restore(db, prepared, "check")
    assert out.returncode == 0, out.stderr
    assert "CHECK ONLY" in out.stdout and "loaded" in out.stderr
    assert all(not rows for rows in fingerprint(c).values())


def test_restore_refuses_to_load_over_existing_rows(db, tmp_path):
    c = db.conn
    populate(c)
    _, prepared = take(db, tmp_path)
    before = fingerprint(c)
    for mode in ("check", "commit"):
        out = restore(db, prepared, mode)
        assert out.returncode != 0
        assert "restore needs empty tables" in out.stderr and "messages" in out.stderr
    assert fingerprint(c) == before


def test_the_backup_restores_whatever_order_its_rows_were_written_in(db, tmp_path):
    """pg_dump writes rows in physical order; a reply can come before the message it answers."""
    c = db.conn
    populate(c)
    before = fingerprint(c)
    raw, prepared = take(db, tmp_path)
    text = raw.read_text()
    m = re.search(r"(COPY public\.messages \([^\n]*\) FROM stdin;\n)(.*?)(\\\.\n)", text, re.S)
    rows = m.group(2).splitlines(keepends=True)
    assert len(rows) >= 4
    flipped = text[: m.start(2)] + "".join(reversed(rows)) + text[m.end(2):]
    raw.write_text(flipped)

    # Straight out of pg_dump in that order it cannot be restored ...
    reset(db)
    out = raw_restore(db, raw)
    assert out.returncode != 0
    assert "reply_target_invalid" in out.stderr or 'relation "messages" does not exist' in out.stderr
    # ... the prepared copy of the same file can.
    prepared2 = tmp_path / "prepared2.sql"
    assert sh('prepare_backup "$1" "$2"', str(raw), str(prepared2)).returncode == 0
    out = restore(db, prepared2, "commit")
    assert out.returncode == 0, out.stderr
    assert fingerprint(c) == before


def test_migration_12_lets_a_raw_pg_dump_restore_but_before_it_the_reply_guard_breaks_it(
    db, db_pre12, tmp_path
):
    """A person who restores a backup by plain psql (not our script) hits the empty search_path."""
    populate(db_pre12.conn)
    raw, _ = take(db_pre12, tmp_path)
    reset(db_pre12)
    out = raw_restore(db_pre12, raw)
    assert out.returncode != 0 and 'relation "messages" does not exist' in out.stderr

    # The guard still does its job after migration 12 ...
    populate(db.conn)
    raw2, _ = take(db, tmp_path)
    reset(db)
    # ... and now the same dump restores with plain psql, replies included.
    out = raw_restore(db, raw2)
    # (message row order in this dump may still be unlucky; only the search_path failure is gone)
    assert 'relation "messages" does not exist' not in out.stderr


def test_a_backup_that_loads_wrong_is_caught_by_verify_and_changes_nothing(db, tmp_path):
    c = db.conn
    populate(c)
    before = fingerprint(c)
    raw, prepared = take(db, tmp_path)
    assert restore(db, prepared, "verify").returncode == 0
    assert fingerprint(c) == before  # verify itself always rolls back

    text = prepared.read_text()
    m = re.search(r"(COPY public\.touchpoints \([^\n]*\) FROM stdin;\n)(.*?)(\\\.\n)", text, re.S)
    prepared.write_text(text[: m.start(2)] + text[m.end(2):])  # a touchpoint went missing
    out = restore(db, prepared, "verify")
    assert out.returncode != 0
    assert "does not restore to the same data" in out.stderr and "touchpoints" in out.stderr
    assert fingerprint(c) == before


def test_verify_passes_before_migration_9_too(db_pre9, tmp_path):
    from tests.db.test_reset_trial_data import populate_pre9

    populate_pre9(db_pre9.conn)
    before = {t: db_pre9.conn.execute(f"select count(*) as n from {t}").fetchone()["n"]
              for t in ("patients", "messages", "audit_log")}
    _, prepared = take(db_pre9, tmp_path)
    out = restore(db_pre9, prepared, "verify")
    assert out.returncode == 0, out.stderr
    assert "VERIFIED" in out.stdout
    assert {t: db_pre9.conn.execute(f"select count(*) as n from {t}").fetchone()["n"]
            for t in before} == before


# --- the real shell scripts, driven through a terminal -------------------------------------------

def url_for(db) -> str:
    return f"postgresql://{db.info.get('user', 'postgres')}:@/{db.name}?host={db.info['host']}"


def run_in_terminal(cmd, env, answers, timeout=60):
    """Run cmd on a pseudo-terminal, answer its prompts. Returns (exit code, everything it printed)."""
    master, slave = pty.openpty()
    proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True)
    os.close(slave)
    out = b""
    deadline = time.time() + timeout
    answered = 0
    while time.time() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        elif proc.poll() is not None:
            break
        if answered < len(answers):
            prompt, reply = answers[answered]
            m = re.search(prompt.encode(), out)
            if m:
                reply = reply(out.decode()) if callable(reply) else reply
                os.write(master, (reply + "\n").encode())
                answered += 1
    proc.wait(timeout=10)
    os.close(master)
    return proc.returncode, out.decode()


def typed_host(printed: str) -> str:
    return re.search(r"Type the host \('(.*?)'\)", printed).group(1)


def script_env(db):
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS")}
    env["DATABASE_URL"] = url_for(db)
    return env


def test_reset_script_makes_a_backup_proves_it_then_restore_script_brings_it_back(db, tmp_path):
    c = db.conn
    populate(c)
    before = fingerprint(c)
    backups = SCRIPTS / ".backups"
    existing = set(backups.glob("*.sql")) if backups.exists() else set()
    try:
        code, printed = run_in_terminal(
            [str(RESET_SH), "--execute"], script_env(db),
            [(r"Type the host", lambda p: typed_host(p))],
        )
        assert code == 0, printed
        assert "VERIFIED" in printed and "COMMITTED" in printed
        assert printed.index("VERIFIED") < printed.index("COMMITTED")  # proven before wiping
        assert all(not rows for rows in fingerprint(c).values())
        made = set(backups.glob("*.sql")) - existing
        assert len(made) == 1
        backup = made.pop()
        assert oct(backup.stat().st_mode & 0o777) == "0o600"

        # Dry run of the restore: loads, rolls back.
        env = script_env(db)
        out = subprocess.run([str(RESTORE_SH), str(backup)], env=env, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert "CHECK ONLY" in out.stdout
        assert all(not rows for rows in fingerprint(c).values())

        # Without a terminal, --execute refuses.
        out = subprocess.run([str(RESTORE_SH), str(backup), "--execute"], env=env,
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert out.returncode == 1 and "interactive terminal" in out.stderr

        # Wrong host typed: nothing changes. Right host: restored.
        code, printed = run_in_terminal(
            [str(RESTORE_SH), str(backup), "--execute"], env, [(r"Type the host", "nope")]
        )
        assert code == 1 and "Nothing changed" in printed
        assert all(not rows for rows in fingerprint(c).values())
        code, printed = run_in_terminal(
            [str(RESTORE_SH), str(backup), "--execute"], env,
            [(r"Type the host", lambda p: typed_host(p))],
        )
        assert code == 0, printed
        assert "COMMITTED" in printed
        assert fingerprint(c) == before
    finally:
        for f in set(backups.glob("*.sql")) - existing:
            f.unlink()


def test_reset_script_does_not_wipe_when_its_backup_will_not_restore(db):
    """A table the restore does not know about cannot be restored, so the reset must stop."""
    c = db.conn
    populate(c)
    before = fingerprint(c)
    # Sabotage: a trigger that rejects every audit_log insert makes any restore fail.
    c.execute(
        "create function no_audit() returns trigger language plpgsql as $$ begin "
        "raise exception 'sabotaged'; end $$"
    )
    c.execute("create trigger no_audit before insert on audit_log for each row execute function no_audit()")
    backups = SCRIPTS / ".backups"
    existing = set(backups.glob("*.sql")) if backups.exists() else set()
    try:
        code, printed = run_in_terminal(
            [str(RESET_SH), "--execute"], script_env(db),
            [(r"Type the host", lambda p: typed_host(p))],
        )
        assert code == 1
        assert "NOT wiping" in printed and "COMMITTED" not in printed
        assert fingerprint(c) == before
    finally:
        for f in set(backups.glob("*.sql")) - existing:
            f.unlink()
