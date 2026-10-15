#!/usr/bin/env bash
# Wipe all patient-side trial data in every clinic (see reset_trial_data.sql for the lists).
#
#   DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres' \
#     supabase/scripts/reset_trial_data.sh              # dry run: shows counts, changes nothing
#   ... reset_trial_data.sh --execute                    # backs up, asks you to type the host, wipes
#
# DATABASE_URL must be a direct connection as the table owner (postgres). The service-role
# REST key cannot do this. This script is run by a person, at a terminal, on purpose:
#   * it refuses to run when CI or GITHUB_ACTIONS is set;
#   * it refuses to wipe without an interactive terminal (there is no --yes);
#   * --execute needs you to type the database host as shown;
#   * it saves a data-only pg_dump of the tables first (the free Supabase plan has no
#     backups, and the audit log is evidence even when the data is synthetic), then PROVES the
#     backup restores (loads it back inside a transaction that is rolled back) and stops
#     before wiping if it does not.
# Dev and production share one database here: a wipe is a wipe of both.
# To put a backup back, see restore_trial_data.sh.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sql="$here/reset_trial_data.sql"
restore_sql="$here/restore_trial_data.sql"
# shellcheck source=backup_common.sh
source "$here/backup_common.sh"
execute=false

for arg in "$@"; do
  case "$arg" in
    --execute) execute=true ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ -n "${CI:-}" || -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "Refusing to run: this script must never run in CI." >&2
  exit 1
fi
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "Set DATABASE_URL to a direct Postgres connection string (see the header of this file)." >&2
  exit 2
fi
for tool in psql pg_dump; do
  command -v "$tool" >/dev/null || { echo "$tool not found on PATH." >&2; exit 2; }
done

# What we are about to touch: host and database, never the password.
target="$(psql "$DATABASE_URL" -X -At -c "select coalesce(inet_server_addr()::text, 'local-socket') || ' ' || current_database()")"
host="$(printf '%s' "$DATABASE_URL" | sed -E 's#^[a-z]+://([^@/]*@)?([^:/?]+).*#\2#')"
echo "Target: host '$host' ($target)"
echo "Dev and production share this database."
echo

if [[ "$execute" != true ]]; then
  psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -v execute=false -f "$sql"
  exit 0
fi

if [[ ! -t 0 || ! -t 1 ]]; then
  echo "Refusing to wipe without an interactive terminal." >&2
  exit 1
fi

echo "Dry run first:"
psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -v execute=false -f "$sql"
echo
read -r -p "Type the host ('$host') to wipe the data above: " typed
if [[ "$typed" != "$host" ]]; then
  echo "Does not match. Nothing changed." >&2
  exit 1
fi

backup_dir="$here/.backups"
mkdir -p "$backup_dir"
backup="$backup_dir/reset-$(date -u +%Y%m%dT%H%M%SZ).sql"
take_backup "$DATABASE_URL" "$backup"
echo "Backup written: $backup"

# A backup nobody has restored is a hope. Load it back into the live tables inside a transaction
# that always rolls back, and compare every table before and after. Nothing is wiped until this passes.
prepared="$(mktemp "${TMPDIR:-/tmp}/reset-backup.XXXXXX")"
trap 'rm -f "$prepared"' EXIT
prepare_backup "$backup" "$prepared"
if ! psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -v mode=verify -v "tables=$(backup_tables_csv)" \
     -v "backup=$prepared" -f "$restore_sql"; then
  echo "The backup does not restore cleanly. NOT wiping. Fix that first (backup kept at $backup)." >&2
  exit 1
fi
echo

psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -v execute=true -f "$sql"
echo
echo "Next: apply any pending migrations, deploy api + web, then re-seed."
echo "To undo this reset: supabase/scripts/restore_trial_data.sh $backup"
