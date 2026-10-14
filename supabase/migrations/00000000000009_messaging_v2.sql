-- Messaging v2: episodes and scheduled patient messaging.
--
-- A patient's post-op period is an EPISODE: one surgery, one surgery date (entered by the
-- clinic), one schedule version. After the patient completes the post-op check-in they may
-- send ONE message per scheduled interval (opening N days after surgery at 07:00 clinic
-- time, open until the next one opens), plus one reply to each doctor message. When the
-- last interval ends, or a clinician discharges the patient, the episode closes and the
-- patient link stops working.
--
-- What is stored and what is computed:
--   * Stored facts: the episode (surgery date, schedule version, check-in time, close),
--     and on each message the interval it used, its category and its origin.
--   * COMPUTED, never stored: which interval is open, whether it is used, when the next
--     one opens, which intervals were missed. That is a pure function of the schedule,
--     the surgery date, the clock and the messages, and lives in the api (core/schedule.py,
--     a later PR). Nothing has to run at 07:00 for an interval to open.
--   * The database enforces what a race could break and the api cannot: one scheduled
--     message per interval, one reply per doctor message, nothing written to a discharged
--     episode, and every row pointing at an episode of the SAME clinic and patient. It
--     does not know the clock: the api's engine decides which interval is open.
--
-- *** This migration needs EMPTY patient data. *** Messages, check-ins and patients must
-- have no rows (new NOT NULL columns, and every patient needs an episode). Run
-- supabase/scripts/reset_trial_data.sh first; the check below stops the migration with a
-- message if that was skipped. Nothing here deletes your data.
--
-- Also closed here, because the new invariants would otherwise be bypassable: clinic
-- members could write messages, patients and touchpoints straight through PostgREST with
-- no audit. Those writes now go through apps/api only (the web app never used them).

-- ---------------------------------------------------------------------------
-- Preflight
-- ---------------------------------------------------------------------------

-- Migration 2's fixture patient has no dependents on a fresh database; drop it so a local
-- `supabase db reset` can apply this migration. If anything references it, the check
-- below fails as intended.
delete from patients where id = '00000000-0000-0000-0000-0000000000e1';

do $$
begin
  if exists (select 1 from messages)
     or exists (select 1 from check_ins)
     or exists (select 1 from patients) then
    raise exception
      'messaging_v2_needs_empty_patient_data: run supabase/scripts/reset_trial_data.sh before applying migration 9';
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- Clinic office phone (shown on every locked or closed patient screen)
-- ---------------------------------------------------------------------------

alter table clinics
  add column office_phone text check (office_phone is null or char_length(office_phone) between 3 and 40);

-- ---------------------------------------------------------------------------
-- Message schedules: versioned data, not code
-- ---------------------------------------------------------------------------
-- offsets_days: interval N opens `offsets_days[N]` days after the surgery date, at
-- opens_at_local in `timezone`. The last interval stays open `close_after_days` after it
-- opens; the episode closes then. A new schedule is a new version; episodes keep the
-- version they were created with.

create function schedule_offsets_ok(o int[]) returns boolean
language sql immutable
as $$
  select o is not null
     and array_ndims(o) = 1
     and array_lower(o, 1) = 1
     and cardinality(o) >= 1
     and array_position(o, null) is null
     and o[1] >= 1
     and not exists (
       select 1 from generate_subscripts(o, 1) i where i > 1 and o[i] <= o[i - 1]
     );
$$;

create table message_schedules (
  version          integer primary key check (version > 0),
  offsets_days     integer[] not null check (schedule_offsets_ok(offsets_days)),
  close_after_days integer not null check (close_after_days > 0),
  -- Not before 04:00: Toronto's clocks change at 02:00, so any opening time from 04:00 on
  -- exists exactly once on every day of the year.
  opens_at_local   time not null check (opens_at_local >= time '04:00'),
  timezone         text not null,
  note             text,
  created_at       timestamptz not null default now()
);

-- v1: the schedule the surgeon asked for. PLACEHOLDER: the exact offsets are still being
-- confirmed. v1 stays editable until the first episode uses it (see the guard below).
insert into message_schedules (version, offsets_days, close_after_days, opens_at_local, timezone, note)
values (1, array[2, 3, 5, 7, 14, 21, 28, 35, 42], 7, time '07:00', 'America/Toronto',
        'Offsets pending confirmation with the surgeon.');

