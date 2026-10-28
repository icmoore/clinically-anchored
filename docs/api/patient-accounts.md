# Patient accounts (passwordless email sign-in)

A patient signs in with their email address and a 6-digit code, and stays signed in on that device
for 30 days. No passwords. This replaces the patient link (`/care?token=...`), which keeps working
until the "retire the old links" change ships.

Parts: `core/mailer.py` + `docs/api/email.md` (sending), `core/gotrue.py` (Supabase Auth),
`core/patient_auth.py` (who is signed in), `api/patient_auth.py` (public sign-in routes),
`api/patient_first_sign_in.py` (surgery date, consent), `api/patient_accounts.py` (the clinic's
actions), `api/auth_hooks.py` (Supabase's email hook), migration 18.

## How it works

1. **Invite** (clinic): the patient's record needs an email. The api creates a Supabase Auth user
   (confirmed, `app_metadata.account_type = 'patient'`, set by the service role only) and a
   `patient_accounts` row (`invited`), and emails an invite that points at `<web>/sign-in`. Nothing
   in the invite is a credential.
2. **Code**: the patient enters their email. The api answers `{"ok": true}` at once whoever the email
   belongs to, then (only for an invited or active patient) makes a code with Supabase's admin
   `generate_link` (which returns the code and sends nothing) and mails it through SES. Limits: one
   code a minute and five an hour per email, silently; a code works 10 minutes and allows 5 tries
   (counted in the database before Supabase is asked); every failure to enter one is the same 401.
   Codes are stored nowhere (only a demo clinic's outbox row keeps its rendered email).
3. **First sign-in**: the patient confirms their surgery date (it must match their latest care
   period; three wrong dates lock the account until the clinic re-invites) and agrees to messaging
   and AI-assisted replies (stored in `consents` with the wording version). Then the account is
   `active` and the existing care screens work.
4. **Every request** (`core/patient_auth.py`): the token is checked with Supabase Auth (a session that
   was ended stops working at once; the check is remembered for 30 s), the user must be a patient
   user, the session must be younger than 30 days, and the account's status is read from the
   database. The patient, clinic and care period come from the account alone; no path, query or body
   parameter can name another patient. A request that carries `Authorization` is never answered from
   a `?token=` link.
5. **Turn off** (owner/clinician): the status becomes `off` (the api refuses the patient on their
   next request), the user is banned and all their sessions are deleted. **Re-invite** starts the
   first sign-in again.
6. **Fallback link** (owner/clinician; for a patient with no email): single use, 24 hours, shown once
   (only its hash is stored), and it asks for the surgery date every time it is opened.

Clinician routes refuse a patient token, and patient routes refuse a clinician token.

## What you set up

### Supabase dashboard (project `cdaswsjirkkdhvhegwco`)

1. **Authentication -> Sign In / Providers -> turn OFF "Allow new users to sign up".** Patients are
   created by the api, never by themselves. (Today anyone with the public anon key can create an Auth
   user; with RLS they can read nothing, but they should not exist.)
2. **Authentication -> Hooks -> Send Email -> add a hook: HTTPS**, URL
   `https://<your railway api>/auth-hooks/send-email`, and generate the secret. Copy the secret
   (`v1,whsec_...`) to Railway as `SEND_EMAIL_HOOK_SECRET`. Supabase then hands every email it would
   send to the api, which drops it. Without this, anyone can ask Supabase to email a code to a
   registered patient (the anon key is public).
3. **Authentication -> Sign In / Providers -> Email: "Email OTP Expiration" = 600** seconds and
   "Email OTP Length" = 6. (The api also enforces 10 minutes itself. The project defaulted to 8 digits,
   which `gotrue_check` reports; the api accepts 6 to 10 so a mis-set length does not lock patients
   out, but the emails and screens are written for 6.)
4. **Authentication -> Rate Limits -> "Rate limit for token verifications"**: the api checks codes
   from one server address, and Supabase's default (30 per 5 minutes per address) is shared by every
   patient. Raise it to something like 300.
5. "Confirm email" and the email templates do not matter: the api creates confirmed users and sends no
   Supabase mail. "Secure email change" is not used.

### Railway (api service)

| Variable | Value |
|---|---|
| `SUPABASE_ANON_KEY` | the public anon key (same as the web app's `NEXT_PUBLIC_SUPABASE_ANON_KEY`) |
| `SEND_EMAIL_HOOK_SECRET` | from step 2 above |
| `TRUSTED_PROXY_HOPS` | `1` (Railway's edge is one proxy). Without it every patient shares one per-address sign-in budget (30 a minute) |
| `SES_*`, `WEB_BASE_URL` | see [email.md](email.md) |

Nothing else changes: no new Procfile flags.

### Check it against the real project

The tests use a stand-in for Supabase Auth, so they cannot show that the real service behaves the way
`core/gotrue.py` assumes. After the dashboard and Railway steps (and with migration 18 applied), run
this once. It makes one throwaway Auth user (an address at `no-email.invalid`), walks through code ->
session -> token -> end sessions -> ban -> un-ban, prints PASS or FAIL for each step and deletes the user:

```bash
railway run python -m clinically_anchored_api.core.gotrue_check
```

## Rules the database enforces (migration 18)

- A patient's Supabase user reads **nothing** directly from any table and writes nothing (every table
  has row-level security, every policy goes through `is_clinic_member`). The one policy a patient
  could have used, `message_schedules`' `using (true)`, is now staff-only.
- One account per patient; one live (not `off`) account per Auth user; a user is never both staff and
  a patient (two triggers); the email of an active account cannot change.
- The code/identity counters and the link redemption are atomic functions (service role only).
