-- Follow-up extensions: after the nine-window care period ends by schedule, the patient may ask for
-- more follow-up through the app, and a clinician accepts or declines. PLACEHOLDER numbers (30 days
-- to ask, six more windows, one per 7 days) pending the surgeon's confirmation; they live in the
-- `extension` schedule row below, not in code.
--
-- The model, in one paragraph. A closed episode never changes (migration 9). So an accepted
-- extension is a NEW episode, kind 'extension', that continues the old one: same patient and
-- procedure, the ORIGINAL surgery date (so post-op day stays true), the check-in counted as done,
-- the same link generation. Its windows are numbered 1-6 inside it, so the one-message-per-window
-- index, replies and "unused windows are gone" work exactly as for the main period. What the patient
-- link may do after the main period ends, and what the extension's windows are, is COMPUTED by the
-- api (core/schedule.py, core/extension.py); only facts are stored here.
--
-- What is stored:
--   * message_schedules: `kind` ('main' | 'extension') and, for extension schedules,
--     `request_window_days`. Version 3 is "extension v1": window 1 opens at the moment of
--     acceptance, windows 2-6 at 07:00 America/Toronto on days 7, 14, 21, 28, 35 after the
--     acceptance DATE, the extension ends on day 42. (offsets_days lists windows 2-6 only.)
--   * episodes: `kind`, `extends_episode_id`, `started_at` (the acceptance instant).
--   * messages: two new origins, 'extension_request' (patient; the reason) and
--     'extension_decision' (clinician; what the clinic decided and said). They are thread entries
--     of their own, so every reader of the thread (summaries, briefs) sees them, but they are not
--     ordinary messages: they use no window, grant no reply (a decline cannot invite one), and
--     never count as an unread message.
--   * extension_requests: one row per ended episode (ever): pending, then accepted or declined, once.
--
-- No existing data changes and no data needs to be empty. The api's writes go through the two
-- functions at the bottom, which lock the episode like the other state changes do.

-- ---------------------------------------------------------------------------
-- Schedules: main vs extension
-- ---------------------------------------------------------------------------

alter table message_schedules
  add column kind text not null default 'main' check (kind in ('main', 'extension')),
  -- Extension schedules only: how many days after the care period ended the patient may ask.
  add column request_window_days integer check (request_window_days is null or request_window_days > 0),
  add constraint message_schedules_kind_shape check (
    (kind = 'main' and request_window_days is null)
    or (kind = 'extension' and request_window_days is not null)
  );

comment on column message_schedules.kind is
  'main: the first care period (offsets from the surgery date). extension: a follow-up extension '
  '(window 1 opens at acceptance; offsets_days are windows 2.. in days after the acceptance date).';

-- Same guard as migration 11, now also freezing kind and request_window_days once in use.
create or replace function message_schedules_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op <> 'INSERT' then
    if exists (select 1 from episodes where schedule_version = old.version) then
      if tg_op = 'DELETE'
         or (new.version, new.offsets_days, new.close_after_days, new.opens_at_local, new.timezone,
             new.kind, new.request_window_days)
            is distinct from
            (old.version, old.offsets_days, old.close_after_days, old.opens_at_local, old.timezone,
             old.kind, old.request_window_days)
      then
        raise exception 'message_schedules_in_use: a schedule version that episodes use cannot change; add a new version';
      end if;
    end if;
    if tg_op = 'DELETE' then
      return old;
    end if;
    if new.version <> old.version then
      raise exception 'message_schedules_version: the version number cannot change';
    end if;
  end if;
  perform now() at time zone new.timezone;  -- raises on an unknown zone
  return new;
end;
$$;

-- Extension v1. PLACEHOLDER: the surgeon has not confirmed these numbers.
insert into message_schedules
  (version, offsets_days, close_after_days, opens_at_local, timezone, kind, request_window_days, note)
values (3, array[7, 14, 21, 28, 35], 7, time '07:00', 'America/Toronto', 'extension', 30,
        'Extension v1 (placeholder, pending the surgeon): ask within 30 days after the care period '
        'ends; window 1 opens at acceptance, windows 2-6 at 07:00 on days 7, 14, 21, 28, 35 after the '
        'acceptance date; ends on day 42.');