alter table message_schedules enable row level security;

create policy "signed-in users can read schedules" on message_schedules
  for select to authenticated using (true);

revoke insert, update, delete, truncate on message_schedules from authenticated, anon;

-- ---------------------------------------------------------------------------
-- Same-clinic guarantees: composite keys the new foreign keys point at
-- ---------------------------------------------------------------------------

alter table patients   add constraint patients_clinic_id_id_key   unique (clinic_id, id);
alter table procedures add constraint procedures_clinic_id_id_key unique (clinic_id, id);

-- ---------------------------------------------------------------------------
-- Episodes
-- ---------------------------------------------------------------------------
-- Like message_drafts: retained, and foreign keys deliberately have no ON DELETE CASCADE,
-- so deleting a clinic or patient that has an episode is refused rather than silently
-- erasing the record. (supabase/scripts/reset_trial_data.sh uses TRUNCATE.)

create table episodes (
  id                    uuid primary key default gen_random_uuid(),
  clinic_id             uuid not null references clinics (id),
  patient_id            uuid not null,
  procedure_id          uuid not null,
  surgery_date          date not null,
  schedule_version      integer not null references message_schedules (version),
  checkin_completed_at  timestamptz,
  -- Bumped to revoke the current patient link; the link token carries the number it was
  -- issued under (see core/security.py, later PR).
  link_generation       integer not null default 1 check (link_generation >= 1),
  closed_at             timestamptz,
  close_reason          text check (close_reason in ('discharged', 'schedule_complete')),
  closed_by             uuid references auth.users (id),
  created_by            uuid not null references auth.users (id),
  created_at            timestamptz not null default now(),

  foreign key (clinic_id, patient_id)   references patients (clinic_id, id),
  foreign key (clinic_id, procedure_id) references procedures (clinic_id, id),
  -- What messages and check-ins point at, so their clinic and patient can't disagree
  -- with their episode's.
  unique (id, clinic_id, patient_id),

  -- A discharge records who; a natural close records nobody.
  constraint episodes_close_consistent check (
    (closed_at is null and close_reason is null and closed_by is null)
    or (closed_at is not null and close_reason = 'discharged' and closed_by is not null)
    or (closed_at is not null and close_reason = 'schedule_complete' and closed_by is null)
  )
);

-- One open episode per patient.
create unique index episodes_one_open_per_patient on episodes (patient_id) where closed_at is null;
create index episodes_clinic_idx on episodes (clinic_id, created_at desc);

alter table episodes enable row level security;

create policy "clinic members can read their clinic's episodes" on episodes
  for select using (is_clinic_member(clinic_id));

revoke insert, update, delete, truncate on episodes from authenticated, anon;

create function episodes_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'episodes_retained: episodes are never deleted';
  end if;
  if old.closed_at is not null then
    raise exception 'episodes_closed: a closed episode cannot change';
  end if;
  if (new.id, new.clinic_id, new.patient_id, new.schedule_version, new.created_by, new.created_at)
     is distinct from
     (old.id, old.clinic_id, old.patient_id, old.schedule_version, old.created_by, old.created_at) then
    raise exception 'episodes_original: that field of an episode cannot change';
  end if;
  if new.link_generation < old.link_generation then
    raise exception 'episodes_link_generation: the link generation only goes up';
  end if;
  if old.checkin_completed_at is not null
     and new.checkin_completed_at is distinct from old.checkin_completed_at then
    raise exception 'episodes_checkin: the check-in time cannot change once set';
  end if;
  -- Correcting the surgery date or procedure moves every interval, so it is only allowed
  -- while nothing depends on them: before the check-in and before any scheduled message.
  if (new.surgery_date, new.procedure_id) is distinct from (old.surgery_date, old.procedure_id)
     and (old.checkin_completed_at is not null
          or exists (select 1 from messages m where m.episode_id = old.id and m.origin = 'scheduled')) then
    raise exception 'episodes_surgery_locked: the surgery date and procedure are fixed once the patient has checked in';
  end if;
  return new;
end;
$$;

create trigger episodes_guard
  before update or delete on episodes
  for each row execute function episodes_guard();

