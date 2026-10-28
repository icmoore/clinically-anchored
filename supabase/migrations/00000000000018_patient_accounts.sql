-- Patient accounts, part B: a patient signs in with their email and a code instead of a link.
--
-- A patient is a Supabase Auth user (created by the api, with app_metadata.account_type =
-- 'patient'; staff never carry it). patient_accounts links that user to ONE patient record and
-- holds the account's state. Everything the api does with it goes through the service role; no
-- authenticated user writes here, and none can read another clinic's rows.
--
-- What this migration changes for the people who are ALREADY signed in (clinic staff):
--   * message_schedules: "signed-in users can read schedules" (to authenticated using (true))
--     became readable by ANY Supabase Auth user, which now includes patients. It is now readable
--     by clinic members only. Nothing else a patient user could reach changes: every other policy
--     already goes through is_clinic_member(), which a patient is not.
--   * a user cannot be both a clinic member and a patient account (two triggers).
--
-- Email: patients.email is where the invite and the sign-in codes go. It is separate from the
-- free-text `contact` (phone or email). Existing contacts that are an email address are copied.
-- Once the account is active the email cannot change (turn access off and re-invite first).

-- ---------------------------------------------------------------------------
-- patients.email
-- ---------------------------------------------------------------------------

alter table patients
  add column email text
  check (email is null or (email = lower(btrim(email)) and char_length(email) between 3 and 254));

update patients
set email = lower(btrim(contact))
where email is null
  and btrim(contact) ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
  and char_length(btrim(contact)) <= 254;

-- ---------------------------------------------------------------------------
-- Staff-only schedule reads
-- ---------------------------------------------------------------------------

create or replace function is_any_clinic_member()
returns boolean
language sql
stable
as $$
  select exists (select 1 from clinic_members where user_id = auth.uid());
$$;

drop policy "signed-in users can read schedules" on message_schedules;
create policy "clinic members can read schedules" on message_schedules
  for select to authenticated using (is_any_clinic_member());

-- ---------------------------------------------------------------------------
-- Accounts
-- ---------------------------------------------------------------------------
-- Like episodes: retained, no ON DELETE CASCADE (deleting a patient or an Auth user that has an
-- account is refused). status:
--   invited  the clinic invited them; they have not finished the first sign-in (surgery-date check
--            and consent)
--   active   first sign-in finished
--   locked   three wrong surgery dates; only a re-invite from the clinic unlocks it
--   off      the clinic turned access off; sessions ended; only a re-invite brings it back
-- One account per patient. One live (not 'off') account per Auth user, so a patient session can
-- never be ambiguous about whose data it shows.

create table patient_accounts (
  id                        uuid primary key default gen_random_uuid(),
  clinic_id                 uuid not null references clinics (id),
  patient_id                uuid not null,
  user_id                   uuid not null references auth.users (id),
  status                    text not null check (status in ('invited', 'active', 'locked', 'off')),
  invited_at                timestamptz not null default now(),
  invited_by                uuid not null references auth.users (id),
  activated_at              timestamptz,
  turned_off_at             timestamptz,
  turned_off_by             uuid references auth.users (id),
  locked_at                 timestamptz,
  last_sign_in_at           timestamptz,
  identity_verified_at      timestamptz,
  identity_failed_attempts  integer not null default 0 check (identity_failed_attempts >= 0),
  -- The sign-in code in force: when it was sent and how many tries it has used. The code itself
  -- is never stored here (Supabase Auth holds it, hashed).
  code_sent_at              timestamptz,
  code_failed_attempts      integer not null default 0 check (code_failed_attempts >= 0),
  created_at                timestamptz not null default now(),

  foreign key (clinic_id, patient_id) references patients (clinic_id, id),
  unique (patient_id),

  constraint patient_accounts_active_consistent check (
    status <> 'active' or (activated_at is not null and identity_verified_at is not null)
  ),
  -- turned_off_* and locked_at keep the LAST time it happened after a re-invite (the audit log is
  -- the full history); only the status says whether it is so now.
  constraint patient_accounts_off_consistent check (
    status <> 'off' or (turned_off_at is not null and turned_off_by is not null)
  ),
  constraint patient_accounts_locked_consistent check (status <> 'locked' or locked_at is not null)
);

create unique index patient_accounts_one_live_per_user on patient_accounts (user_id)
  where status <> 'off';
create index patient_accounts_clinic_idx on patient_accounts (clinic_id, status);

alter table patient_accounts enable row level security;

