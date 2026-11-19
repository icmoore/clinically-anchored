# Environments: production and staging

Two Supabase projects, and nothing shared between them.

| | Production | Staging |
|---|---|---|
| Supabase project | the original one (`ca-central-1`, ref `cdaswsjirkkdhvhegwco`) | a new one, same region |
| Holds | Sarah's real patients | synthetic data only: the demo and dev clinics |
| `app_settings.environment` | `production` | `staging` |
| Git branch | `main` | `develop` |
| Api | Railway service `clinically-anchored`, `https://api.clinicallyanchored.ai` (`APP_ENV=production`) | Railway service `clinically-anchored-staging`, `https://api-staging.clinicallyanchored.ai` (`APP_ENV=staging`) |
| Web | Vercel Production, `https://clinicallyanchored.ai` | Vercel Preview; `develop` is also served at `https://staging.clinicallyanchored.ai` |
| AI (Bedrock) | per the go-live decision | on, **synthetic data only** |
| Audit signing key | its own (Railway only) | its own, `staging-1` (Railway staging service and your `.env`) |

**Your local `.env` files always point at staging.** Production credentials live in Railway and
Vercel Production, and nowhere on your machine.

## Branches and releases

`develop` deploys to staging; `main` deploys to production. Feature and fix PRs target `develop`.
`main` takes only release PRs from `develop`, plus hotfixes (which are then merged back into
`develop`). Both branches run CI on PRs and pushes. The steps of a release, in order, are in
[release.md](release.md). Two scripts do the database and pre-release work by environment *name*:

- `scripts/db_migrate staging|production`: applies pending migrations to that project. The project
  ref and pooler host come from the fixed mapping in `scripts/lib/environments.sh` (staging is
  `qhwipkigthcfprscthcr`), never from an argument.
- `scripts/release_check`: read-only; run before merging a release PR.

## How production is protected

The database says which environment it is: one row in `app_settings` (migration 21), inserted by hand
in each project (below). Everything that could do damage checks it.

- **The api** refuses to start unless `APP_ENV` equals that row. `APP_ENV` unset, no row, or a
  mismatch each stop it with a message saying what to fix. `GET /health` reports the environment.
- **`reset_trial_data.sh/.sql`, `restore_trial_data.sh/.sql`** refuse on a production database,
  inside SQL and again in the shell wrapper.
- **The seed** (`seed_trial_patients`) writes only when `APP_ENV=staging` and the row says staging.
- **A demo clinic** (`clinics.is_demo`) can't be created in production (a trigger).

The scripts refuse only on a row that says `production`, so they still run on a project whose row
isn't set yet: the one-time reset in the cutover (step b) depends on that. The api and the seed are
stricter: they need a row.

To change a project's row you must be the table owner, in psql. That is on purpose: nothing the api
or the dashboard can do flips it.

## Running SQL

All SQL here runs in `psql` as the table owner, against the project the section names. Open one
terminal per project and label it.

```bash
export PATH=/opt/homebrew/opt/libpq/bin:$PATH      # psql and pg_dump, if they aren't on PATH
printf 'Database password: '; stty -echo; read -r PGPASSWORD; stty echo; echo; export PGPASSWORD
# Dashboard -> Connect -> "Session pooler": copy the exact user and host. Port 5432, never 6543.
export DATABASE_URL='postgresql://postgres.<project-ref>@aws-0-ca-central-1.pooler.supabase.com:5432/postgres'
```

The password is typed at the prompt, so it is never in the URL, your shell history or this repo. Then,
**before anything else**, ask the database which environment it is:

```bash
psql "$DATABASE_URL" -X -At -c "select coalesce((select environment from app_settings), 'NOT SET')"
```

Run a SQL block by pasting it into `psql "$DATABASE_URL" -X`. Each block starts with
`\set ON_ERROR_STOP on`, so it stops at the first error.

## Creating the staging project

1. **Create it.** Supabase dashboard -> New project, same region as production (`ca-central-1`),
   named `clinically-anchored-staging`. Put the database password in your password manager.
2. **Turn sign-ups off.** Authentication -> Sign In / Providers -> turn off **Allow new users to sign
   up** (the dashboard moves things around; the setting is that one). Clinicians are created by hand,
   and patients by the api, never by self-sign-up.