-- ---------------------------------------------------------------------------
-- Episodes: an extension continues an ended episode
-- ---------------------------------------------------------------------------

alter table episodes
  add column kind text not null default 'main' check (kind in ('main', 'extension')),
  add column extends_episode_id uuid,
  add column started_at timestamptz,
  add constraint episodes_extension_shape check (
    (kind = 'main' and extends_episode_id is null and started_at is null)
    or (kind = 'extension' and extends_episode_id is not null and started_at is not null)
  ),
  -- Deferred, so a backup (rows in physical order) loads back inside one transaction.
  add constraint episodes_extends_fk
    foreign key (extends_episode_id, clinic_id, patient_id)
    references episodes (id, clinic_id, patient_id) deferrable initially deferred;

-- An episode is extended at most once (a second request needs the extension to have ended first,
-- and then it extends THAT episode).
create unique index episodes_extended_once on episodes (extends_episode_id)
  where extends_episode_id is not null;

create or replace function episodes_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'episodes_retained: episodes are never deleted';
  end if;
  if old.closed_at is not null then
    raise exception 'episodes_closed: a closed episode cannot change';
  end if;
  if (new.id, new.clinic_id, new.patient_id, new.schedule_version, new.created_by, new.created_at,
      new.kind, new.extends_episode_id, new.started_at)
     is distinct from
     (old.id, old.clinic_id, old.patient_id, old.schedule_version, old.created_by, old.created_at,
      old.kind, old.extends_episode_id, old.started_at) then
    raise exception 'episodes_original: that field of an episode cannot change';
  end if;
  if new.link_generation < old.link_generation then
    raise exception 'episodes_link_generation: the link generation only goes up';
  end if;
  if old.checkin_completed_at is not null
     and new.checkin_completed_at is distinct from old.checkin_completed_at then
    raise exception 'episodes_checkin: the check-in time cannot change once set';
  end if;
  if (new.surgery_date, new.procedure_id) is distinct from (old.surgery_date, old.procedure_id)
     and (old.checkin_completed_at is not null
          or exists (select 1 from messages m where m.episode_id = old.id and m.origin = 'scheduled')) then
    raise exception 'episodes_surgery_locked: the surgery date and procedure are fixed once the patient has checked in';
  end if;
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Messages: two thread entry kinds that are not messages
-- ---------------------------------------------------------------------------
-- extension_request:  sender patient,   body = the patient's reason (1-500 characters)
-- extension_decision: sender clinician, body = "Extension request accepted/declined." plus the
--                     clinician's own words if they wrote any
-- Both belong to the ENDED episode the request is about.

alter table messages
  drop constraint messages_origin_check,
  drop constraint messages_origin_shape,
  add constraint messages_origin_check check (
    origin in ('scheduled', 'reply', 'clinician', 'extension_request', 'extension_decision')
  ),
  add constraint messages_origin_shape check (
    (origin = 'scheduled' and sender = 'patient'
       and interval_no is not null and category is not null and reply_to_message_id is null)
    or (origin = 'reply' and sender = 'patient'
       and interval_no is null and category is null and reply_to_message_id is not null)
    or (origin = 'clinician' and sender = 'clinician'
       and interval_no is null and category is null and reply_to_message_id is null)
    or (origin = 'extension_request' and sender = 'patient'
       and interval_no is null and category is null and reply_to_message_id is null
       and char_length(btrim(body)) between 1 and 500)
    or (origin = 'extension_decision' and sender = 'clinician'
       and interval_no is null and category is null and reply_to_message_id is null)
  );

-- ---------------------------------------------------------------------------
-- Extension requests
-- ---------------------------------------------------------------------------
-- One row per ended episode, ever: a decline is final for that care period, and a pending request
-- blocks a second. Only the decision can change the row, once. Like message_drafts: retained, no
-- ON DELETE CASCADE (supabase/scripts/reset_trial_data.sh uses TRUNCATE).
--   id                 the id the audit events name (metadata.ref_id)
--   episode_id         the ENDED episode asked about
--   schedule_version   the extension schedule it was asked under (and starts under, if accepted)
--   decision_message   the clinician's own words, if any (what the patient is shown)
--   extension_episode_id the new episode, once accepted

