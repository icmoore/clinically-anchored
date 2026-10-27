-- Patient accounts, part A: sending email.
--
-- Two things:
--
--   1. clinics.is_demo. A demo clinic never sends real email: the api stores the rendered email
--      (sign-in code included) in the outbox and shows it on a "Demo inbox" page instead. The flag
--      is set ONLY by SQL, by someone with the table owner's rights (psql as postgres). The api's
--      role (service_role) is given no privilege on the column, so no route, script or seed can
--      set or change it, even by mistake. Column grants only work once the table-level grant is
--      gone, hence the revoke first.
--
--   2. email_outbox: one row per email the api tried to send. It holds who it went to, which
--      template, for which clinic and patient, whether it was sent, and the provider's message id.
--      It does NOT hold the email body, and never a sign-in code -- except for a demo clinic,
--      whose rows have status 'demo' and carry the rendered email, because that is the only copy
--      there is (nothing was sent). A row is written 'queued' before the send and settled once
--      ('sent' or 'failed'); 'refused' (reserved test domain, bad address) and 'demo' are final
--      at birth. Rows are never deleted. Nothing authenticated can read or write the table: the
--      api reads it for the owner-only demo inbox.

-- ---------------------------------------------------------------------------
-- Demo clinics
-- ---------------------------------------------------------------------------

alter table clinics add column is_demo boolean not null default false;

-- The seeded demo clinic (apps/api seed_trial_patients.py, "demo"). A no-op where it doesn't exist.
update clinics set is_demo = true where id = '8a757d96-b69e-46c3-9693-7b5e604ed809';

revoke insert, update on clinics from anon, authenticated, service_role;
grant insert (id, name, office_phone) on clinics to service_role;
grant update (name, office_phone) on clinics to service_role;

-- ---------------------------------------------------------------------------
-- Outbox
-- ---------------------------------------------------------------------------

create table email_outbox (
  id                   uuid primary key default gen_random_uuid(),
  clinic_id            uuid not null references clinics (id),
  patient_id           uuid,
  recipient            text not null check (char_length(recipient) between 3 and 320),
  template             text not null check (template in ('invite', 'sign_in_code')),
  status               text not null check (status in ('queued', 'sent', 'failed', 'demo', 'refused')),
  provider_message_id  text,
  -- A short reason, never a provider message: an error code ('MessageRejected'), or why we
  -- refused ('reserved_domain').
  error                text check (error is null or char_length(error) <= 200),
  -- Demo clinics only (status 'demo'): the email as it would have been sent.
  rendered_subject     text,
  rendered_text        text,
  rendered_html        text,
  created_at           timestamptz not null default now(),
  sent_at              timestamptz,

  foreign key (clinic_id, patient_id) references patients (clinic_id, id),

  constraint email_outbox_content_only_in_demo check (
    (status = 'demo' and rendered_subject is not null and rendered_text is not null
       and rendered_html is not null)
    or (status <> 'demo' and rendered_subject is null and rendered_text is null
       and rendered_html is null)
  ),
  constraint email_outbox_sent_consistent check (
    (status = 'sent') = (sent_at is not null)
    and (provider_message_id is null or status = 'sent')
  ),
  constraint email_outbox_error_consistent check (
    (status in ('failed', 'refused')) = (error is not null)
  )
);

create index email_outbox_clinic_idx on email_outbox (clinic_id, created_at desc);
-- "How many sign-in codes did this address get lately" (rate limits, a later PR).
create index email_outbox_recipient_idx on email_outbox (lower(recipient), created_at desc);

alter table email_outbox enable row level security;
-- No policy: no authenticated user reads or writes it. Only the api (service role) does.
revoke all on email_outbox from anon, authenticated;

create function email_outbox_guard() returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'email_outbox_retained';
  end if;
  -- Only a queued row changes, and once: to sent or failed.
  if old.status <> 'queued' then
    raise exception 'email_outbox_settled';
  end if;
  if new.status not in ('sent', 'failed') then
    raise exception 'email_outbox_settled';
  end if;
  if (new.id, new.clinic_id, new.patient_id, new.recipient, new.template, new.created_at,
      new.rendered_subject, new.rendered_text, new.rendered_html)
     is distinct from
     (old.id, old.clinic_id, old.patient_id, old.recipient, old.template, old.created_at,
      old.rendered_subject, old.rendered_text, old.rendered_html) then
    raise exception 'email_outbox_immutable';
  end if;
  return new;
end;
$$;

create trigger email_outbox_guard
  before update or delete on email_outbox
  for each row execute function email_outbox_guard();
