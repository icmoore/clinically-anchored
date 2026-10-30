-- The clinic's patient list, one page at a time (api: GET /clinics/{id}/patients).
--
-- The list used to load every patient, every episode and every account status and sort and
-- filter them in Python. Now the database filters, searches, sorts, counts and pages, and the
-- api computes episode state (core/schedule.py) only for the patients on the page it gets back.
--
-- "Active" vs "Ended" has to be decided here too, so two helpers restate when a care period
-- ends. They MUST agree with core/schedule.py (`episode_end`, and `episode_state` closing an
-- episode that was discharged, settled, or whose end has passed); tests/db/test_patient_list.py
-- compares them for every state the trial seed makes and one second either side of each end.
--   * discharged, or already settled (closed_at is set): ended
--   * otherwise ended once `now` reaches the schedule's end: the surgery DATE plus the schedule's
--     last offset plus its close_after_days, at opens_at_local (07:00) in the schedule's zone
--     (a follow-up extension counts from the local date it was accepted instead of the surgery
--     date). Wall-clock arithmetic, so a clock change inside the period moves nothing.
-- The schedule version is the one the episode recorded, so a schedule change never moves it.

create function episode_schedule_end(
  p_kind              text,
  p_surgery_date      date,
  p_started_at        timestamptz,
  p_schedule_version  integer
) returns timestamptz
language sql
stable
as $$
  select (
           (case when p_kind = 'extension'
                 then (p_started_at at time zone s.timezone)::date
                 else p_surgery_date end)
           + (s.offsets_days[cardinality(s.offsets_days)] + s.close_after_days)
           + s.opens_at_local
         ) at time zone s.timezone
  from message_schedules s
  where s.version = p_schedule_version
$$;

create function episode_is_ended(
  p_closed_at         timestamptz,
  p_kind              text,
  p_surgery_date      date,
  p_started_at        timestamptz,
  p_schedule_version  integer,
  p_now               timestamptz
) returns boolean
language sql
stable
as $$
  select p_closed_at is not null
         or p_now >= episode_schedule_end(p_kind, p_surgery_date, p_started_at, p_schedule_version)
$$;

-- One page of a clinic's patients, each with their latest episode (newest created_at) and their
-- sign-in account status.
--   p_status        'active' (the latest care period is still running), 'ended' (discharged or
--                   run out), 'all' (also patients with no care period at all)
--   p_surgery_from  inclusive dates on the latest episode's surgery date; null = no bound. A patient
--   p_surgery_to    with no care period never matches a bound.
--   p_q             name search: case-insensitive, partial; % and _ are plain characters
--   p_limit         1..50, p_offset >= 0
-- Order: newest surgery first, then name; patients with no care period last.
-- Returns {"total": <matches before paging>, "items": [{patient columns, account_status,
-- episode: <the episode row or null>}]}. `p_now` is passed in so the api's clock rules.
-- Callers check clinic membership; every table here is filtered by p_clinic_id.
create function list_patients_page(
  p_clinic_id     uuid,
  p_now           timestamptz,
  p_status        text    default 'active',
  p_surgery_from  date    default null,
  p_surgery_to    date    default null,
  p_q             text    default null,
  p_limit         integer default 5,
  p_offset        integer default 0
) returns jsonb
language plpgsql
stable
as $$
declare
  pattern text;
  out jsonb;
begin
  if p_status not in ('active', 'ended', 'all') then
    raise exception 'patient_list_bad_status';
  end if;
  if p_limit is null or p_limit < 1 or p_limit > 50 or p_offset is null or p_offset < 0 then
    raise exception 'patient_list_bad_page';
  end if;
  if p_surgery_from is not null and p_surgery_to is not null and p_surgery_from > p_surgery_to then
    raise exception 'patient_list_bad_dates';
  end if;
  if nullif(btrim(p_q), '') is not null then
    pattern := '%' || replace(replace(replace(btrim(p_q), '\', '\\'), '%', '\%'), '_', '\_') || '%';
  end if;

  with latest as (
    select p.id, p.full_name, p.contact, p.email, p.created_at,
           e.id as ep_id, e.surgery_date, e.closed_at, e.kind, e.started_at, e.schedule_version,
           e.ep_json as ep_row
    from patients p
    left join lateral (
      select x.id, x.surgery_date, x.closed_at, x.kind, x.started_at, x.schedule_version,
             to_jsonb(x) as ep_json
      from episodes x
      where x.patient_id = p.id and x.clinic_id = p.clinic_id
      order by x.created_at desc, x.id desc
      limit 1
    ) e on true
    where p.clinic_id = p_clinic_id
      and (pattern is null or p.full_name ilike pattern escape '\')
  ),
  matched as (
    select l.*
    from latest l
    where case p_status
            when 'all' then true
            when 'active' then l.ep_id is not null and not episode_is_ended(
              l.closed_at, l.kind, l.surgery_date, l.started_at, l.schedule_version, p_now)
            else l.ep_id is not null and episode_is_ended(
              l.closed_at, l.kind, l.surgery_date, l.started_at, l.schedule_version, p_now)
          end
      and (p_surgery_from is null or l.surgery_date >= p_surgery_from)
      and (p_surgery_to is null or l.surgery_date <= p_surgery_to)
  ),
  page as (
    select * from matched
    order by surgery_date desc nulls last, lower(full_name), full_name, id
    limit p_limit offset p_offset
  )
  select jsonb_build_object(
           'total', (select count(*) from matched),
           'items', coalesce((
             select jsonb_agg(
                      jsonb_build_object(
                        'id', pg.id,
                        'full_name', pg.full_name,
                        'contact', pg.contact,
                        'email', pg.email,
                        'created_at', pg.created_at,
                        'account_status', coalesce(a.status, 'none'),
                        'episode', pg.ep_row)
                      order by pg.surgery_date desc nulls last, lower(pg.full_name),
                               pg.full_name, pg.id)
             from page pg
             left join patient_accounts a
               on a.patient_id = pg.id and a.clinic_id = p_clinic_id
           ), '[]'::jsonb))
  into out;
  return out;
end;
$$;

-- Finding a patient's latest episode is now one indexed lookup per patient on the page's way in
-- (episodes_clinic_idx leads with clinic_id, and the one-open-episode index is partial).
create index episodes_patient_latest_idx on episodes (patient_id, created_at desc, id desc);

-- Only the service role (apps/api) may call these.
revoke execute on function episode_schedule_end, episode_is_ended, list_patients_page
  from public, anon, authenticated;
grant execute on function episode_schedule_end, episode_is_ended, list_patients_page
  to service_role;
