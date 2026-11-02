# The app's own domain

| What | Address |
|---|---|
| Web app | `https://clinicallyanchored.ai` (`www.` redirects to it) |
| API | `https://api.clinicallyanchored.ai` |
| Email sender | `no-reply@clinicallyanchored.ai` (SES domain identity, `ca-central-1`) |

No address is hard-coded in the code. Each is an environment variable, set in the host's dashboard.
The code's only defaults are `localhost` ones for development.

## Where each address is configured

| Place | Setting | Where it is set |
|---|---|---|
| The browser's API base URL (`apps/web/src/lib/api.ts`) | `NEXT_PUBLIC_API_URL` | Vercel; baked in at build, so redeploy after changing it |
| The api's CORS allow-list (`main.py` via `core/config.py`) | `ALLOWED_ORIGINS` | Railway |
| Links in emails: invite sign-in, the guide (`core/mailer.py`) | `WEB_BASE_URL` | Railway |
| The single-use fallback link (`api/patient_accounts.py`) | `WEB_BASE_URL` | Railway |
| The email sender (`core/mailer.py`) | `SES_FROM_ADDRESS` (and optional `SES_FROM_NAME`) | Railway |
| The IAM policy's `ses:FromAddress` and the SES identity | the sender address | AWS console |
| Supabase Auth: Site URL and redirect URLs | dashboard settings | Supabase dashboard |

Supabase Auth redirect URLs: the code never asks Supabase for a redirect. Clinicians sign in with a
password, and every patient email goes through the api (the Send Email hook swallows Supabase's own
emails). The settings are still worth getting right, because Site URL is the fallback Supabase uses
for any link it builds. `supabase/config.toml` only configures the local stack (`127.0.0.1`); it does
not touch the hosted project.

Not changed on purpose: `@example.com` / `@example.org` addresses in tests and seed data (reserved
domains that no mail can reach), and the `https://example.org/sign-in` placeholder the `mailer` test
command uses when `WEB_BASE_URL` is unset.

## Order of changes

1. **AWS / SES**: finish verifying the `clinicallyanchored.ai` domain identity (DKIM shows
   "Successful"). In IAM, change the api user's policy to the new identity ARN
   (`identity/clinicallyanchored.ai`) and `ses:FromAddress` to `no-reply@clinicallyanchored.ai`
   (see [api/email.md](api/email.md)). Keep the old identity's permission until step 6 is done.
2. **Railway** (api service): set `ALLOWED_ORIGINS` to the new origin **and the old Vercel origin**,
   then `SES_FROM_ADDRESS` and `WEB_BASE_URL` (below). Railway redeploys. Setting `ALLOWED_ORIGINS`
   first means the old site keeps working and the new one can call the api.
3. **Vercel** (production): set `NEXT_PUBLIC_API_URL`, then redeploy (a rebuild, not a re-run of the
   same build: `NEXT_PUBLIC_*` values are read at build time).
4. **Supabase**: set Site URL and add the redirect URLs.
5. **Check**: open `https://clinicallyanchored.ai`, sign in as a clinician, load the dashboard (no CORS
   errors in the browser console), then send one `[TEST]` invite with
   `railway run python -m clinically_anchored_api.core.mailer --to <a verified address>`, and
   check the link in it opens `https://clinicallyanchored.ai/sign-in` and the "From" is the new sender.
6. **Later, when nothing uses the old addresses**: remove the old Vercel origin from
   `ALLOWED_ORIGINS` in Railway (see below), and the old redirect entries from Supabase.

## Values

Vercel (production environment):

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://api.clinicallyanchored.ai` |

`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` do not change.

Railway (api service):

| Variable | Value |
|---|---|
| `ALLOWED_ORIGINS` | `https://clinicallyanchored.ai,<the old https://….vercel.app origin>` |
| `WEB_BASE_URL` | `https://clinicallyanchored.ai` |
| `SES_FROM_ADDRESS` | `no-reply@clinicallyanchored.ai` |

Origins have a scheme and host only: no path, no trailing slash (a trailing slash is trimmed, but do
not rely on it). `www.clinicallyanchored.ai` does not belong in the list: it redirects before the page
loads, so the browser never calls the api from it. The old Railway address is an api address, not a
browser origin, so it is not in `ALLOWED_ORIGINS`; it keeps answering until the Railway service's
generated domain is removed.

Supabase (Authentication → URL Configuration):

| Setting | Value |
|---|---|
| Site URL | `https://clinicallyanchored.ai` |
| Redirect URLs | `https://clinicallyanchored.ai/**` (keep the old Vercel entry until step 6) |

## Removing the old addresses later

1. Railway: edit `ALLOWED_ORIGINS` to just `https://clinicallyanchored.ai`. The api then rejects the
   old Vercel origin's calls (CORS) after the redeploy.
2. Supabase: delete the old Vercel entry from Redirect URLs.
3. Vercel / Railway: remove the generated `*.vercel.app` and `*.railway.app` domains if you want them
   to stop answering. Emails already sent still contain links to the old web address, so leave the
   old Vercel domain up (or redirecting) until the last of those links has expired.
4. Preview deployments on Vercel have their own `*.vercel.app` origins; they are not in
   `ALLOWED_ORIGINS` unless added by hand.