create table extension_requests (
  id                    uuid primary key,
  clinic_id             uuid not null references clinics (id),
  patient_id            uuid not null,
  episode_id            uuid not null,
  schedule_version      integer not null references message_schedules (version),
  request_message_id    uuid not null,
  requested_at          timestamptz not null default now(),
  status                text not null default 'pending' check (status in ('pending', 'accepted', 'declined')),
  decided_by            uuid references auth.users (id),
  decided_at            timestamptz,
  decision_message      text check (decision_message is null or char_length(btrim(decision_message)) between 1 and 5000),
  decision_entry_id     uuid,
  extension_episode_id  uuid,

  foreign key (episode_id, clinic_id, patient_id) references episodes (id, clinic_id, patient_id),
  foreign key (episode_id, request_message_id)    references messages (episode_id, id),
  foreign key (episode_id, decision_entry_id)     references messages (episode_id, id),
  foreign key (extension_episode_id, clinic_id, patient_id) references episodes (id, clinic_id, patient_id),
  unique (episode_id),

  constraint extension_requests_decision_shape check (
    (status = 'pending' and decided_by is null and decided_at is null and decision_message is null
       and decision_entry_id is null and extension_episode_id is null)
    or (status = 'accepted' and decided_by is not null and decided_at is not null
       and decision_entry_id is not null and extension_episode_id is not null)
    or (status = 'declined' and decided_by is not null and decided_at is not null
       and decision_entry_id is not null and extension_episode_id is null)
  )
);

create index extension_requests_clinic_idx on extension_requests (clinic_id, status, requested_at);
create index extension_requests_patient_idx on extension_requests (patient_id, requested_at desc);

alter table extension_requests enable row level security;

create policy "clinic members can read their clinic's extension requests" on extension_requests
  for select using (is_clinic_member(clinic_id));

revoke insert, update, delete, truncate on extension_requests from authenticated, anon;

create function extension_requests_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'extension_requests_retained: an extension request is never deleted';
  end if;
  if old.status <> 'pending' then
    raise exception 'extension_requests_decided: a decided request cannot change';
  end if;
  if new.status = 'pending' then
    raise exception 'extension_requests_decided: a pending request can only be decided';
  end if;
  if (new.id, new.clinic_id, new.patient_id, new.episode_id, new.schedule_version,
      new.request_message_id, new.requested_at)
     is distinct from
     (old.id, old.clinic_id, old.patient_id, old.episode_id, old.schedule_version,
      old.request_message_id, old.requested_at) then
    raise exception 'extension_requests_original: the request itself cannot change';
  end if;
  return new;
end;
$$;

create trigger extension_requests_guard
  before update or delete on extension_requests
  for each row execute function extension_requests_guard();

-- ---------------------------------------------------------------------------
-- State changes: one transaction each, with the episode row locked
-- ---------------------------------------------------------------------------
-- The api decides what the clock says (is the term over, is the request still within its window)
-- and passes the ids it already audited under; these functions enforce what a race could break.
-- Error codes are the exception messages; api/extensions.py maps them to HTTP statuses.

-- The patient asks. Allowed only for an episode that ended BY SCHEDULE, before p_expires_at, that
-- has no request yet and that nothing newer has replaced.
create function request_extension(
  p_clinic_id        uuid,
  p_episode_id       uuid,
  p_request_id       uuid,
  p_message_id       uuid,
  p_reason           text,
  p_schedule_version integer,
  p_expires_at       timestamptz
) returns jsonb
language plpgsql
as $$
declare
  e episodes;
  s message_schedules;
  m messages;
  r extension_requests;
