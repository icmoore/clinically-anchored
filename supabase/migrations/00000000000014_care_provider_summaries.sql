-- Saved AI care provider summaries.
--
-- A care provider summary is a short paragraph the physician asks the AI for, to pass on to the
-- patient's other care provider. It is generated on demand (api/care_provider_summaries.py) from
-- the patient's current episode: the check-in and its answers, the message thread and the
-- calls/visits log. It is shown in the Physician Summary box, audited as
-- `care_provider_summary.generated` (the audit row holds only a hash), and kept here so opening
-- the patient again shows the newest one without calling the model.
--
-- Why a table of its own (and not patient_briefs, migration 13): a brief is three labelled bullets
-- with verified citations; this is one plain paragraph with none. Sharing the table would mean
-- either a kind column on every read or a nullable bullets column.
--
-- Same rules as patient_briefs: one row per generation, never edited or deleted (this is what the
-- clinician was shown, so it is the record the audit hash can be checked against); `id` is the
-- summary_id the audit event names (`metadata.ref_id`); reads for clinic members (RLS); writes by
-- apps/api only (service role), after the audit write. Foreign keys have no ON DELETE CASCADE;
-- supabase/scripts/reset_trial_data.sh wipes this table with the rest (TRUNCATE).

create table care_provider_summaries (
  id              uuid primary key,
  clinic_id       uuid not null references clinics (id),
  patient_id      uuid not null,
  episode_id      uuid not null,
  summary_text    text not null,
  covers          jsonb not null,            -- {"messages": n, "check_ins": n, "touchpoints": n} the model was shown
  truncated       boolean not null,          -- older messages or contacts were left out
  model_id        text not null,
  prompt_version  text not null,
  generated_by    uuid not null references auth.users (id),
  generated_at    timestamptz not null,
  foreign key (clinic_id, patient_id) references patients (clinic_id, id),
  foreign key (episode_id, clinic_id, patient_id) references episodes (id, clinic_id, patient_id)
);

-- "The newest care provider summary for this patient."
create index care_provider_summaries_patient_idx
  on care_provider_summaries (patient_id, generated_at desc);

alter table care_provider_summaries enable row level security;

create policy "clinic members can read their clinic's care provider summaries"
  on care_provider_summaries
  for select using (is_clinic_member(clinic_id));

revoke insert, update, delete, truncate on care_provider_summaries from authenticated, anon;

create function care_provider_summaries_guard() returns trigger
language plpgsql
as $$
begin
  raise exception 'care_provider_summaries_retained: a saved care provider summary is never changed or deleted';
end;
$$;

create trigger care_provider_summaries_guard
  before update or delete on care_provider_summaries
  for each row execute function care_provider_summaries_guard();