create policy "clinic members can read their clinic's patient accounts" on patient_accounts
  for select using (is_clinic_member(clinic_id));

revoke insert, update, delete, truncate on patient_accounts from authenticated, anon;

-- A staff member is never a patient, and a patient is never staff: the two kinds of Auth user
-- must stay apart, because the api treats "has a clinic membership" as "is staff".
create function patient_accounts_not_staff() returns trigger
language plpgsql
as $$
begin
  if exists (select 1 from clinic_members where user_id = new.user_id) then
    raise exception 'patient_account_user_is_staff';
  end if;
  return new;
end;
$$;

create trigger patient_accounts_not_staff
  before insert or update of user_id on patient_accounts
  for each row execute function patient_accounts_not_staff();

create function clinic_members_not_patient() returns trigger
language plpgsql
as $$
begin
  if exists (select 1 from patient_accounts where user_id = new.user_id) then
    raise exception 'staff_user_is_patient';
  end if;
  return new;
end;
$$;

create trigger clinic_members_not_patient
  before insert or update of user_id on clinic_members
  for each row execute function clinic_members_not_patient();

-- Once active, the address the codes go to cannot be edited under the account.
create function patients_email_locked() returns trigger
language plpgsql
as $$
begin
  if new.email is distinct from old.email
     and exists (select 1 from patient_accounts where patient_id = new.id and status = 'active') then
    raise exception 'patient_email_locked';
  end if;
  return new;
end;
$$;

create trigger patients_email_locked
  before update of email on patients
  for each row execute function patients_email_locked();

-- ---------------------------------------------------------------------------
-- Single-use fallback sign-in links (for patients without email)
-- ---------------------------------------------------------------------------
-- Only the SHA-256 of the secret is stored, so a link can be shown once and never again. Used once,
-- valid until expires_at (24 hours from issue), replaced when the clinic issues a newer one.

create table patient_sign_in_links (
  id          uuid primary key default gen_random_uuid(),
  clinic_id   uuid not null references clinics (id),
  account_id  uuid not null references patient_accounts (id),
  token_hash  text not null unique check (token_hash ~ '^[0-9a-f]{64}$'),
  issued_by   uuid not null references auth.users (id),
  issued_at   timestamptz not null default now(),
  expires_at  timestamptz not null,
  used_at     timestamptz,
  revoked_at  timestamptz,
  check (expires_at > issued_at),
  check (used_at is null or revoked_at is null)
);

create index patient_sign_in_links_account_idx on patient_sign_in_links (account_id, issued_at desc);

alter table patient_sign_in_links enable row level security;
-- No policy: nobody signed in reads or writes it. Only the api (service role) does.
revoke all on patient_sign_in_links from anon, authenticated;

create function patient_sign_in_links_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'patient_sign_in_links_retained';
  end if;
  if (new.id, new.clinic_id, new.account_id, new.token_hash, new.issued_by, new.issued_at,
      new.expires_at)
     is distinct from
     (old.id, old.clinic_id, old.account_id, old.token_hash, old.issued_by, old.issued_at,
      old.expires_at) then
    raise exception 'patient_sign_in_links_immutable';
  end if;
  -- used_at / revoked_at are each set once, on a link that is still unused and unrevoked.
  if (old.used_at is not null and new.used_at is distinct from old.used_at)
     or (old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at) then
    raise exception 'patient_sign_in_links_settled';
  end if;
  return new;
end;
$$;

create trigger patient_sign_in_links_guard
  before update or delete on patient_sign_in_links
  for each row execute function patient_sign_in_links_guard();

-- ---------------------------------------------------------------------------
-- Atomic steps the api cannot do safely with a read and then a write
-- ---------------------------------------------------------------------------
-- All callable by the service role only. Each locks the account row, so two requests at once
-- cannot both slip under a limit. They return jsonb.

-- A new sign-in code was generated for this account: the clock and the tries start over.
create function issue_patient_code(p_account_id uuid) returns jsonb
language plpgsql
as $$
begin
  update patient_accounts
  set code_sent_at = now(), code_failed_attempts = 0
  where id = p_account_id and status in ('invited', 'active');
  return jsonb_build_object('ok', found);
end;
$$;

-- Someone is about to try a code. Counts the try BEFORE the code is checked (so parallel guesses
-- cannot outrun the limit) and says whether the try may go ahead.
--   no_account  no live account       expired   no code in force, or older than p_ttl_seconds
--   too_many    p_max tries used      ok        go ahead (this try is counted)
create function claim_patient_code_attempt(p_account_id uuid, p_max integer, p_ttl_seconds integer)
returns jsonb
language plpgsql
as $$
declare
  a patient_accounts;
