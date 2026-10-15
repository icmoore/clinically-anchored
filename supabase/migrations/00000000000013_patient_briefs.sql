-- Saved AI patient summaries ("patient briefs").
--
-- A brief is generated on demand (api/briefs.py) from the patient's current episode: the check-in
-- and its answers, the message thread and the calls/visits log. It is shown in the Physician
-- Summary box, audited as `patient_brief.generated` (the audit row holds only a hash), and kept
-- here so opening the patient again shows the newest one without calling the model.
--
-- Why a table of its own and not a `kind` column on thread_summaries (migration 10): a thread
-- summary is a list of lines citing MESSAGES, with a message count and a "truncated" flag. A
-- brief is three labelled bullets citing messages, check-ins AND contact-log entries, tied to an
-- episode. Sharing the table would mean renaming it, making its columns nullable or
-- double-purpose, and a kind filter on every read; two small tables with the same rules are
-- clearer.
--
-- Same rules as thread_summaries: one row per generation, never edited or deleted (this is what a
-- clinician was shown, so it is the record the audit hash can be checked against); `id` is the
-- brief_id the audit event names (`metadata.ref_id`); reads for clinic members (RLS); writes by
-- apps/api only (service role), after the audit write. Cited rows (messages, check-ins,
-- touchpoints) are never deleted, so the citations stay resolvable. Foreign keys have no ON DELETE
-- CASCADE; supabase/scripts/reset_trial_data.sh wipes this table with the rest (TRUNCATE).

create table patient_briefs (
  id              uuid primary key,
  clinic_id       uuid not null references clinics (id),
  patient_id      uuid not null,
  episode_id      uuid not null,
  -- [{"label": "The picture now", "text": ..., "sources": [{"kind": "message"|"check_in"|"touchpoint", "id": ...}]}]
  bullets         jsonb not null,
  covers          jsonb not null,            -- {"messages": n, "check_ins": n, "touchpoints": n} the model was shown
  truncated       boolean not null,          -- older messages or contacts were left out
  model_id        text not null,
  prompt_version  text not null,
  generated_by    uuid not null references auth.users (id),
  generated_at    timestamptz not null,
  foreign key (clinic_id, patient_id) references patients (clinic_id, id),
  foreign key (episode_id, clinic_id, patient_id) references episodes (id, clinic_id, patient_id)
);

-- "The newest brief for this patient."
create index patient_briefs_patient_idx on patient_briefs (patient_id, generated_at desc);

alter table patient_briefs enable row level security;

create policy "clinic members can read their clinic's patient briefs" on patient_briefs
  for select using (is_clinic_member(clinic_id));

revoke insert, update, delete, truncate on patient_briefs from authenticated, anon;

create function patient_briefs_guard() returns trigger
language plpgsql
as $$
begin
  raise exception 'patient_briefs_retained: a saved patient brief is never changed or deleted';
end;
$$;

create trigger patient_briefs_guard
  before update or delete on patient_briefs
  for each row execute function patient_briefs_guard();
