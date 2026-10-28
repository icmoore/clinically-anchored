# Email (Amazon SES)

The api sends the patient invite and the sign-in code through Amazon SES in `ca-central-1`
(`core/mailer.py`; `AWS_REGION` is ignored, and only the optional `SES_REGION` setting can move email
to another region -- see "Using another region" below). Supabase Auth never
sends mail. Every attempt is recorded in `email_outbox` (migration 17): recipient, template,
clinic, patient, status, SES message id, timestamps. The outbox does not keep the email body or
a sign-in code, with one exception: a **demo clinic** sends nothing and keeps the rendered email so
the owner can read it on the Demo inbox page.

All wording is in `core/email_templates.py` (placeholder wording). It contains the clinic's name and
office phone and nothing clinical.

## Set up SES (once)

All in the AWS console, **region Canada (Central) `ca-central-1`** (check the region picker top
right on every page: SES identities are per region).

1. **Verify the sender.** SES -> Configuration -> Identities -> Create identity.
   - *Domain (preferred):* enter the domain, keep Easy DKIM (RSA_2048), create. Add the three CNAME
     records it shows at your DNS provider. Wait until the identity says **Verified** (minutes to
     hours). Any address at the domain can then send.
   - *Email address (fastest for a trial):* enter the address, create, click the link in the
     verification email. Only that exact address can send.
2. **Sandbox.** A new SES account is in the sandbox: it only delivers to verified identities, with
   low limits. To test, also create an identity for your own receiving address and verify it the
   same way.
3. **Allow the api's AWS user to send.** The Railway service already has `AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY` for Bedrock. In IAM, add this policy to that same user (fill in the
   account id and the identity you verified; for a domain use `identity/your-domain.ca` and the
   `ses:FromAddress` below):

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": "ses:SendEmail",
       "Resource": "arn:aws:ses:ca-central-1:<ACCOUNT_ID>:identity/<your-identity>",
       "Condition": {"StringEquals": {"ses:FromAddress": "no-reply@your-domain.ca"}}
     }]
   }
   ```

   If you later add an SES configuration set, add its ARN to `Resource` too. If a send in the
   sandbox is denied, add the recipient identity's ARN as well.
4. **Set the env vars on Railway** (the api service):

   | Variable | Value |
   |---|---|
   | `SES_FROM_ADDRESS` | the verified sender, e.g. `no-reply@your-domain.ca` (required to send) |
   | `SES_FROM_NAME` | optional display name, e.g. `Your Surgeon's Office` |
   | `SES_CONFIGURATION_SET` | optional; leave unset for now |
   | `WEB_BASE_URL` | the web app's address (already used by patient links); the invite points at `<it>/sign-in` |

   Until `SES_FROM_ADDRESS` is set a send is recorded as `failed` with error `ses_not_configured`;
   the service still boots.
5. **Check it works** (sends one `[TEST]` invite; touches no database). From `apps/api`, with the
   Railway variables loaded:

   ```bash
   railway run python -m clinically_anchored_api.core.mailer --to you@your-verified-address.ca
   ```

   `--dry-run` prints the email without sending. It prints SES's message id on success and the SES
   error code on failure (`MessageRejected`, `AccessDenied`, ...).

## Production access (when real patients need it)

The sandbox cannot send to patients. When you are ready: SES -> Account dashboard -> **Request
production access**. Choose *Transactional*, give the website URL, and describe the use: sign-in
codes and invitations only, no marketing, one email per request, no clinical content. AWS also asks
how you handle bounces and complaints: say you will add a configuration set with an SNS event
destination before sending to real patients (not built yet). AWS usually answers within a day.
While the request is pending, keep using verified test addresses.

## Using another region (temporary)

Canada is the default and the rule: nothing here depends on it, but data residency was the
reason. If SES cannot be used in `ca-central-1` yet (for example the account's organization blocks
the region), set `SES_REGION=us-east-2` (or another region) on Railway. Only EMAIL moves: Bedrock
stays fixed to Canada. The api logs a warning when it sends from another region. To go back, delete
the variable. Then redo, in Canada: verify the identities again (they are per region) and request
production access again if you had in the other region. The IAM permission is not per region.
Emails carry the clinic's name, its office phone and a code, no clinical content.

Do the steps above in the region you chose (the console's region picker, top right).

If the console says an organization policy (SCP) denies `ses:*`, that is set in the organization's
**management account** (Organizations -> Policies -> Service control policies). A member account
cannot read or change it.

## Demo clinics and test addresses

- `clinics.is_demo` makes a clinic send nothing. It can be set **only with SQL as the table owner**
  (the api's database role has no privilege on that column, so no route or script can change it):

  ```sql
  update clinics set is_demo = true where id = '<clinic id>';
  ```

  Migration 17 already does this for the seeded demo clinic.
- Real email is refused, in every clinic, to the reserved test domains `example.com`, `example.org`,
  `example.net` (and their subdomains) and `*.test`, `*.invalid`, `*.example`. The outbox row is
  `refused` with error `reserved_domain`. A demo clinic is not affected: it sends nothing.
- `GET /clinics/{id}/demo-inbox` (owner of a demo clinic only; 403 for other members, 404 for an
  ordinary clinic) returns the stored emails, newest first. The web page comes in the screens PR.