begin
  select * into a from patient_accounts where id = p_account_id for update;
  if not found or a.status not in ('invited', 'active') then
    return jsonb_build_object('outcome', 'no_account');
  end if;
  if a.code_sent_at is null or a.code_sent_at < now() - make_interval(secs => p_ttl_seconds) then
    return jsonb_build_object('outcome', 'expired');
  end if;
  if a.code_failed_attempts >= p_max then
    return jsonb_build_object('outcome', 'too_many');
  end if;
  update patient_accounts set code_failed_attempts = code_failed_attempts + 1
  where id = p_account_id;
  return jsonb_build_object('outcome', 'ok');
end;
$$;

-- The code worked: it is spent, and this is the patient's latest sign-in.
create function finish_patient_code(p_account_id uuid) returns jsonb
language plpgsql
as $$
begin
  update patient_accounts
  set code_sent_at = null, code_failed_attempts = 0, last_sign_in_at = now()
  where id = p_account_id;
  return jsonb_build_object('ok', found);
end;
$$;

-- The surgery-date check. The api compares the date; this applies the outcome under the lock.
--   verified    identity_verified_at is set, the counter resets
--   wrong       one more miss; attempts_left says how many remain
--   locked      p_max misses: the account is locked (an active account too; the clinic re-invites)
--   not_allowed the account is locked, off or missing
create function record_patient_identity(p_account_id uuid, p_matches boolean, p_max integer)
returns jsonb
language plpgsql
as $$
declare
  a patient_accounts;
  misses integer;
begin
  select * into a from patient_accounts where id = p_account_id for update;
  if not found or a.status not in ('invited', 'active') then
    return jsonb_build_object('outcome', 'not_allowed');
  end if;
  if p_matches then
    update patient_accounts set identity_verified_at = now(), identity_failed_attempts = 0
    where id = p_account_id;
    return jsonb_build_object('outcome', 'verified');
  end if;
  misses := a.identity_failed_attempts + 1;
  if misses >= p_max then
    update patient_accounts
    set identity_failed_attempts = misses, status = 'locked', locked_at = now()
    where id = p_account_id;
    return jsonb_build_object('outcome', 'locked', 'attempts_left', 0);
  end if;
  update patient_accounts set identity_failed_attempts = misses where id = p_account_id;
  return jsonb_build_object('outcome', 'wrong', 'attempts_left', p_max - misses);
end;
$$;

-- Use up a fallback link, once. Returns the link row, or null if it is not usable right now
-- (unknown, used, replaced, expired).
create function consume_patient_link(p_token_hash text) returns jsonb
language plpgsql
as $$
declare
  l patient_sign_in_links;
begin
  update patient_sign_in_links
  set used_at = now()
  where token_hash = p_token_hash and used_at is null and revoked_at is null
    and expires_at > now()
  returning * into l;
  if not found then
    return null;
  end if;
  return to_jsonb(l);
end;
$$;

-- ---------------------------------------------------------------------------
-- Reading and ending Supabase Auth sessions (the auth schema belongs to Supabase Auth)
-- ---------------------------------------------------------------------------

-- Turn-off ends every session the user has: their refresh tokens go with the session rows.
create function end_patient_sessions(p_user_id uuid) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  n integer;
begin
  delete from auth.sessions where user_id = p_user_id;
  get diagnostics n = row_count;
  return jsonb_build_object('ended', n);
end;
$$;

-- When a session began: the api ends patient sessions older than its limit.
create function patient_session_started(p_session_id uuid) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  t timestamptz;
begin
  select created_at into t from auth.sessions where id = p_session_id;
  return case when t is null then null else jsonb_build_object('created_at', t) end;
end;
$$;

-- Supabase's Admin API has no "find user by email".
create function find_auth_user_by_email(p_email text) returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  uid uuid;
begin
  select id into uid from auth.users where lower(email) = lower(p_email) limit 1;
  return case when uid is null then null else jsonb_build_object('id', uid) end;
end;
$$;

revoke execute on function
  issue_patient_code, claim_patient_code_attempt, finish_patient_code, record_patient_identity,
  consume_patient_link, end_patient_sessions, patient_session_started, find_auth_user_by_email
  from public, anon, authenticated;
grant execute on function
  issue_patient_code, claim_patient_code_attempt, finish_patient_code, record_patient_identity,
  consume_patient_link, end_patient_sessions, patient_session_started, find_auth_user_by_email
  to service_role;
