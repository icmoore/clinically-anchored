-- The patient's "Before you start" screen: Terms, Privacy Notice and the two consents, versioned.
--
-- patient_acceptances is an APPEND-ONLY record of what a patient did with that screen: accepted it,
-- declined it, or later withdrew. Each row says which version of each document the patient was
-- shown and the SHA-256 of the exact text of each, so "what did they agree to?" can be answered
-- from the row alone, years later. Nothing is ever updated: a new answer is a new row, and the
-- LATEST row is where the patient stands now.
--
-- Written only by apps/api (service role), audit event first (acceptance.accepted / .declined /
-- .withdrawn). Clinic members can read; nobody signed in can write. The two consent rows in
-- `consents` (migration 5) are still written on accept and ended on withdraw, so what gates
-- activation does not change.
--
-- Also: an episode can now be closed with reason 'withdrawn' (a patient chose to stop using the
-- app). It stops the messaging windows the same way a discharge does; the closer is the patient's
-- own Auth user.

-- ---------------------------------------------------------------------------
-- Episode close reason: add 'withdrawn'
-- ---------------------------------------------------------------------------

alter table episodes drop constraint episodes_close_consistent;
alter table episodes drop constraint episodes_close_reason_check;

alter table episodes add constraint episodes_close_reason_check
  check (close_reason in ('discharged', 'schedule_complete', 'withdrawn'));

-- A discharge records the clinician and a withdrawal the patient's own user; a natural close
-- records nobody.
alter table episodes add constraint episodes_close_consistent check (
  (closed_at is null and close_reason is null and closed_by is null)
  or (closed_at is not null and close_reason in ('discharged', 'withdrawn') and closed_by is not null)
  or (closed_at is not null and close_reason = 'schedule_complete' and closed_by is null)
);

-- close_episode (migration 9) with the new reason. 'withdrawn' behaves like 'discharged': effective
-- now, the closer required.
create or replace function close_episode(
  p_clinic_id   uuid,
  p_episode_id  uuid,
  p_reason      text,
  p_closed_by   uuid,
  p_closed_at   timestamptz default null
) returns jsonb
language plpgsql
as $$
declare
  e episodes;
  at_time timestamptz;
begin
  if p_reason not in ('discharged', 'schedule_complete', 'withdrawn') then
    raise exception 'episode_bad_reason';
  end if;
  if (p_reason in ('discharged', 'withdrawn')) <> (p_closed_by is not null) then
    raise exception 'episode_bad_closer';
  end if;

  select * into e from episodes where id = p_episode_id and clinic_id = p_clinic_id for update;
  if not found then
    raise exception 'episode_not_found';
  end if;
  if e.closed_at is not null then
    return jsonb_build_object('episode', to_jsonb(e), 'changed', false);
  end if;

  at_time := case when p_reason in ('discharged', 'withdrawn') then now()
                  else coalesce(p_closed_at, now()) end;
  if at_time > now() then
    raise exception 'episode_close_in_future';
  end if;

  update episodes
     set closed_at = at_time, close_reason = p_reason, closed_by = p_closed_by
   where id = e.id
  returning * into e;
  return jsonb_build_object('episode', to_jsonb(e), 'changed', true);
end;
$$;

-- ---------------------------------------------------------------------------
-- Acceptance records
-- ---------------------------------------------------------------------------
-- Like consents and episodes: retained, no ON DELETE CASCADE (deleting a patient or an episode
-- that has a record is refused). The api picks the id, so the audit event can name it first.

create table patient_acceptances (
  id              uuid primary key,
  clinic_id       uuid not null,
  patient_id      uuid not null,
  episode_id      uuid not null,
  action          text not null check (action in ('accepted', 'declined', 'withdrawn')),
  -- The versions of the documents and of the two consent sentences the patient was shown.
  terms_version   text not null check (char_length(terms_version) between 1 and 64),
  privacy_version text not null check (char_length(privacy_version) between 1 and 64),
  consent_version text not null check (char_length(consent_version) between 1 and 200),
  -- SHA-256 (lower-case hex) of the exact text shown for each.
  terms_sha256    text not null check (terms_sha256 ~ '^[0-9a-f]{64}$'),
  privacy_sha256  text not null check (privacy_sha256 ~ '^[0-9a-f]{64}$'),
  consent_sha256  text not null check (consent_sha256 ~ '^[0-9a-f]{64}$'),
  created_at      timestamptz not null default now(),

  foreign key (clinic_id, patient_id) references patients (clinic_id, id),
  foreign key (episode_id, clinic_id, patient_id) references episodes (id, clinic_id, patient_id)
);

-- "Where does this patient stand?" is the newest row.
create index patient_acceptances_patient_idx
  on patient_acceptances (clinic_id, patient_id, created_at desc);

alter table patient_acceptances enable row level security;

create policy "clinic members can read their clinic's acceptances" on patient_acceptances
  for select using (is_clinic_member(clinic_id));

-- No insert/update/delete policy, and the privileges go too: the only writer is the audited api.
revoke insert, update, delete, truncate on patient_acceptances from authenticated, anon;

-- Append-only, also against the service role and the table owner. (TRUNCATE is not a row
-- operation, so the trial-data reset script, which truncates, still works.)
create function patient_acceptances_append_only() returns trigger
language plpgsql
as $$
begin
  raise exception 'patient_acceptances_append_only';
end;
$$;

create trigger patient_acceptances_append_only
  before update or delete on patient_acceptances
  for each row execute function patient_acceptances_append_only();
