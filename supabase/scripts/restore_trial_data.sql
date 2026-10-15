-- Load a backup taken by reset_trial_data.sh back into the database. Run it through
-- restore_trial_data.sh, which prepares the file (see backup_common.sh) and adds the safety rails.
-- Never from a workflow, a migration or a seed file.
--
-- psql variables:
--   backup  path of the PREPARED backup file
--   tables  comma-separated table names the backup covers (backup_tables_csv)
--   mode    one of
--             check   the tables must be empty; load the backup, show counts, ROLL BACK
--             commit  the tables must be empty; load the backup and COMMIT
--             verify  (used by the reset script, before it wipes anything) prove the backup
--                     restores: fingerprint every table, empty them, load the backup, fingerprint
--                     again, require the two to match exactly, then ROLL BACK
--
-- It never loads into tables that already hold rows: a restore goes into a freshly reset database.
-- Everything is one transaction; any error rolls the whole thing back.

\set ON_ERROR_STOP on
\set QUIET on
-- Never wait on a pager at a terminal.
\pset pager off

\if :{?mode}
\else
  \echo 'set -v mode=check|commit|verify'
  \quit
\endif

begin;
set local lock_timeout = '5s';
select set_config('ca.tables', :'tables', true) \gset

create temp table _present as
  select t as name
  from unnest(string_to_array(current_setting('ca.tables'), ',')) as t
  where to_regclass('public.' || t) is not null;

create temp table _fingerprint (phase text, tbl text, n bigint, h text);

create function pg_temp.fingerprint(p_phase text) returns void
language plpgsql
as $$
declare
  t text;
begin
  for t in select name from _present loop
    execute format(
      'insert into _fingerprint select %L, %L, count(*), '
      'md5(coalesce(string_agg(x::text, ''|'' order by x::text), '''')) from public.%I x',
      p_phase, t, t);
  end loop;
end $$;

create function pg_temp.require_empty() returns void
language plpgsql
as $$
declare
  t text;
  n bigint;
  full_tables text[] := '{}';
begin
  for t in select name from _present loop
    execute format('select count(*) from public.%I', t) into n;
    if n > 0 then
      full_tables := full_tables || format('%s (%s rows)', t, n);
    end if;
  end loop;
  if cardinality(full_tables) > 0 then
    raise exception 'restore needs empty tables, but these hold rows: %. Reset first.',
      array_to_string(full_tables, ', ');
  end if;
end $$;

select :'mode' = 'verify' as verifying \gset
\if :verifying
  do $$ begin perform pg_temp.fingerprint('before'); end $$;
  -- Same explicit list as reset_trial_data.sql, no CASCADE.
  select 'truncate table ' || string_agg(format('public.%I', name), ', ') as stmt from _present \gset
  :stmt;
\else
  do $$ begin perform pg_temp.require_empty(); end $$;
\endif

\i :backup

-- The dump changes a few session settings (search_path, client_min_messages, ...): undo the two
-- that matter to what follows.
reset search_path;
reset client_min_messages;

\if :verifying
  do $$ begin perform pg_temp.fingerprint('after'); end $$;
  do $$
  declare bad text;
  begin
    select string_agg(format('%s: before %s rows / after %s', b.tbl, b.n, a.n), '; ')
      into bad
    from _fingerprint b
    join _fingerprint a on a.tbl = b.tbl and a.phase = 'after'
    where b.phase = 'before' and (a.n, a.h) is distinct from (b.n, b.h);
    if bad is not null then
      raise exception 'the backup does not restore to the same data: %', bad;
    end if;
  end $$;
  select count(*) as tables_checked, sum(n) as rows_checked from _fingerprint where phase = 'after' \gset
  \echo VERIFIED: :tables_checked tables, :rows_checked rows load back identical (rolled back, nothing changed).
  rollback;
\else
  do $$
  declare
    t text;
    n bigint;
  begin
    for t in select name from _present order by name loop
      execute format('select count(*) from public.%I', t) into n;
      raise notice '  loaded % rows : %', lpad(n::text, 6), t;
    end loop;
  end $$;
  select :'mode' = 'commit' as committing \gset
  \if :committing
    commit;
    \echo COMMITTED: the backup is restored.
  \else
    rollback;
    \echo CHECK ONLY: the backup loads cleanly into the empty tables; rolled back, nothing changed.
  \endif
\endif