3. **Apply the migrations.** The staging project's ref is already in `scripts/lib/environments.sh`.
   Check that the pooler host there (`env_pooler_host`) is exactly the host the dashboard shows under
   Connect -> Session pooler (older projects use `aws-0-...`, newer ones `aws-1-...`). Then, from the
   repo root:

   ```bash
   scripts/db_migrate staging --dry-run    # shows the project and the migrations still to apply
   scripts/db_migrate staging              # asks for the password, then you type: staging
   ```

   It needs `psql` and the Supabase CLI (`brew install supabase/tap/supabase`). It pushes with
   `supabase db push --db-url`, so `supabase link` state never matters, and the password is typed at
   a hidden prompt. It refuses if the database already says it is a different environment. The
   alternative, with only `psql`, is the loop below with `DATABASE_URL` pointing at staging. It
   records each one in `supabase_migrations.schema_migrations`, skips ones already recorded, and
   stops at the first failure, so it is safe to re-run.

   ```bash
   psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -c "
     create schema if not exists supabase_migrations;
     create table if not exists supabase_migrations.schema_migrations
       (version text not null primary key, statements text[], name text);"
   for f in supabase/migrations/*.sql; do
     base="$(basename "$f" .sql)"; version="${base%%_*}"; name="${base#*_}"
     if [ -n "$(psql "$DATABASE_URL" -X -At -c "select 1 from supabase_migrations.schema_migrations where version = '$version'")" ]; then
       echo "skip   $base"; continue
     fi
     echo "apply  $base"
     psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -1 -f "$f" \
       -c "insert into supabase_migrations.schema_migrations (version, name) values ('$version', '$name')" || break
   done
   ```

4. **Mark it staging.**

   <!-- sql:staging_row -->
   ```sql
   \set ON_ERROR_STOP on
   insert into app_settings (environment) values ('staging');
   select environment, set_at from app_settings;
   ```

5. **Give it its own audit signing key.** Never reuse production's. From `apps/api`:

   ```bash
   uv run python -m clinically_anchored_api.core.audit
   # without uv:  python3 -m venv .venv && .venv/bin/pip install cryptography && PYTHONPATH=src .venv/bin/python -m clinically_anchored_api.core.audit
   ```

   It prints a base64 value: that is `AUDIT_SIGNING_KEY`. Use `AUDIT_KEY_ID=staging-1`.
6. **Put staging's keys in your local `.env` files** (Dashboard -> Project Settings -> API):

   `apps/api/.env`
   ```
   ENVIRONMENT=development
   APP_ENV=staging
   SUPABASE_URL=https://<staging-ref>.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=<staging secret / service_role key>
   SUPABASE_ANON_KEY=<staging anon / publishable key>
   AUDIT_SIGNING_KEY=<the value from step 5>
   AUDIT_KEY_ID=staging-1
   ```
   Leave `AUDIT_PUBLIC_KEYS` empty. `TEST_EMAIL` / `TEST_PASSWORD` are for a staging clinician (step 7).

   `apps/web/.env.local`
   ```
   NEXT_PUBLIC_API_URL=http://localhost:8000
   NEXT_PUBLIC_SUPABASE_URL=https://<staging-ref>.supabase.co
   NEXT_PUBLIC_SUPABASE_ANON_KEY=<staging anon / publishable key>
   ```

   Check that neither file mentions production: `grep -c cdaswsjirkkdhvhegwco apps/api/.env apps/web/.env.local`
   must print `0` for both. Restart your dev servers after editing; the api won't start until its
   `APP_ENV` matches the row.
