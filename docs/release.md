# Releasing to production

`develop` deploys to staging, `main` deploys to production, and a release is a pull request from
`develop` to `main`. Environments and how they are set up: [environments.md](environments.md).

```
feature branch --PR--> develop --(auto deploy)--> staging
                          |
                          +--release PR--> main --(auto deploy)--> production
```

- Feature and fix PRs target `develop`.
- `main` takes only release PRs from `develop`, plus **hotfixes**. A hotfix branches from `main`,
  goes to `main` by PR, and is **merged back into `develop` straight after** so the two don't drift.
- Both branches run CI on PRs and on pushes.

## Steps, in order

**a. CI is green on `develop`, and it has been tested on staging.** Open
`https://staging.clinicallyanchored.ai` (or a preview URL) and use what changed, as a clinician and,
where it matters, as a patient. Staging holds synthetic data only.

**b. Apply any new migrations to production.** From an up-to-date `main`:

```bash
git checkout main && git pull --ff-only
scripts/db_migrate production --dry-run     # shows the project and the pending migrations
scripts/db_migrate production               # password at the hidden prompt, then type: production
```

The script refuses unless you are on `main` and level with `origin/main`, and it shows the project it
will touch before it asks you to type `production`. A migration must already have gone onto staging
(`scripts/db_migrate staging`) and run there with the new code. Migrations must be **backward
compatible** with the code currently in production (add a column or table; don't drop or rename in
the same release), because the database changes before the code does.

**c. `scripts/release_check` passes.** Read-only. It checks that every migration in `main` is recorded in
production (a gap fails), that both `/health` endpoints report their own environment, that
`golive_check` passes against production, and lists the commits in `develop` that are not in `main`.

```bash
scripts/release_check
```

Not yet passing is a stop: fix the cause; don't merge. (`golive_check` fails today on the legal
`[placeholders]` until the Terms and Privacy text is final: that is a real FAIL, not noise.)

**d. Merge the release PR `develop` -> `main`.** Use a **merge commit** (not squash), so `develop` and
`main` keep sharing history and the next release PR doesn't list old commits again.

**e. Railway and Vercel deploy production.** Railway (`clinically-anchored`) and Vercel Production build
from `main`. Watch both finish. If the api fails to start, its log says which of `APP_ENV` and the
database row disagrees.

**f. Check production.**

```bash
curl -s https://api.clinicallyanchored.ai/health   # {"status":"ok","environment":"production"}
(cd apps/api && railway run --service clinically-anchored ../../scripts/golive_check --no-env-file)
```

Then open `https://clinicallyanchored.ai` and sign in.

## Releases that add migrations

Step b needs the migration files on `main`, but they are on `develop` until the release PR merges, and
merging deploys the code at once. So that the database changes **before** the code, land the
migration files on `main` first as their own small PR:

1. Branch from `main`; take only the new files from `develop`
   (`git checkout develop -- supabase/migrations/<file>.sql`); open a PR to `main`, titled
   "Migrations for release X". Merge it. (This deploys unchanged code.)
2. Run step b from the updated `main`.
3. Continue with steps c to f. The release PR's diff then shows those files as already identical on
   `main`, so nothing conflicts.

`release_check` prints a **WARN** when `develop` has migrations that are in neither `main` nor
production, to catch a release that would deploy code ahead of its schema.

## Changing an environment marker: the row first, then `APP_ENV`

Each Supabase project records which environment it is (`app_settings`, migration 21) and the api
refuses to start unless `APP_ENV` equals that row. For **any** change to the marker, in this order:

1. Update the **database row** (psql as the table owner: the SQL is in environments.md, step f of the
   cutover). The running api carries on: it checks only at startup.
2. Then change **`APP_ENV`** on that Railway service. Railway redeploys; the new instance finds the
   row already matching.

The other way round (the 2026-11-16 outage) restarts the api while the row still disagrees, so it
refuses to start. When adding a brand-new environment, insert its row before the first deploy.

## Hotfix

1. Branch from `main`, fix, PR to `main`, merge.
2. Steps b, c (if it has a migration), e, f as above.
3. **Merge `main` back into `develop`** (PR `main` -> `develop`) straight away.
