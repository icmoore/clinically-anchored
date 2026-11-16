-- Which environment this database is: 'production' or 'staging'.
--
-- Two Supabase projects exist (production and staging) and the same tooling points at either.
-- app_settings holds ONE row saying which one this is, so the code that runs against a database
-- can check before it does anything:
--
--   * the api refuses to start unless APP_ENV matches this row (core/environment.py);
--   * reset_trial_data.sql and restore_trial_data.sql call assert_not_production() first;
--   * the Python seed reads the row and refuses on anything but 'staging';
--   * a clinic can't be made is_demo in a production database (trigger below).
--
-- THIS MIGRATION INSERTS NO ROW. Each project's row is set by hand, once, as the table owner
-- (docs/environments.md):
--
--     insert into app_settings (environment) values ('staging');      -- or 'production'
--
-- Until the row exists the api won't start. The destructive scripts only refuse when the row says
-- 'production', so the one-time reset that turns the current project into production can still
-- run before its row is inserted (the cutover order in docs/environments.md depends on that).
--
-- service_role may only READ the row: no route, script or seed can flip a staging database to
-- production or back. Changing it is a deliberate psql session as the table owner.

create table app_settings (
  -- Always true, and the primary key: a second row can't be inserted.
  singleton   boolean primary key default true check (singleton),
  environment text not null check (environment in ('production', 'staging')),
  set_at      timestamptz not null default now()
);

alter table app_settings enable row level security;
-- No policies: clinicians and patients (anon / authenticated) can't see or touch it.
revoke all on app_settings from anon, authenticated;
revoke all on app_settings from service_role;
grant select on app_settings to service_role;

-- ---------------------------------------------------------------------------
-- assert_not_production(what): raises when this database is production.
-- Called at the top of every script that bulk-deletes or bulk-creates. Not callable through the
-- REST API: only the table owner (psql as postgres) can run it.
-- ---------------------------------------------------------------------------

create function assert_not_production(p_what text default 'this operation')
returns void
language plpgsql
stable
set search_path = public, pg_temp
as $$
begin
  if exists (select 1 from public.app_settings where environment = 'production') then
    raise exception '% refused: this database is PRODUCTION (app_settings.environment).', p_what
      using errcode = 'P0001',
            hint = 'Run it against the staging project instead.';
  end if;
end;
$$;

revoke all on function assert_not_production(text) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- A demo clinic can't exist in production. is_demo is already settable only by SQL (migration 17);
-- this makes even that SQL fail where it would put a "never sends real email" clinic next to real
-- patients.
-- ---------------------------------------------------------------------------

create function clinics_refuse_demo_in_production()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if new.is_demo then
    perform public.assert_not_production('Making a clinic a demo clinic');
  end if;
  return new;
end;
$$;

revoke all on function clinics_refuse_demo_in_production() from public, anon, authenticated, service_role;

create trigger clinics_refuse_demo_in_production
  before insert or update of is_demo on clinics
  for each row execute function clinics_refuse_demo_in_production();
