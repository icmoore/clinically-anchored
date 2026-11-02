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

DNS is at Cloudflare. For every record below, set the proxy status to **DNS only** (grey cloud):
Vercel and Railway issue their own HTTPS certificates, and the SES DKIM records must not be proxied.

### Web, API and Supabase (done 2026-11-02)

1. **DNS and domains**
   - Vercel (project -> Settings -> Domains): add `clinicallyanchored.ai` (Production) and
     `www.clinicallyanchored.ai` (redirect to the apex). Untick "Redirect apex domains to www". Create
     the two `CNAME` records Vercel shows (`@` and `www`, both to the project's `…vercel-dns-….com`
     target) and wait for "Valid Configuration".
   - Railway (api service -> Settings -> Networking -> Custom Domain): add
     `api.clinicallyanchored.ai` and create the `CNAME` (and `TXT`, if shown) Railway gives you.
     Check `https://api.clinicallyanchored.ai/health` answers.
2. **Railway** (api service): set `ALLOWED_ORIGINS` to the new origin **and the old Vercel origin**,
   and `WEB_BASE_URL` (below). Railway redeploys. Do this before step 3: until the new origin is in
   `ALLOWED_ORIGINS`, the browser blocks the new site's calls to the api.
3. **Vercel** (production): set `NEXT_PUBLIC_API_URL`, then redeploy without the build cache (a
   rebuild, not a re-run of the same build: `NEXT_PUBLIC_*` values are read at build time).
4. **Supabase**: set Site URL and add the redirect URLs.
5. **Check**: open `https://clinicallyanchored.ai`, sign in as a clinician and load the dashboard (no
   CORS errors in the browser console).

### Email sender (separate; do before real patients use the app)

Until this is done, `SES_FROM_ADDRESS` stays on its current address and email keeps working as before.
But a sender at a `gmail.com` (or any domain we do not control) address cannot pass authentication
when Amazon SES sends it: Gmail shows "this message appears to be sent from your account but Gmail
couldn't verify this", and real recipients' providers may send it to spam or reject it. Sending as
our own domain, with DKIM, fixes that.

1. **SES** (region `ca-central-1`): create the `clinicallyanchored.ai` domain identity with Easy DKIM
   (RSA_2048). Add the three `CNAME` records it shows in Cloudflare (DNS only). Wait until the identity
   says **Verified**.
2. **DMARC** (recommended): add a `TXT` record in Cloudflare, name `_dmarc`, value
   `v=DMARC1; p=none; rua=mailto:<an address someone reads>`. `p=none` only reports; it blocks nothing.
   Tighten it later once the reports look clean.
3. **IAM**: in the api user's `SES-Send` policy, change `Resource` to the new identity ARN
   (`arn:aws:ses:ca-central-1:<ACCOUNT_ID>:identity/clinicallyanchored.ai`) and add the `ses:FromAddress`
   condition (see [api/email.md](api/email.md)). Keep the old sender's identity ARN (and its
   `ses:FromAddress`) in the policy until step 5 passes: with it removed and `SES_FROM_ADDRESS` not yet
   switched, sends are denied.
4. **Railway**: set `SES_FROM_ADDRESS` = `no-reply@clinicallyanchored.ai`.
5. **Check**: from `apps/api` run
   `railway run uv run python -m clinically_anchored_api.core.mailer --to <a verified address>`
   (`--dry-run` prints without sending). `railway run` runs on your computer, so use `uv run`; plain
   `python` is usually not installed. The email's "From" is the new sender, its links open
   `https://clinicallyanchored.ai/...`, and Gmail shows no warning banner.
6. **Production access**: a new SES account is in the sandbox and only delivers to verified
   addresses. Request production access before patients who are not verified in SES need email
   (see [api/email.md](api/email.md)).

### Later, when nothing uses the old addresses

Remove the old Vercel origin from `ALLOWED_ORIGINS` in Railway and the old redirect entries from
Supabase (see the end of this page).

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
| `SES_FROM_ADDRESS` | `no-reply@clinicallyanchored.ai` (only once the SES domain identity is verified; see Email sender above) |

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
