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

`scripts/reset_trial_data.sh` wipes every clinic's patients, check-ins, messages, drafts,
touchpoints, consents, episodes and audit log, and keeps clinics, memberships, procedures,
message schedules and Supabase Auth users. A person runs it, on purpose (it refuses in CI and
without a terminal; there is no `--yes`). Dev and production share one database, so it wipes both.

```
DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres' \
  supabase/scripts/reset_trial_data.sh              # dry run: counts, changes nothing
  supabase/scripts/reset_trial_data.sh --execute    # backup, type the host, wipe
```

Use the direct connection as `postgres` (the service-role REST key can't do this). It writes a
data-only `pg_dump` to `scripts/.backups/` first (git-ignored). It uses one `TRUNCATE` over an
explicit table list: that gets past `message_drafts`' delete trigger and its foreign keys, and a
table missing from the list fails the statement instead of being wiped silently. Migration 9
needs the data gone first, so the order is: reset, `db push` / psql migration 9, deploy api + web,
re-seed with `apps/api`'s `seed_trial_patients` (see below). The scripts live outside `migrations/` and are not `seed.sql`, so nothing loads them.

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
disagree (e.g. you edited v1's offsets). `--include-alternate-schedule` adds a v2 schedule and a
v2 patient, **and makes v2 the schedule new dashboard patients get**; leave it off normally.
Check afterwards with `python -m clinically_anchored_api.core.reconcile --clinic <demo clinic id>`.
