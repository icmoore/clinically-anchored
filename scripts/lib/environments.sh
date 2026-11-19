# The fixed mapping from environment name to what it is. Sourced by scripts/db_migrate and
# scripts/release_check; never run on its own. This file is the ONLY place a project ref lives in
# the repo: a script never takes a ref (or a URL) from an argument or from your shell.
#
# Staging project ref: fill in once the staging Supabase project exists (Dashboard -> Project
# Settings -> General -> Reference ID). While it still says REPLACE_*, `db_migrate staging` refuses.

POOLER_HOST="aws-0-ca-central-1.pooler.supabase.com"

env_known() { case "$1" in production|staging) return 0 ;; *) return 1 ;; esac; }

env_project_ref() {
  case "$1" in
    production) echo "cdaswsjirkkdhvhegwco" ;;
    staging) echo "REPLACE_WITH_STAGING_PROJECT_REF" ;;
  esac
}

env_api_url() {
  case "$1" in
    production) echo "https://api.clinicallyanchored.ai" ;;
    staging) echo "https://api-staging.clinicallyanchored.ai" ;;
  esac
}

env_railway_service() {
  case "$1" in
    production) echo "clinically-anchored" ;;
    staging) echo "clinically-anchored-staging" ;;
  esac
}

# A project ref is 20 lowercase letters; anything else (the placeholder, a typo) is refused.
env_ref_is_set() { [[ "$1" =~ ^[a-z]{20}$ ]]; }

# Connection string for the session pooler (port 5432, never 6543). No password: it comes from
# PGPASSWORD, typed at a hidden prompt, so it is never in a URL, a process list or your history.
env_db_url() { echo "postgresql://postgres.$(env_project_ref "$1")@${POOLER_HOST}:5432/postgres"; }

# Read a password into PGPASSWORD without echoing it. Always asks: a PGPASSWORD left in your shell
# from another project's session is never reused.
prompt_password() {
  local label="$1"
  unset PGPASSWORD
  printf 'Database password for %s (input hidden): ' "$label" >&2
  if [[ -t 0 ]]; then stty -echo; fi
  local pw=""
  IFS= read -r pw || true
  if [[ -t 0 ]]; then stty echo; fi
  echo >&2
  [[ -n "$pw" ]] || { echo "No password entered." >&2; return 1; }
  export PGPASSWORD="$pw"
}

# The version of every migration file: "00000000000021_name.sql" -> "00000000000021".
migration_versions_in() { # <dir>
  local f
  for f in "$1"/*.sql; do
    [[ -e "$f" ]] || continue
    f="$(basename "$f")"; echo "${f%%_*}"
  done | sort
}
