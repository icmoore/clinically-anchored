-- The parts of Supabase the migrations lean on, so they can run on a plain local Postgres:
-- the three API roles (with Supabase's default "grant everything on public tables" so the
-- REVOKEs in the migrations mean something), the auth schema, and auth.uid().

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin bypassrls;  -- as in Supabase: the api bypasses RLS
  end if;
end $$;
alter role service_role bypassrls;

create schema if not exists auth;
create table auth.users (
  id    uuid primary key default gen_random_uuid(),
  email text
);
create function auth.uid() returns uuid
language sql stable
as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;

grant usage on schema public, auth to anon, authenticated, service_role;
grant select on auth.users to authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
