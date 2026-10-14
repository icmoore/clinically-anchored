-- Wipe all patient-side trial data in EVERY clinic. Run it through reset_trial_data.sh,
-- which adds the safety rails (confirmation, backup, no CI). Never from a workflow, a
-- migration or a seed file.
--
--   Wiped:  message_drafts, thread_summaries, touchpoints, consents, messages, check_ins,
--           episodes, patients, audit_log
--   Kept:   clinics, clinic_members, procedures, message_schedules, auth.users
--
-- One transaction, one TRUNCATE over an explicit list (no CASCADE): a table that
-- references patients and is missing from the list makes the whole statement fail instead
-- of being silently wiped. TRUNCATE does not fire row-level triggers, which is how it gets
-- past message_drafts' "never deleted" trigger (migration 6); audit_log has no trigger and
-- is only protected by grants that do not apply to the table owner. Identity counters are
-- NOT restarted, so an old audit_log id is never reused.
--
-- The table list is filtered by what exists, so the script works before migration 9
-- (episodes absent) and after it. psql variable `execute`: true commits, anything else
-- (the default) rolls back after showing what would happen.

\set ON_ERROR_STOP on
\set QUIET on
\if :{?execute}
\else
  \set execute false
\endif

begin;
set local lock_timeout = '5s';

do $$
declare
  wipe text[] := array['message_drafts', 'thread_summaries', 'touchpoints', 'consents',
                       'messages', 'check_ins', 'episodes', 'patients', 'audit_log'];
  keep text[] := array['clinics', 'clinic_members', 'procedures', 'message_schedules'];
  t text;
  n bigint;
  present text[] := '{}';
  kept_before jsonb := '{}';
  kept_after jsonb := '{}';
begin
  foreach t in array wipe loop
    if to_regclass('public.' || t) is null then
      raise notice '  not present, skipped : %', t;
    else
      execute format('select count(*) from public.%I', t) into n;
      raise notice '  will wipe  % rows : %', lpad(n::text, 6), t;
      present := present || t;
    end if;
  end loop;

  foreach t in array keep loop
    if to_regclass('public.' || t) is not null then
      execute format('select count(*) from public.%I', t) into n;
      kept_before := kept_before || jsonb_build_object(t, n);
    end if;
  end loop;
  select count(*) into n from auth.users;
  kept_before := kept_before || jsonb_build_object('auth.users', n);
  raise notice '  kept (before)        : %', kept_before;

  execute 'truncate table '
    || (select string_agg(format('public.%I', x), ', ') from unnest(present) x);

  foreach t in array present loop
    execute format('select count(*) from public.%I', t) into n;
    if n <> 0 then
      raise exception 'reset: % still has % rows', t, n;
    end if;
  end loop;

  foreach t in array keep loop
    if to_regclass('public.' || t) is not null then
      execute format('select count(*) from public.%I', t) into n;
      kept_after := kept_after || jsonb_build_object(t, n);
    end if;
  end loop;
  select count(*) into n from auth.users;
  kept_after := kept_after || jsonb_build_object('auth.users', n);
  if kept_after is distinct from kept_before then
    raise exception 'reset: kept tables changed (% -> %)', kept_before, kept_after;
  end if;
  raise notice '  kept (after)         : % (unchanged)', kept_after;
end $$;

\if :execute
  commit;
  \echo 'COMMITTED: the data above is gone.'
\else
  rollback;
  \echo 'DRY RUN: rolled back, nothing changed. Re-run with --execute to wipe.'
\endif
