# supabase/

Schema and migrations for the Supabase project (Postgres, `ca-central-1`). Managed with the
Supabase CLI; migrations here are the source of truth, not changes made in the dashboard.

## Setup (once you have a Supabase project)

```
npx supabase login
npx supabase link --project-ref <your-project-ref>
npx supabase db push
```

## Adding a migration

```
npx supabase migration new <name>
```

## Design notes

- Tenant isolation is enforced by row-level security (`clinic_id` + `clinic_members`),
  not by application-layer filtering -- see `migrations/00000000000001_init_schema.sql`.
- Patients are not Supabase Auth users. Patient-facing writes (check-ins, replies) go
  through `apps/api` using the service role key, which validates the patient's signed
  link token itself before writing.
- `audit_log` is append-only at the database level (`UPDATE`/`DELETE` revoked for
  `authenticated`/`anon`), matching data-catalogue D16/D17.
- Only synthetic data until the privacy review is done -- see the project's
  data-catalogue.md. Nothing here should be pointed at a real patient yet.

## Resetting trial data

`scripts/reset_trial_data.sh` wipes every clinic's patients, check-ins, messages, drafts, saved AI
summaries, touchpoints, consents, sent-email records (`email_outbox`), episodes and audit log, and keeps clinics, memberships, procedures,
message schedules and Supabase Auth users. A person runs it, on purpose (it refuses in CI and
without a terminal; there is no `--yes`). Dev and production share one database, so it wipes both.

```
DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres' \
  supabase/scripts/reset_trial_data.sh              # dry run: counts, changes nothing
  supabase/scripts/reset_trial_data.sh --execute    # backup, type the host, wipe
```

Use the direct connection as `postgres` (the service-role REST key can't do this). It writes a
data-only `pg_dump` to `scripts/.backups/` first (git-ignored), then **proves the backup restores**
(loads it back inside a transaction that always rolls back and compares every table before and
after) and stops without wiping if it does not. It uses one `TRUNCATE` over an
explicit table list: that gets past `message_drafts`' delete trigger and its foreign keys, and a
table missing from the list fails the statement instead of being wiped silently. Migration 9
needs the data gone first, so the order is: reset, `db push` / psql migration 9, deploy api + web,
re-seed with `apps/api`'s `seed_trial_patients` (see below). The scripts live outside `migrations/` and are not `seed.sql`, so nothing loads them.

### Restoring a backup

Put a backup back into a database that was just reset (its tables must be empty; a table that
holds rows stops it). Same rails as the reset: never in CI, `--execute` needs a terminal and the
host typed.

```
DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres' \
  supabase/scripts/restore_trial_data.sh supabase/scripts/.backups/reset-<stamp>.sql            # trial load, rolled back
  supabase/scripts/restore_trial_data.sh supabase/scripts/.backups/reset-<stamp>.sql --execute  # for real
```

Apply any pending migrations first (the backup is data only, so the schema must already match:
the tables and columns it names must exist). Then check the audit log still verifies for each
clinic. Do not restore the file with a bare `psql -f`: a plain `pg_dump` data file does not load
back into this schema without help (an empty `search_path` breaks the reply guard on `messages`
before migration 12, a reply can come before the message it answers, and `SET transaction_timeout`
fails on servers older than 17). `restore_trial_data.sh` prepares the file for exactly those three
things (see `scripts/backup_common.sh`). It does not use `pg_dump --disable-triggers`, which needs
a superuser; nothing here needs more than the privileges the reset already uses. pg_dump prints a
warning about circular foreign keys on `messages` when taking the backup: that is the reply link,
and the restore handles it.

## Database tests

`apps/api/tests/db/` runs the migrations and the reset script against a real Postgres. They are
skipped unless `TEST_DATABASE_URL` points at a server on this machine (the fixtures refuse any
other host, since they create and drop databases) and are not in CI yet. See `apps/api/README.md`.

## Seeding the demo clinic

After the reset and migration 9, from `apps/api` with the api's `.env`:

```
uv run python -m clinically_anchored_api.seed_trial_patients --dry-run
uv run python -m clinically_anchored_api.seed_trial_patients --email you@example.com
```

It writes 13 synthetic patients into a separate demo clinic (created if missing), one per state
the schedule engine can report (locked, waiting, open, used, discharged, ran out, a reply open,
a late check-in, red flags), with the same audit events the api writes. It checks its plan
against the engine and the schedules in the database first and refuses to write if they
disagree (e.g. someone edited a schedule's offsets). All 13 patients are on schedule v2 (migration
11); `--include-v1-patient` adds a 14th on the old v1, to show that an episode keeps the version it
recorded. The seed never writes to `message_schedules` and never changes which schedule new
dashboard patients get (the newest active version).
Check afterwards with `python -m clinically_anchored_api.core.reconcile --clinic <demo clinic id>`.

`--clinic dev` fills the shared dev clinic instead (the same 13 patients, so each account has its own
copy). The dev clinic must already exist and the account must already be a member of it; the seed
never adds a membership to it. Run it with the account that belongs to that clinic.

**Patient accounts** (migration 18). The seed also gives every patient an email at a reserved test
domain (`trial-patient-N.demo@example.com`, `...dev@...`: no real mail can reach it) and puts some in
each account state: patients 1-3 active, 11 invited (its invite is in the Demo inbox), 12 locked by the
surgery-date check, 10 with access turned off; everyone else is not invited. A database seeded before
accounts existed gets its emails and accounts on a re-run (existing patients are skipped, never
duplicated). To test real email through SES, add one more synthetic patient under YOUR address, with
an active account, in the dev clinic:

```
uv run python -m clinically_anchored_api.seed_trial_patients --email you@example.com --clinic dev --active-email you+patient@gmail.com
```

Use an address that is not a clinician's (a plus-address is a different sign-in); the seed refuses one
that belongs to staff or to another patient's live account, before it writes anything. Then open
`/sign-in` on the web app, enter that address, and the code arrives by email.
