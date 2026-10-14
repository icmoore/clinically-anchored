-- Saved AI thread summaries.
--
-- A summary is generated on demand (api/summaries.py), shown to a clinician, and audited as
-- `summary.generated` (the audit row holds only a hash). Until now it lived only on the page, so
-- opening a patient again meant asking the model again. Each generated summary is now kept here,
-- and the newest one for a patient is shown when their file is opened.
--
-- One row per generation, never edited or deleted (like message_drafts): this is what a clinician
-- was shown, so it is also the record the audit hash can be checked against. `id` is the
-- summary_id the audit event names (`metadata.ref_id`), so reconcile can tie row to event. The
-- citations in `lines` are message ids; messages are never deleted, so they stay resolvable.
--
-- Reads: clinic members (RLS). Writes: apps/api only (service role), after the audit write.
-- Foreign keys deliberately have no ON DELETE CASCADE; supabase/scripts/reset_trial_data.sh
-- wipes this table with the rest (TRUNCATE).

create table thread_summaries (
  id              uuid primary key,
  clinic_id       uuid not null references clinics (id),
  patient_id      uuid not null,
  lines           jsonb not null,            -- [{"text": ..., "citations": [message id, ...]}]
  covers_messages integer not null check (covers_messages >= 0),
  truncated       boolean not null,          -- older messages were left out
  model_id        text not null,
  prompt_version  text not null,
  generated_by    uuid not null references auth.users (id),
  generated_at    timestamptz not null,
  foreign key (clinic_id, patient_id) references patients (clinic_id, id)
);

-- "The newest summary for this patient."
create index thread_summaries_patient_idx on thread_summaries (patient_id, generated_at desc);

alter table thread_summaries enable row level security;

create policy "clinic members can read their clinic's summaries" on thread_summaries
  for select using (is_clinic_member(clinic_id));

revoke insert, update, delete, truncate on thread_summaries from authenticated, anon;

create function thread_summaries_guard() returns trigger
language plpgsql
as $$
begin
  raise exception 'thread_summaries_retained: a saved summary is never changed or deleted';
end;
$$;

create trigger thread_summaries_guard
  before update or delete on thread_summaries
  for each row execute function thread_summaries_guard();
