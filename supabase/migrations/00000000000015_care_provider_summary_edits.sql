-- Physician edits to an AI care provider summary.
--
-- A care provider summary (migration 14) is exactly what the AI wrote and is never changed. When
-- the physician edits the text before passing it on, the edited version is saved here as a new row
-- that names the summary it came from, so the record always shows both: what the AI wrote, and
-- what the clinic changed it to (the same idea as an edited message draft, which keeps the AI's
-- text and the text that was sent). Every save is its own row, so an edit of an edit keeps the
-- earlier ones. The newest row for a summary is the one shown.
--
-- Each row is audited as `care_provider_summary.edited` (the audit row holds only a hash); `id` is
-- the edit_id the audit event names (`metadata.ref_id`). Same rules as the summaries themselves:
-- never edited or deleted (trigger), reads for clinic members (RLS), writes by apps/api only
-- (service role), after the audit write. No ON DELETE CASCADE; supabase/scripts/reset_trial_data.sh
-- wipes this table with the rest (TRUNCATE).

-- So an edit can name its summary AND prove it is the same clinic's and the same patient's.
alter table care_provider_summaries
  add constraint care_provider_summaries_scope_key unique (id, clinic_id, patient_id);

create table care_provider_summary_edits (
  id            uuid primary key,
  clinic_id     uuid not null references clinics (id),
  patient_id    uuid not null,
  summary_id    uuid not null,
  edited_text   text not null,
  edited_by     uuid not null references auth.users (id),
  edited_at     timestamptz not null,
  foreign key (clinic_id, patient_id) references patients (clinic_id, id),
  foreign key (summary_id, clinic_id, patient_id)
    references care_provider_summaries (id, clinic_id, patient_id)
);

-- "The newest edit of this summary."
create index care_provider_summary_edits_summary_idx
  on care_provider_summary_edits (summary_id, edited_at desc);

alter table care_provider_summary_edits enable row level security;

create policy "clinic members can read their clinic's care provider summary edits"
  on care_provider_summary_edits
  for select using (is_clinic_member(clinic_id));

revoke insert, update, delete, truncate on care_provider_summary_edits from authenticated, anon;

create function care_provider_summary_edits_guard() returns trigger
language plpgsql
as $$
begin
  raise exception 'care_provider_summary_edits_retained: a saved edit is never changed or deleted';
end;
$$;

create trigger care_provider_summary_edits_guard
  before update or delete on care_provider_summary_edits
  for each row execute function care_provider_summary_edits_guard();
