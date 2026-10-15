#!/usr/bin/env bash
# Put a backup taken by reset_trial_data.sh back into the database.
#
#   DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres' \
#     supabase/scripts/restore_trial_data.sh supabase/scripts/.backups/reset-<stamp>.sql
#   ... restore_trial_data.sh <backup> --execute        # types-the-host, then commits
#
# Without --execute it loads the backup and rolls back, so you see it work and see the row counts
# without changing anything. The tables must be empty (restore into a database that was just reset);
# a non-empty table stops it. Same rails as the reset: never in CI, --execute needs a terminal and
# the host typed. After a restore, the audit log's signature chain should still verify.
# Why a backup needs preparing before it loads: see backup_common.sh.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
restore_sql="$here/restore_trial_data.sql"
# shellcheck source=backup_common.sh
source "$here/backup_common.sh"
execute=false
backup=""

for arg in "$@"; do
  case "$arg" in
    --execute) execute=true ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    -*) echo "Unknown argument: $arg" >&2; exit 2 ;;
    *) [[ -z "$backup" ]] || { echo "Only one backup file." >&2; exit 2; }; backup="$arg" ;;
  esac
done

if [[ -n "${CI:-}" || -n "${GITHUB_ACTIONS:-}" ]]; then
  echo "Refusing to run: this script must never run in CI." >&2
  exit 1
fi
[[ -n "$backup" && -f "$backup" ]] || { echo "Give the path of a backup file (supabase/scripts/.backups/reset-*.sql)." >&2; exit 2; }
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "Set DATABASE_URL to a direct Postgres connection string (see the header of reset_trial_data.sh)." >&2
  exit 2
fi
command -v psql >/dev/null || { echo "psql not found on PATH." >&2; exit 2; }

host="$(printf '%s' "$DATABASE_URL" | sed -E 's#^[a-z]+://([^@/]*@)?([^:/?]+).*#\2#')"
echo "Target: host '$host'. Backup: $backup"
echo "Dev and production share this database."
echo

prepared="$(mktemp "${TMPDIR:-/tmp}/restore-backup.XXXXXX")"
trap 'rm -f "$prepared"' EXIT
prepare_backup "$backup" "$prepared"
run() {
  psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -v "mode=$1" -v "tables=$(backup_tables_csv)" \
    -v "backup=$prepared" -f "$restore_sql"
}

echo "Trial load (rolled back):"
run check
if [[ "$execute" != true ]]; then
  echo "Re-run with --execute to restore for real."
  exit 0
fi

if [[ ! -t 0 || ! -t 1 ]]; then
  echo "Refusing to restore without an interactive terminal." >&2
  exit 1
fi
echo
read -r -p "Type the host ('$host') to restore the data above: " typed
if [[ "$typed" != "$host" ]]; then
  echo "Does not match. Nothing changed." >&2
  exit 1
fi
run commit
