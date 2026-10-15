-- Message schedule v2: the offsets the surgeon confirmed, and an explicit rule for which
-- schedule a NEW episode gets.
--
-- Schedule v1 (migration 9) had placeholder offsets. The surgeon's confirmed schedule counts each
-- interval from the previous one: +48 h, +72 h, +5 days, then weekly. In days from the surgery
-- date, each opening at 07:00 America/Toronto:
--   intervals open on days 2, 5, 10, 17, 24, 31, 38, 45, 52; the episode closes on day 59
--   (interval 9 lasts one week).
-- v1 is NOT touched: episodes that recorded it keep computing with it.
--
-- Which schedule a new episode gets (api/episodes.py -> core/episodes.default_schedule): the
-- NEWEST ACTIVE version, i.e. the highest `version` with active = true. Before this migration
-- the rule was implicit ("the highest version"); `active` makes it a decision instead of a side
-- effect of inserting a row: stage a future version with active = false, review it, flip it on.
-- Existing episodes always keep the version they recorded (episodes.schedule_version).

alter table message_schedules
  add column active boolean not null default true;

comment on column message_schedules.active is
  'May be chosen for NEW episodes. New episodes get the highest active version. Turning it off '
  'never changes what existing episodes compute.';

-- A version that episodes use cannot change what it computes (offsets, close, opening time,
-- timezone, number), but whether new episodes may pick it, and its note, are not part of that:
-- retiring a version, or rolling a bad new one back, must stay possible.
create or replace function message_schedules_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op <> 'INSERT' then
    if exists (select 1 from episodes where schedule_version = old.version) then
      if tg_op = 'DELETE'
         or (new.version, new.offsets_days, new.close_after_days, new.opens_at_local, new.timezone)
            is distinct from
            (old.version, old.offsets_days, old.close_after_days, old.opens_at_local, old.timezone)
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

insert into message_schedules (version, offsets_days, close_after_days, opens_at_local, timezone, note)
values (2, array[2, 5, 10, 17, 24, 31, 38, 45, 52], 7, time '07:00', 'America/Toronto',
        'Confirmed by the surgeon: +48 h, +72 h, +5 days, then weekly; closes on day 59.');
