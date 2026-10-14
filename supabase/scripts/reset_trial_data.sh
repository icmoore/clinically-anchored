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
#     backups, and the audit log is evidence even when the data is synthetic).
# Dev and production share one database here: a wipe is a wipe of both.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sql="$here/reset_trial_data.sql"
execute=false

for arg in "$@"; do
  case "$arg" in
    --execute) execute=true ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
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
tables=()
for t in message_drafts thread_summaries touchpoints consents messages check_ins episodes patients audit_log; do
  exists="$(psql "$DATABASE_URL" -X -At -c "select to_regclass('public.$t') is not null")"
  [[ "$exists" == t ]] && tables+=(-t "public.$t")
done
pg_dump "$DATABASE_URL" --data-only --no-owner --no-privileges "${tables[@]}" -f "$backup"
chmod 600 "$backup"
echo "Backup written: $backup"

psql "$DATABASE_URL" -X -v ON_ERROR_STOP=1 -v execute=true -f "$sql"
echo
echo "Next: apply migration 9 (if not applied), deploy api + web, then re-seed."
