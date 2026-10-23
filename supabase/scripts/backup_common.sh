# Shared by reset_trial_data.sh and restore_trial_data.sh (sourced, not run).
#
# Why a backup needs more than `pg_dump --data-only`. Three things stop a plain dump loading back
# into the wiped database (found by trying it):
#   1. The dump starts with set_config('search_path', '', false). The reply guard on `messages`
#      (migration 9) looks up `messages` unqualified, so the first reply row fails with
#      "relation messages does not exist". We drop that one line when restoring: the dump's own
#      statements are all schema-qualified, so nothing else depends on it.
#   2. `messages` is loaded row by row and the same guard checks that a reply's target already
#      exists. pg_dump writes rows in physical order, which is not guaranteed to put a message
#      before its reply (an UPDATE or a vacuum can move rows). We load `messages` through a
#      temporary table and insert it with replies last. (pg_dump's own warning about circular
#      foreign keys on `messages` is this self-reference; it is expected and handled here.)
#   3. A dump made by pg_dump 17/18 begins with `SET transaction_timeout`, which a server older
#      than 17 does not know. We drop that line too.
# `--disable-triggers` is NOT used: it runs ALTER TABLE ... DISABLE TRIGGER ALL, and disabling the
# foreign-key triggers needs a superuser, which Supabase's `postgres` role is not. Nothing here
# needs more than the privileges the reset script already uses.

# The tables a backup holds, in no particular order (pg_dump sorts them by foreign key).
BACKUP_TABLES=(message_drafts thread_summaries patient_briefs care_provider_summaries care_provider_summary_edits touchpoints consents messages check_ins episodes patients audit_log)

backup_tables_csv() {
  local IFS=,
  echo "${BACKUP_TABLES[*]}"
}

# take_backup <database-url> <outfile>: a data-only dump of the tables that exist.
take_backup() {
  local url="$1" out="$2" t exists
  local targs=()
  for t in "${BACKUP_TABLES[@]}"; do
    exists="$(psql "$url" -X -At -c "select to_regclass('public.$t') is not null")"
    [[ "$exists" == t ]] && targs+=(-t "public.$t")
  done
  pg_dump "$url" --data-only --no-owner --no-privileges "${targs[@]}" -f "$out"
  chmod 600 "$out"
}

# prepare_backup <backup> <outfile>: the same data, made loadable (see the top of this file).
prepare_backup() {
  local in="$1" out="$2"
  awk '
    /^SET transaction_timeout = / { next }
    /^SELECT pg_catalog\.set_config\(.search_path., .., false\);$/ { next }
    /^COPY public\.messages \(/ {
      cols = $0
      sub(/^COPY public\.messages \(/, "", cols)
      sub(/\) FROM stdin;$/, "", cols)
      print "CREATE TEMP TABLE messages_stage (LIKE public.messages);"
      sub(/public\.messages/, "pg_temp.messages_stage")
      staging = 1
    }
    { print }
    staging && $0 == "\\." {
      # Replies (which point at an earlier message of the same table) last. Before migration 9 there is
      # no `origin` column, and nothing points at anything.
      order = (cols ~ /origin/) ? "(origin = '"'"'reply'"'"'), created_at, id" : "created_at, id"
      print "INSERT INTO public.messages (" cols ") SELECT " cols " FROM pg_temp.messages_stage ORDER BY " order ";"
      print "DROP TABLE pg_temp.messages_stage;"
      staging = 0
    }
  ' "$in" > "$out"
  chmod 600 "$out"
}