-- A schedule version is editable only until an episode uses it, and its timezone must be real.
create function message_schedules_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op <> 'INSERT' then
    if exists (select 1 from episodes where schedule_version = old.version) then
      raise exception 'message_schedules_in_use: a schedule version that episodes use cannot change; add a new version';
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

create trigger message_schedules_guard
  before insert or update or delete on message_schedules
  for each row execute function message_schedules_guard();

-- ---------------------------------------------------------------------------
-- Messages and check-ins belong to an episode
-- ---------------------------------------------------------------------------
-- origin:
--   scheduled -- the patient's one message for an interval (interval_no + category)
--   reply     -- the patient's one reply to a doctor message (reply_to_message_id)
--   clinician -- any message from the clinic
-- Messages with sender 'system' have no origin and are no longer allowed (none were ever
-- written).

alter table messages
  add column episode_id          uuid not null,
  add column origin              text not null check (origin in ('scheduled', 'reply', 'clinician')),
  add column interval_no         integer check (interval_no >= 1),
  -- A key, not a label: labels change, keys are stored. Which keys exist is the api's list.
  add column category            text check (category ~ '^[a-z][a-z0-9_]{0,63}$'),
  add column reply_to_message_id uuid;

alter table messages
  add constraint messages_episode_fk
    foreign key (episode_id, clinic_id, patient_id) references episodes (id, clinic_id, patient_id),
  add constraint messages_episode_id_id_key unique (episode_id, id),
  add constraint messages_reply_to_fk
    foreign key (episode_id, reply_to_message_id) references messages (episode_id, id),
  add constraint messages_origin_shape check (
    (origin = 'scheduled' and sender = 'patient'
       and interval_no is not null and category is not null and reply_to_message_id is null)
    or (origin = 'reply' and sender = 'patient'
       and interval_no is null and category is null and reply_to_message_id is not null)
    or (origin = 'clinician' and sender = 'clinician'
       and interval_no is null and category is null and reply_to_message_id is null)
  );

-- One message per interval; one reply per doctor message.
create unique index messages_one_per_interval
  on messages (episode_id, interval_no) where origin = 'scheduled';
create unique index messages_one_reply_per_message
  on messages (reply_to_message_id) where origin = 'reply';
create index messages_episode_idx on messages (episode_id, created_at);

-- A reply answers a doctor's message, not another patient message.
create function messages_reply_target_guard() returns trigger
language plpgsql
as $$
begin
  if new.origin = 'reply' and not exists (
    select 1 from messages m
    where m.id = new.reply_to_message_id and m.episode_id = new.episode_id and m.origin = 'clinician'
  ) then
    raise exception 'reply_target_invalid: a reply must answer a clinician message in the same episode';
  end if;
  return new;
end;
$$;

create trigger messages_reply_target_guard
  before insert on messages
  for each row execute function messages_reply_target_guard();

-- The only thing that ever changes on a message is that the clinic read it. The interval,
-- category and origin it was sent under are evidence.
create function messages_guard() returns trigger
language plpgsql
as $$
begin
  if (new.id, new.clinic_id, new.patient_id, new.episode_id, new.sender, new.body, new.origin,
      new.interval_no, new.category, new.reply_to_message_id, new.created_at)
     is distinct from
     (old.id, old.clinic_id, old.patient_id, old.episode_id, old.sender, old.body, old.origin,
      old.interval_no, old.category, old.reply_to_message_id, old.created_at) then
    raise exception 'messages_immutable: only read_at can change on a message';
  end if;
  return new;
end;
$$;

create trigger messages_guard
  before update on messages
  for each row execute function messages_guard();

-- One check-in per episode; completing it is what unlocks messaging.
alter table check_ins
  add column episode_id uuid not null,
  add constraint check_ins_episode_fk
    foreign key (episode_id, clinic_id, patient_id) references episodes (id, clinic_id, patient_id);

create unique index check_ins_one_per_episode on check_ins (episode_id);

-- ---------------------------------------------------------------------------
-- Close the direct-write paths (all writes go through apps/api, which audits them)
-- ---------------------------------------------------------------------------

drop policy "clinic members can send messages in their clinic" on messages;
drop policy "clinic members can manage their clinic's patients" on patients;
drop policy "clinic members can log touchpoints in their clinic" on touchpoints;