7. **Make yourself a clinician on staging** (see [Adding a clinician](#adding-a-clinician)) and add
   yourself to the dev clinic as owner: use clinic id `00000000-0000-0000-0000-0000000000c1`.
8. **Seed the demo clinics.** A demo clinic must exist *as a demo clinic* before seeding, so the
   demo inbox works. Create it by SQL, then run both seeds from `apps/api`:

   <!-- sql:staging_demo_clinic -->
   ```sql
   \set ON_ERROR_STOP on
   insert into clinics (name, is_demo)
   select 'Major Mackenzie Surgery (demo)', true
   where not exists (select 1 from clinics where name = 'Major Mackenzie Surgery (demo)');
   select id, name, is_demo from clinics order by name;
   ```

   ```bash
   uv run python -m clinically_anchored_api.seed_trial_patients --dry-run                       # no database needed
   uv run python -m clinically_anchored_api.seed_trial_patients --email you@example.com          # the demo clinic
   uv run python -m clinically_anchored_api.seed_trial_patients --email you@example.com --clinic dev
   ```

   (`you@example.com` is your staging clinician's sign-in address.) The seed refuses unless
   `APP_ENV=staging` and the row says staging.
9. **Staging sends no real email.** Staging has its own Send Email hook (below) so Supabase's mailer
   never sends anything, but leave `SES_FROM_ADDRESS` unset on the staging api: a send is then
   recorded as failed (`ses_not_configured`) and nothing leaves. The dashboard's "invite user" and
   "reset password" buttons: don't use them on staging; create clinicians with Auto Confirm.

## Deploying staging

Once the staging Supabase project exists (above), give `develop` somewhere to run. You create all of
this by hand; none of it is in the repo.

### A. Railway: the staging api service

Create a **second service**, `clinically-anchored-staging`, in the same Railway project, from the same
GitHub repo, with **Root Directory** `/apps/api` (as production) and its deploy branch set to
**`develop`**. (`clinically-anchored` stays on `main`.) Variables:

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` (the api's strict mode: required secrets are checked; this is not the environment marker) |
| `APP_ENV` | `staging` |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY` | the **staging** project's. Never production's |
| `AUDIT_SIGNING_KEY`, `AUDIT_KEY_ID` | its own key (step 5 above), `staging-1`. See "Audit key" below |
| `ALLOWED_ORIGINS` | `https://staging.clinicallyanchored.ai` |
| `ALLOWED_ORIGIN_REGEX` | the Vercel preview pattern, below. **Staging only**: production refuses to start with it |
| `WEB_BASE_URL` | `https://staging.clinicallyanchored.ai` |
| `SEND_EMAIL_HOOK_SECRET` | the **staging** hook's secret (section B). Not production's |
| `TRUSTED_PROXY_HOPS` | `1` |
| `BEDROCK_MODEL_ID`, AWS credentials | AI on (below) |
| `SES_FROM_ADDRESS` | **leave unset**: no real email from staging |

Then Settings -> Networking -> Custom Domain: `api-staging.clinicallyanchored.ai` (Cloudflare, D).

**The preview pattern.** Vercel preview URLs for this project look like
`clinically-anchored-<hash>-<team-slug>.vercel.app` and `clinically-anchored-git-<branch>-<team-slug>.vercel.app`.
The regex is matched against the **whole** origin, so a look-alike host or a suffix does not match:

```
ALLOWED_ORIGIN_REGEX=https://clinically-anchored-[a-z0-9-]+-<team-slug>\.vercel\.app
```

Use your Vercel team slug (the one in your preview URLs, e.g. `acme` above). The exact list
(`ALLOWED_ORIGINS`) still applies as well. If the api refuses to start with a message about
`ALLOWED_ORIGIN_REGEX`, `APP_ENV` is not `staging`.

**AI on, synthetic data only.** Staging runs with the AI features on, so the drafting and summary
screens can be tested, but the Bedrock model runs through a cross-region inference profile (see
[api/README.md](api/README.md), decision 10), so **only synthetic data may ever be in the staging
database**: never copy production rows into it. Leave `AI_DAILY_CALL_CAP_PER_CLINIC` at its default
(200) or lower it; `0` turns AI off. Give the staging service Bedrock permission only (no SES).

**Audit key.** Staging has its own `AUDIT_SIGNING_KEY` with `AUDIT_KEY_ID=staging-1`, generated with
the command in step 5, never production's, so a production chain can never be signed by a key that sits
in a lower-security place. Your local `.env` may use the same staging key (the same database), or its
own key id: then put its public key in `AUDIT_PUBLIC_KEYS` on the Railway staging service so its rows
still verify.

### B. Supabase (staging project): the Send Email hook and URLs

1. Authentication -> Hooks -> **Send Email** -> HTTPS: URL `https://api-staging.clinicallyanchored.ai/auth-hooks/send-email`.
   Generate the secret here (`v1,whsec_...`) and put it in Railway staging as `SEND_EMAIL_HOOK_SECRET`.
   It must differ from production's: each project has its own.
2. Authentication -> URL Configuration: Site URL `https://staging.clinicallyanchored.ai`; redirect URLs
   `https://staging.clinicallyanchored.ai/**` (add `http://localhost:3000/**` for local work).
3. Sign-ups stay off (step 2 above).

### C. Vercel: Preview variables and the staging domain

Project -> Settings -> Environment Variables. **Untick Production** on these (Preview only), so a
preview build can never carry a production value:

| Variable | Preview |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api-staging.clinicallyanchored.ai` |
| `NEXT_PUBLIC_SUPABASE_URL` | the staging project's URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | the staging project's anon (publishable) key |

Settings -> Domains -> add `staging.clinicallyanchored.ai` and assign it to the Git branch
**`develop`**. Production Branch stays `main`. `NEXT_PUBLIC_*` values are baked in at build time:
redeploy after changing them.

### D. Cloudflare DNS

Both records are **DNS only** (grey cloud), as for production ([domain-switch.md](domain-switch.md)):

| Name | Type | Value |
|---|---|---|
| `api-staging` | `CNAME` | the target Railway shows for the custom domain (plus the `TXT` it asks for, if any) |
| `staging` | `CNAME` | the target Vercel shows for the domain (a `…vercel-dns-….com` name) |

### E. Check it

```bash
curl -s https://api-staging.clinicallyanchored.ai/health   # {"status":"ok","environment":"staging"}
```

Then open `https://staging.clinicallyanchored.ai`, sign in as the staging clinician, and open one
Vercel preview URL: its calls to the staging api must not fail CORS. `scripts/release_check` checks
`/health` on both apis every time.

## Where each variable lives

**Railway staging api** (`clinically-anchored-staging`): the table in
[Deploying staging](#deploying-staging).

**Railway (production api).** `ENVIRONMENT=production` and `APP_ENV=production` (the pair: a
production `APP_ENV` is refused alongside `ENVIRONMENT=development`), `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY` (the secret key), `SUPABASE_ANON_KEY`, `AUDIT_SIGNING_KEY`,
`AUDIT_KEY_ID`, `ALLOWED_ORIGINS`, `WEB_BASE_URL`, `SES_FROM_ADDRESS`, `SEND_EMAIL_HOOK_SECRET`,
`TRUSTED_PROXY_HOPS=1`, AWS credentials. Details: [api/email.md](api/email.md),
[domain-switch.md](domain-switch.md).

**Vercel** (project -> Settings -> Environment Variables). Each variable has three checkboxes:
Production, Preview, Development.

| Variable | Production | Preview | Development |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api.clinicallyanchored.ai` | `https://api-staging.clinicallyanchored.ai` | unset |
| `NEXT_PUBLIC_SUPABASE_URL` | production project | staging project | unset |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | production anon key | staging anon key | unset |

Each value is ticked for **one** environment only: the production values for Production alone, the
staging values for Preview alone. A Preview deployment must never carry a production value: it is
built from any branch. Its address reaches the staging api only through `ALLOWED_ORIGIN_REGEX`
(Railway staging), never the production api.
`NEXT_PUBLIC_*` values are baked in at build time: redeploy after changing them.

**Your machine:** staging only (step 6 above).

## Production cutover

The current project becomes production. Do the steps **in this order**, each in one sitting. Nobody
real uses the app yet, so a short outage is fine.

**Before you start**
- The migrations through 21 are applied to this project (`supabase/migrations/`).
- Its `app_settings` row is `staging`, or there is none yet, and Railway has `APP_ENV=staging` (the
  api won't start without a matching row). Step f changes both to production. Don't insert
  `production` earlier: the reset in step b refuses on a production database.
- Staging exists and works (above), and you have signed in to it locally as a clinician.
- Have your clinician's email handy: it must survive steps c and d.

### a. Move your local `.env` files to staging

Done in [Creating the staging project](#creating-the-staging-project), step 6. Check both files:

```bash
grep -c cdaswsjirkkdhvhegwco apps/api/.env apps/web/.env.local     # both must print 0
```

Restart the dev servers, and confirm `curl -s localhost:8000/health` says `"environment":"staging"`.
From here on, nothing on your machine can reach production data except psql, which you point
there on purpose.

### b. Reset: dry run, then execute

Point `DATABASE_URL` at the **current (soon production) project**: user `postgres.cdaswsjirkkdhvhegwco`.
First look at what is there, and who:

<!-- sql:cutover_look -->
```sql
\set ON_ERROR_STOP on
select (select count(*) from patients) as patients,
       (select count(*) from audit_log) as audit_rows,
       (select count(*) from auth.users) as auth_users;
select id, name, is_demo from clinics order by name;
select u.email,
       coalesce(u.raw_app_meta_data->>'account_type', '-') as account_type,
       exists (select 1 from clinic_members m where m.user_id = u.id) as clinician
from auth.users u order by clinician desc, u.email;
```

Then the script. The banner must name the project you mean (`User: postgres.cdaswsjirkkdhvhegwco`);
it says `Environment: staging` or `not set` here, which is expected before step f.

```bash
supabase/scripts/reset_trial_data.sh              # dry run: counts, changes nothing
supabase/scripts/reset_trial_data.sh --execute    # dry run again, backup, proves it restores, you type the host, wipes
```

This wipes patients and everything under them **and `audit_log`**, so production starts a fresh
audit chain. The backup lands in `supabase/scripts/.backups/` (git-ignored): keep it somewhere safe
and off the repo, it is synthetic data plus the old chain.

### c. Remove the demo clinics

Production has none, and the trigger refuses to make one. Deleting a clinic cascades to its
members and procedures; it needs the reset to have emptied its patients and log first.

<!-- sql:cutover_demo_clinics -->
```sql
\set ON_ERROR_STOP on
begin;
select id, name from clinics where is_demo;     -- exactly the demo clinics, nothing else
do $$ begin
  if exists (select 1 from patients p join clinics c on c.id = p.clinic_id where c.is_demo) then
    raise exception 'A demo clinic still has patients. Run the reset (step b) first.';
  end if;
end $$;
delete from clinics where is_demo;
select count(*) as demo_clinics_left from clinics where is_demo;   -- 0
commit;
```

### d. Delete the synthetic patients' sign-in users

The reset keeps `auth.users` (an invited patient's login survives). Patient logins are marked in Auth
(`account_type = patient`); clinicians are never deleted, whatever they are marked. First look:

<!-- sql:cutover_patient_users_look -->
```sql
\set ON_ERROR_STOP on
-- These will be deleted:
select u.id, u.email from auth.users u
where u.raw_app_meta_data->>'account_type' = 'patient'
  and not exists (select 1 from clinic_members m where m.user_id = u.id)
order by u.email;
-- These stay (clinicians):
select u.id, u.email from auth.users u
where exists (select 1 from clinic_members m where m.user_id = u.id) order by u.email;
-- Anyone in neither list needs a decision from you; there should be nobody:
select u.id, u.email from auth.users u
where u.raw_app_meta_data->>'account_type' is distinct from 'patient'
  and not exists (select 1 from clinic_members m where m.user_id = u.id);
```

If the first list is the synthetic patients and the second is your clinicians, delete:

<!-- sql:cutover_patient_users_delete -->
```sql
\set ON_ERROR_STOP on
begin;
delete from auth.users u
where u.raw_app_meta_data->>'account_type' = 'patient'
  and not exists (select 1 from clinic_members m where m.user_id = u.id);
select u.email, u.raw_app_meta_data->>'account_type' as account_type from auth.users u order by 1;   -- clinicians only
commit;
```

### e. Rename the clinic

Take "(dev)" out of the clinic's name. Use the clinic's real name; this one is the practice's name as
seeded. Check the office phone too: the first sign-in message shows it to patients.

<!-- sql:cutover_rename -->
```sql
\set ON_ERROR_STOP on
update clinics set name = 'Major Mackenzie Surgery'
where id = '00000000-0000-0000-0000-0000000000c1' and name like '%(dev)';          -- UPDATE 1
-- update clinics set office_phone = '<the office number>' where id = '00000000-0000-0000-0000-0000000000c1';
select id, name, office_phone, is_demo from clinics;
```

### f. Mark the project production, and tell Railway

In this order. First the row (the running api keeps going: it only checks at startup):

<!-- sql:cutover_production_row -->
```sql
\set ON_ERROR_STOP on
insert into app_settings (environment) values ('production')
on conflict (singleton) do update set environment = 'production', set_at = now();
select environment, set_at from app_settings;
```

Then Railway (api service -> Variables): set `APP_ENV=production`, check `ENVIRONMENT=production`.
Railway redeploys. Check:

```bash
curl -s https://api.clinicallyanchored.ai/health     # {"status":"ok","environment":"production"}
```

From now on the reset, restore and seed scripts and demo clinics are refused here, by the database.
If the api fails to start after this, its log says which of the two disagrees.

### g. Rotate the Supabase secret key, and update Railway only

The current key has been in local `.env` files and in this project's history of dev work; it must not
survive into production. Your machine no longer needs one for this project, so only Railway gets the
new key.

1. Dashboard -> Project Settings -> API Keys. Create a **new secret key** (`sb_secret_...`) and copy
   it. (If the project still uses the legacy `service_role` JWT, check on staging first that the api
   works with a new-style key in your local `.env`. Rotating the legacy JWT secret instead changes
   the anon key too, signs everyone out, and needs Vercel's anon key updated and a redeploy.)
2. Railway: set `SUPABASE_SERVICE_ROLE_KEY` to it. Wait for the deploy, then `curl -s
   https://api.clinicallyanchored.ai/health` and load the dashboard once.
3. Only then **delete or disable the old key** in the dashboard.
4. Keep the new key in your password manager only. It is not in any local file.

### h. Upgrade to Pro and confirm daily backups

The free plan has no backups. Dashboard -> Organization -> Billing -> upgrade the organization to
Pro, then Project -> Database -> Backups. Confirm **Scheduled backups** lists a daily backup (the
first appears within a day). Point-in-time recovery is a separate paid add-on: decide whether Sarah's
data warrants it before real patients, and note the decision here.

### i. Smoke test with one throwaway patient

At `https://clinicallyanchored.ai`, sign in as a clinician, and add a patient named exactly
**`TEST – do not treat`** with a surgery date and procedure. Check the dashboard shows it. Don't invite
it, don't send messages, and don't use AI on it (AI is for synthetic data only until the Bedrock
question is settled). Optionally check the audit chain and reconcile, read-only, with Railway's
variables:

```bash
cd apps/api && railway run uv run python -m clinically_anchored_api.core.reconcile --clinic 00000000-0000-0000-0000-0000000000c1
```

Then remove the patient (and afterwards run the [go-live check](#go-live-check)). Episodes, summaries, sent-email records, sign-in links, drafts and
acceptances are **retention-protected**: the database refuses to delete them (`episodes_retained`,
`..._append_only`). That is right for real patients. For this one throwaway record, the table owner
can switch those guards off *inside one transaction*, which also holds an exclusive lock on those
tables so nothing else can write meanwhile, and the block refuses unless the patient's name starts
with `TEST`. If anything fails, everything rolls back, guards included.

Find the patient, put its id in the `\set` line of the next block, and run it:

<!-- sql:cutover_find_test_patient -->
```sql
\set ON_ERROR_STOP on
select id, full_name, created_at from patients where full_name like 'TEST%';   -- exactly one row
```

<!-- sql:cutover_remove_test_patient -->
```sql
\set ON_ERROR_STOP on
\set pid '<the id from the query above>'
begin;
select set_config('ca.pid', :'pid', true) \gset
do $$ begin
  if not exists (select 1 from patients where id = current_setting('ca.pid')::uuid and full_name like 'TEST%') then
    raise exception 'That id is not a patient whose name starts with TEST. Nothing was changed.';
  end if;
end $$;

-- The retention guards on this patient's records, off until the end of this transaction.
create temp table guards (tbl text, trg text) on commit drop;
insert into guards values
  ('episodes', 'episodes_guard'), ('patient_sign_in_links', 'patient_sign_in_links_guard'),
  ('care_provider_summary_edits', 'care_provider_summary_edits_guard'),
  ('care_provider_summaries', 'care_provider_summaries_guard'), ('patient_briefs', 'patient_briefs_guard'),
  ('thread_summaries', 'thread_summaries_guard'), ('extension_requests', 'extension_requests_guard'),
  ('email_outbox', 'email_outbox_guard'), ('message_drafts', 'message_drafts_guard'),
  ('patient_acceptances', 'patient_acceptances_append_only');
do $$ declare g record; begin
  for g in select * from guards loop execute format('alter table %I disable trigger %I', g.tbl, g.trg); end loop;
end $$;

create temp table gone_users on commit drop as
  select user_id from patient_accounts where patient_id = :'pid';
delete from patient_sign_in_links where account_id in (select id from patient_accounts where patient_id = :'pid');
delete from care_provider_summary_edits where patient_id = :'pid';
delete from care_provider_summaries where patient_id = :'pid';
delete from patient_briefs where patient_id = :'pid';
delete from thread_summaries where patient_id = :'pid';
delete from extension_requests where patient_id = :'pid';
delete from email_outbox where patient_id = :'pid';
delete from patient_acceptances where patient_id = :'pid';
delete from message_drafts where patient_id = :'pid';
delete from patient_accounts where patient_id = :'pid';
delete from consents where patient_id = :'pid';
delete from touchpoints where patient_id = :'pid';
delete from messages where patient_id = :'pid';
delete from check_ins where patient_id = :'pid';
delete from episodes where patient_id = :'pid';
delete from patients where id = :'pid';                               -- DELETE 1
delete from auth.users where id in (select user_id from gone_users);  -- only if you invited it

set constraints all immediate;     -- run the deferred foreign-key checks now, before the guards go back on
do $$ declare g record; begin
  for g in select * from guards loop execute format('alter table %I enable trigger %I', g.tbl, g.trg); end loop;
end $$;
commit;
```

The patient's audit events stay: the log is append-only, and reconcile lists them as "dangling
events", which is informational. Production now holds Sarah's clinic and no patients.

## Go-live check

`scripts/golive_check` prints PASS or FAIL for each thing that must be true before real patients use
an environment, against whichever environment it is pointed at. It is **read-only**: it only selects
from the database and reads Supabase Auth's public settings, and it sends no email. The top line says
which Supabase project and `APP_ENV` it checked; run it on staging any time, and on production after
step i and before Sarah's first patient.

| Check | Passes when |
|---|---|
| Environment row | `app_settings` has a row (`production` or `staging`) |
| APP_ENV matches | `APP_ENV` equals that row |
| No demo clinics | none exist (production; on staging they are expected) |
| Clinic name has no "(dev)" | no clinic is named so (production; on staging the dev clinic is) |
| Patient count | it can read them; the number is shown |
| Legal documents | `scripts/check_legal_docs` finds no `[placeholders]` |
| Bedrock model | `BEDROCK_MODEL_ID` is set; shown with the AI call cap |
| SES_FROM_ADDRESS | set, valid and on the web app's domain (production; staging may leave it unset) |
| Sign-ups off | Supabase Auth reports `disable_signup` |

Locally it reads `apps/api/.env`, i.e. staging:

```bash
scripts/golive_check
```

To check **production**, don't put production keys in a file: let Railway supply them. `--no-env-file`
ignores `apps/api/.env`, so a local value can never hide a variable that is missing in Railway.

```bash
cd apps/api && railway run ../../scripts/golive_check --no-env-file
```

Exit status is 0 when everything passes, 1 when anything fails, 2 when it can't connect. A failed
legal-documents line is expected until the terms and privacy text is final; the placeholders are listed.

## Adding a clinician

1. Check you are in the right project: run the `app_settings` query from
   [Running SQL](#running-sql).
2. Supabase dashboard -> Authentication -> Users -> **Add user** -> **Create new user**. Enter the
   clinician's email and a long temporary password, and tick **Auto Confirm User**. Give them the
   password through a different channel than the email. They change it themselves from the account
   menu (Account -> Change password).
3. Give them a clinic and a role (`owner`, `clinician` or `delegate`):

   <!-- sql:add_clinician -->
   ```sql
   \set ON_ERROR_STOP on
   insert into clinic_members (clinic_id, user_id, role)
   select c.id, u.id, 'clinician'
   from clinics c, auth.users u
   where c.id = '00000000-0000-0000-0000-0000000000c1'   -- the clinic
     and u.email = 'doctor@example.org'                  -- the person you just created
   on conflict do nothing;
   select c.name, u.email, m.role from clinic_members m
   join clinics c on c.id = m.clinic_id join auth.users u on u.id = m.user_id order by 1, 2;
   ```

   `INSERT 0 1` means it worked; `INSERT 0 0` means the clinic id or the email matched nothing.
4. They sign in at `/login` with email and password. Patients are never created this way: a patient's
   login is made by the api when you invite them.

## Rules of thumb

- Anything that wipes or bulk-creates runs on **staging**. Production is changed by the app, and by
  the SQL in this file.
- Check the environment (the `select ... app_settings` line) before running SQL in a terminal you
  haven't looked at for a while.
- A new migration goes onto **staging first** (`scripts/db_migrate staging`), then production
  (`scripts/db_migrate production`), before the code that needs it is deployed: [release.md](release.md).
- **Changing a project's environment marker: the database row first, then `APP_ENV`.** The api only
  checks the row when it starts, so the order that keeps it up is: update the row (in psql, as the table
  owner), then change `APP_ENV` on that Railway service (which redeploys it). The other order restarts
  the api against a row that still disagrees, and it refuses to start (the outage of 2026-11-16).
- Never paste a production key into a local file, a chat or a ticket.
