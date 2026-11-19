<!--
Where this PR goes:
- Feature and fix PRs target `develop` (deploys to staging).
- `main` (production) only takes release PRs from `develop`, and hotfixes. A hotfix is merged back
  into `develop` straight after. Release steps: docs/release.md.
-->

## What changed

## How it was checked

<!-- Tests that ran (and any that CI cannot run), and what was looked at by hand: which pages, which widths. -->

## Migration

<!-- "None", or the migration file. New migrations are applied by Ian: to staging first with
`scripts/db_migrate staging`, to production before the release PR is merged (docs/release.md). -->