revoke insert, update, delete on messages    from authenticated, anon;
revoke insert, update, delete on patients    from authenticated, anon;
revoke insert, update, delete on touchpoints from authenticated, anon;

-- TRUNCATE is not subject to row-level security; migration 1 only revoked UPDATE and DELETE.
revoke truncate on audit_log from authenticated, anon;

-- ---------------------------------------------------------------------------
-- State changes: one transaction each, with the episode row locked
-- ---------------------------------------------------------------------------
-- Same pattern as decide_message_draft: the function locks the episode, checks what can
-- race, writes, and returns the rows. The api audits afterwards, like every other write.
-- Error codes are the exception messages (api/*.py maps them to HTTP statuses).

-- The patient completes the check-in: stores it and unlocks messaging, atomically.
create function complete_check_in(
  p_clinic_id    uuid,
  p_episode_id   uuid,
  p_post_op_day  integer,
  p_answers      jsonb,
  p_is_red_flag  boolean
) returns jsonb
language plpgsql
as $$
declare
  e episodes;
  c check_ins;
begin
  select * into e from episodes where id = p_episode_id and clinic_id = p_clinic_id for update;
  if not found then
    raise exception 'episode_not_found';
  end if;
  if e.closed_at is not null then
    raise exception 'episode_closed';
  end if;
  if e.checkin_completed_at is not null then
    raise exception 'check_in_already_done';
  end if;

  insert into check_ins (clinic_id, patient_id, episode_id, procedure_id, post_op_day, answers, is_red_flag)
  values (e.clinic_id, e.patient_id, e.id, e.procedure_id, p_post_op_day, p_answers, p_is_red_flag)
  returning * into c;

  update episodes set checkin_completed_at = c.created_at where id = e.id returning * into e;

  return jsonb_build_object('check_in', to_jsonb(c), 'episode', to_jsonb(e));
end;
$$;

-- A patient message. The api has already decided (from the schedule and the clock) which
-- interval is open and passes it in as p_interval_no; this function enforces what a race
-- could break.
--   p_origin 'scheduled': needs the check-in done, an interval number and a category, and
--             the interval unused.
--   p_origin 'reply':     answers the LATEST clinician message in the episode, if it has
--             no reply yet. (An older unanswered doctor message is superseded.) Allowed
--             before the check-in: the clinic started that conversation.
create function send_patient_message(
  p_clinic_id    uuid,
  p_episode_id   uuid,
  p_origin       text,
  p_interval_no  integer,
  p_category     text,
  p_body         text
) returns messages
language plpgsql
as $$
declare
  e episodes;
  target messages;
  m messages;
begin
  if p_origin not in ('scheduled', 'reply') then
    raise exception 'message_bad_origin';
  end if;
  if p_body is null or char_length(btrim(p_body)) = 0 or char_length(p_body) > 5000 then
    raise exception 'message_body_invalid';
  end if;

  select * into e from episodes where id = p_episode_id and clinic_id = p_clinic_id for update;
  if not found then
    raise exception 'episode_not_found';
  end if;
  if e.closed_at is not null then
    raise exception 'episode_closed';
  end if;

  if p_origin = 'scheduled' then
    if p_interval_no is null or p_category is null then
      raise exception 'message_bad_origin';
    end if;
    if e.checkin_completed_at is null then
      raise exception 'check_in_required';
    end if;
    if exists (select 1 from messages
               where episode_id = e.id and origin = 'scheduled' and interval_no = p_interval_no) then
      raise exception 'interval_already_used';
    end if;
    insert into messages (clinic_id, patient_id, episode_id, sender, body, origin, interval_no, category)
    values (e.clinic_id, e.patient_id, e.id, 'patient', p_body, 'scheduled', p_interval_no, p_category)
    returning * into m;
  else
    select * into target from messages
    where episode_id = e.id and origin = 'clinician'
    order by created_at desc, id desc
    limit 1;
    if not found
       or exists (select 1 from messages where origin = 'reply' and reply_to_message_id = target.id) then
      raise exception 'no_reply_available';
    end if;
    insert into messages (clinic_id, patient_id, episode_id, sender, body, origin, reply_to_message_id)
    values (e.clinic_id, e.patient_id, e.id, 'patient', p_body, 'reply', target.id)
    returning * into m;
  end if;

  return m;
end;
$$;

-- A clinician message, at any time, as long as the episode is open (a closed episode's
-- patient link is dead, so the message could never be seen).
create function send_clinician_message(
  p_clinic_id   uuid,
  p_episode_id  uuid,
  p_body        text
) returns messages
language plpgsql
as $$
declare
  e episodes;
  m messages;
begin
  if p_body is null or char_length(btrim(p_body)) = 0 or char_length(p_body) > 5000 then
    raise exception 'message_body_invalid';
  end if;
  select * into e from episodes where id = p_episode_id and clinic_id = p_clinic_id for update;
  if not found then
    raise exception 'episode_not_found';
  end if;
  if e.closed_at is not null then
    raise exception 'episode_closed';
  end if;
  insert into messages (clinic_id, patient_id, episode_id, sender, body, origin)
  values (e.clinic_id, e.patient_id, e.id, 'clinician', p_body, 'clinician')
  returning * into m;
  return m;
end;
$$;

-- Close an episode. Idempotent, so the lazy "settle" of a naturally ended episode is safe
-- to call from many requests: only the call that changes it reports changed = true, and
-- only that caller audits.
--   'discharged':         a clinician, effective now (p_closed_by required, p_closed_at ignored)
--   'schedule_complete':  no one; p_closed_at is the instant the last interval ended
--                         (computed by the api; must not be in the future)
create function close_episode(
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
  if p_reason not in ('discharged', 'schedule_complete') then
    raise exception 'episode_bad_reason';
  end if;
  if (p_reason = 'discharged') <> (p_closed_by is not null) then
    raise exception 'episode_bad_closer';
  end if;

  select * into e from episodes where id = p_episode_id and clinic_id = p_clinic_id for update;
  if not found then
    raise exception 'episode_not_found';
  end if;
  if e.closed_at is not null then
    return jsonb_build_object('episode', to_jsonb(e), 'changed', false);
  end if;

  at_time := case when p_reason = 'discharged' then now() else coalesce(p_closed_at, now()) end;
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

-- The clinician's decision on an AI draft (migration 6), now sending into the draft's
-- episode and refusing to send into a closed one. Rejecting still works on a closed episode.
create or replace function decide_message_draft(
  p_clinic_id  uuid,
  p_draft_id   uuid,
  p_decision   text,
  p_final_text text,
  p_decided_by uuid
) returns jsonb
language plpgsql
as $$
declare
  d message_drafts;
  m messages;
  e episodes;
  body text;
begin
  if p_decision not in ('approved', 'edited', 'rejected') then
    raise exception 'draft_bad_decision';
  end if;

  select * into d from message_drafts
  where id = p_draft_id and clinic_id = p_clinic_id
  for update;
  if not found then
    raise exception 'draft_not_found';
  end if;
  if d.status <> 'pending' then
    raise exception 'draft_not_pending';
  end if;

  if p_decision = 'rejected' then
    update message_drafts
       set status = 'rejected', decided_by = p_decided_by, decided_at = now()
     where id = d.id
    returning * into d;
    return jsonb_build_object('draft', to_jsonb(d), 'message', null);
  end if;

  if p_decision = 'approved' then
    body := d.draft_text;
  else
    if p_final_text is null or char_length(btrim(p_final_text)) = 0
       or p_final_text = d.draft_text then
      raise exception 'draft_edit_invalid';
    end if;
    body := p_final_text;
  end if;

  select ep.* into e from episodes ep
  join messages src on src.episode_id = ep.id
  where src.id = d.source_message_id
  for update of ep;
  if e.closed_at is not null then
    raise exception 'episode_closed';
  end if;

  insert into messages (clinic_id, patient_id, episode_id, sender, body, origin)
  values (d.clinic_id, d.patient_id, e.id, 'clinician', body, 'clinician')
  returning * into m;

  update message_drafts
     set status = p_decision, final_text = body, sent_message_id = m.id,
         decided_by = p_decided_by, decided_at = now()
   where id = d.id
  returning * into d;

  return jsonb_build_object('draft', to_jsonb(d), 'message', to_jsonb(m));
end;
$$;

-- Only the service role (apps/api) may call these.
revoke execute on function complete_check_in, send_patient_message, send_clinician_message,
  close_episode from public, anon, authenticated;
grant execute on function complete_check_in, send_patient_message, send_clinician_message,
  close_episode to service_role;
