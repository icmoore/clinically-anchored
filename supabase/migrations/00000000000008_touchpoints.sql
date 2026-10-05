-- Patient contact/time log (data-catalogue D11).
--
-- One row per touchpoint between the clinic and a patient: a call, a visit, an
-- email, or a message. Two things read it:
--   (a) the "contact log" card on the clinician dashboard, so whoever opens a
--       patient can see at a glance how much contact they have already had;
--   (b) later, the baseline/outcome metrics in decision 8 (fewer calls,
--       callbacks and follow-up visits). Those metrics are only believable if
--       the log was kept the same way before and after the tool is introduced,
--       which is why every row records how it got here (`source`) and who
--       logged it.
--
-- `source` says where a row came from:
--   manual -- a clinician or delegate logged a call/visit/email by hand
--             (`logged_by` is that person);
--   auto   -- the api wrote it as a side effect of a clinician message being
--             sent (a direct send or an approved/edited AI draft). `logged_by`
--             is null: nobody typed it in. Messages a patient sends are NOT
--             touchpoints -- decision 8 counts contact the clinic initiates.
-- Calendar import is a possible future `source`; it is not built, so the check
-- constraint below doesn't allow it yet.
--
-- `occurred_at` is when the contact happened (a manual entry can be backdated);
-- `created_at` is when the row was written. They differ for backdated entries.
--
-- Unlike message_drafts this is an operational log, not an evidentiary record:
-- rows cascade away with their clinic or patient. Every insert is still audited
-- by the api (`touchpoint.logged`). There are deliberately no update/delete
-- policies: a wrong entry is corrected by a follow-up entry, not an edit.
--
-- Reads and inserts: clinic members (RLS). apps/api writes with the service
-- role after checking clinic ownership; there is no patient-facing write path.

create table touchpoints (
  id                uuid primary key default gen_random_uuid(),
  clinic_id         uuid not null references clinics (id) on delete cascade,
  patient_id        uuid not null references patients (id) on delete cascade,
  kind              text not null check (kind in ('call', 'visit', 'email', 'message')),
  source            text not null check (source in ('auto', 'manual')),
  occurred_at       timestamptz not null default now(),
  duration_minutes  integer check (duration_minutes is null or duration_minutes > 0),
  note              text,
  logged_by         uuid references auth.users (id),  -- null for auto-logged rows
  created_at        timestamptz not null default now()
);

-- The dashboard reads one patient's log, newest first.
create index touchpoints_patient_occurred_idx
  on touchpoints (clinic_id, patient_id, occurred_at desc);

alter table touchpoints enable row level security;

create policy "clinic members can read their clinic's touchpoints" on touchpoints
  for select using (is_clinic_member(clinic_id));
create policy "clinic members can log touchpoints in their clinic" on touchpoints
  for insert with check (is_clinic_member(clinic_id));