begin
  if p_reason is null or char_length(btrim(p_reason)) = 0 or char_length(p_reason) > 500 then
    raise exception 'extension_reason_invalid';
  end if;

  select * into e from episodes where id = p_episode_id and clinic_id = p_clinic_id for update;
  if not found then
    raise exception 'episode_not_found';
  end if;
  if e.closed_at is null then
    raise exception 'extension_term_running';
  end if;
  if e.close_reason <> 'schedule_complete' then
    raise exception 'extension_not_eligible';
  end if;
  if exists (select 1 from episodes where patient_id = e.patient_id and id <> e.id and created_at > e.created_at) then
    raise exception 'extension_not_eligible';
  end if;
  if exists (select 1 from extension_requests where episode_id = e.id) then
    raise exception 'extension_already_requested';
  end if;
  if now() >= p_expires_at then
    raise exception 'extension_window_over';
  end if;
  select * into s from message_schedules where version = p_schedule_version;
  if not found or s.kind <> 'extension' then
    raise exception 'extension_bad_schedule';
  end if;

  insert into messages (id, clinic_id, patient_id, episode_id, sender, body, origin)
  values (p_message_id, e.clinic_id, e.patient_id, e.id, 'patient', p_reason, 'extension_request')
  returning * into m;

  insert into extension_requests (id, clinic_id, patient_id, episode_id, schedule_version, request_message_id)
  values (p_request_id, e.clinic_id, e.patient_id, e.id, s.version, m.id)
  returning * into r;

  return jsonb_build_object('request', to_jsonb(r), 'message', to_jsonb(m));
end;
$$;

-- The clinician decides. 'accepted' creates the extension episode (window 1 opens now, in this
-- transaction); 'declined' only records the decision. p_entry_body is the thread entry's text,
-- built by the api; p_message is the clinician's own words (null if they wrote none).
create function decide_extension(
  p_clinic_id   uuid,
  p_request_id  uuid,
  p_decision    text,
  p_decided_by  uuid,
  p_message     text,
  p_entry_id    uuid,
  p_entry_body  text,
  p_episode_id  uuid
) returns jsonb
language plpgsql
as $$
declare
  r extension_requests;
  e episodes;
  m messages;
  x episodes;
  ext_id uuid;
  ext jsonb;
begin
  if p_decision not in ('accepted', 'declined') then
    raise exception 'extension_bad_decision';
  end if;
  if p_message is not null and (char_length(btrim(p_message)) = 0 or char_length(p_message) > 5000) then
    raise exception 'extension_message_invalid';
  end if;

  select * into r from extension_requests where id = p_request_id and clinic_id = p_clinic_id for update;
  if not found then
    raise exception 'extension_not_found';
  end if;
  if r.status <> 'pending' then
    raise exception 'extension_not_pending';
  end if;
  select * into e from episodes where id = r.episode_id for update;

  insert into messages (id, clinic_id, patient_id, episode_id, sender, body, origin)
  values (p_entry_id, e.clinic_id, e.patient_id, e.id, 'clinician', p_entry_body, 'extension_decision')
  returning * into m;

  if p_decision = 'accepted' then
    if exists (select 1 from episodes where patient_id = e.patient_id and id <> e.id and created_at > e.created_at) then
      raise exception 'extension_superseded';
    end if;
    insert into episodes (id, clinic_id, patient_id, procedure_id, surgery_date, schedule_version,
                          checkin_completed_at, link_generation, created_by, kind,
                          extends_episode_id, started_at)
    values (p_episode_id, e.clinic_id, e.patient_id, e.procedure_id, e.surgery_date,
            r.schedule_version, now(), e.link_generation, p_decided_by, 'extension', e.id, now())
    returning * into x;
    ext_id := x.id;
    ext := to_jsonb(x);
  end if;

  update extension_requests
     set status = p_decision, decided_by = p_decided_by, decided_at = now(),
         decision_message = p_message, decision_entry_id = m.id, extension_episode_id = ext_id
   where id = r.id
  returning * into r;

  return jsonb_build_object('request', to_jsonb(r), 'message', to_jsonb(m), 'episode', ext);
end;
$$;

revoke execute on function request_extension, decide_extension from public, anon, authenticated;
grant execute on function request_extension, decide_extension to service_role;
