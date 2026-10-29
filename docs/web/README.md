# web — service brief

Read this before touching `apps/web`. It's the product context a fresh thread needs;
`apps/web/README.md` has the dev-loop commands.

## What this app owns

`apps/web` (Next.js, TypeScript, deployed to Vercel) is the only user-facing surface.
Two distinct audiences, likely two distinct flows:

- **The clinician dashboard.** The card-based view described in the "Overview" doc:
  one card per patient, a badge for post-op day (or a countdown before surgery), a
  second pale badge for total time in care, the rolling AI-written summary with
  "still-open items" (questions asked but never answered, questions asked but not yet
  answered by the clinician), the last few messages with read receipts, and a
  contact/time log. Everything the model writes links back to its source message —
  the clinician should never have to trust a summary line without being able to check
  it in one tap. This view is meant for an iPad or phone first, not a desktop-first
  layout.
- **The patient check-in flow.** Reached via a signed link (SMS/email), not a login —
  patients are not Supabase Auth users. Pick a procedure from a dropdown, answer
  symptom checkboxes. Not a chat interface; structured input only, because many
  patients struggle to describe symptoms in free text (this was Sarah's explicit
  preference). Patients can still send free-form messages once in the thread —
  the structured check-in and free-text messaging are complementary, not one gating
  the other.

## The one rule that matters most

This app never imports server code from `apps/api` directly, and never talks to
Postgres directly except through Supabase Auth (for clinician login) and whatever
Supabase gives you client-side for auth session handling. Everything else — check-ins,
messages, summaries, red-flag state — goes through `apps/api` over HTTP, using a
client generated from its OpenAPI schema. This is what keeps this app and the api
service independently deployable and, if it's ever needed, independently split into
their own repos without a rewrite.

## Current state

A first, basic trial UI for reviewing the clinician workflow (not the card-based
dashboard from the Overview doc yet):

**Styling.** Colors come only from the locked schema in `docs/planning/color-schema.md`
(source of truth for names, hex values, roles and usage rules; change a color there first).
Its tokens are CSS custom properties on `:root` in `app/globals.css`, with the same names, and
are exposed to Tailwind through `@theme inline`, so components use utilities such as `bg-canvas`,
`text-ink`, `bg-primary` / `hover:bg-primary-hover`, `bg-surface`, `bg-surface-subtle`,
`border-border`, `border-border-strong`, `text-muted`, `text-disabled`, `bg-flag-bg`,
`text-flag-text` and `border-ai-border`. No raw Tailwind palette classes and no hex values in
components. `components/status-badge.tsx` is the one badge for clinical status (`flag`, `watch`,
`clear`, `info`) and AI-generated content (`ai`); it always shows a label and a dot. Rules of
thumb: red/amber/green only mean clinical status, teal is the brand and every action, errors and
Reject use the flag text/border colors, metadata badges are neutral. Light scheme only (no dark
mode). Keyboard focus is one global 2px primary ring (`:focus-visible` in `globals.css`).

- `/`: public landing page (`app/page.tsx`: hero, how it works, why it's different, sign-in
  call to action; nav with Login at far left). All copy is a draft, marked `COPY: DRAFT` at the
  top of that file. It used to redirect to `/dashboard`; a visitor who already has
  a clinician session is still sent there (`components/signed-in-redirect.tsx`, client side,
  since the session lives in the browser). No middleware is involved.
- `/login`: clinician email + password via Supabase Auth (session handling only; all data
  goes through `apps/api`).
- **Signed-in clinician** (`lib/use-clinician.ts`, used by the dashboard and the patient page): checks the
  session, then asks the api `/me`. If that has not finished after 15 seconds it stops showing
  "Loading...": the page says "Couldn't load. Check your connection." with a **Retry** button
  (`components/clinician-problem.tsx`). The stalled request is aborted and anything it delivers late is
  ignored, so a retry never has two attempts racing. Other failures keep their own messages (no Retry).
- **Clinic switcher** (`components/app-header.tsx`, `lib/clinic-choice.ts`): the header always names the
  clinic being worked in. A user who belongs to more than one clinic gets a dropdown in its place
  (a user with one sees just the name). The choice is remembered per user in this browser
  (`localStorage` key `ca.clinic.<user id>`, every access in try/catch); if the remembered clinic is
  no longer one of theirs, or storage is blocked, the first clinic from `/me` opens. Switching does a
  full page load of `/dashboard`, so the queue is the new clinic's and no patient from the old one stays
  open. It is a convenience, not a permission: the api checks membership on every request.
- `/dashboard`: "Needs attention" queue (red flags first, then latest activity; unchanged
  ordering: each card also shows a chip per unread message category, and today's day after
  surgery), add patient (the clinic enters the **surgery date and procedure**, both required;
  they create the patient's care period), and all patients, each with where their care period
  stands ("Check-in pending", "Window 3 of 9 open", "Discharged", ...).
- `/dashboard/patients/[id]`, top to bottom: **Physician Summary**
  (`components/physician-summary.tsx`: computed from the schedule and the record, never by AI, so the
  facts have no AI badge). From the top: the procedure, surgery date and "Post-op day N" in a neutral
  badge; then the **check-ins** (`components/check-ins.tsx`):
  a "Show check-ins (N)" / "Hide check-ins" fold (the shared `components/disclosure.tsx`, a native
  `<details>`) that is **closed on load, always**, holding the check-in cards (answers, red-flag reasons,
  Mark reviewed; check-ins that are not this care period's follow under "Not in this care period"), and
  below it the latest check-in (completed date, flagged, reviewed) and every flagged check-in of this
  care period with its date and whether it has been reviewed, or "No red flags" -- those lines are
  visible open or closed; with no check-in yet the block says so instead. Then the status sentence with the care period's
  status badge ("Window 3 of 9 · open") flush right on the same row (below it on a narrow screen), a
  row of the nine message windows: the window number above each circle, the circle for its
  state (✓ ✕ ● ○), then the days since the previous window opened (window 1: since the day of
  surgery; counted in calendar days from the schedule, 2, 3, 5, 7, ...) and its opening date (a missed window's reason is
  in its hover text), whether a reply is open, "fix surgery date or procedure" until the patient has
  checked in, and **discharge** with a confirmation, owner/clinician only, no undo; the two actions
  are in `components/episode-actions.tsx`; schedule version in small print. Below a line in the same
  box, set apart from the computed facts, is the **AI patient summary** (`components/patient-brief.tsx`,
  AI badge): three labelled bullets (The picture now, Trends, Loose ends). "Generate" / "Refresh" are
  the only things that call the model; opening the patient shows the newest saved one; under the
  button, "Last updated ..." in the clinic's time zone and "· N new messages, check-ins, contacts
  since" when something has arrived; with no check-in and no messages the button is off and says why.
  Below another line is the **Care provider summary** (`components/care-provider-summary.tsx`, AI
  badge): one short paragraph the physician can pass on to the patient's other care provider, with no
  source links (it is meant to be copied as written, so its footer says to check it against the
  records first). Same behaviour as the AI patient summary: instructions before the first one,
  "Generate" / "Refresh" are the only things that call the model, the newest saved one is shown on
  open, the "Last updated" and "Written by AI" lines sit under the button. The paragraph sits in a box
  with a copy icon in its top-right corner that puts what is shown on the clipboard (the icon turns into
  a check mark and "Copied." shows for a moment; if the browser refuses, a note says to select the text
  instead). **Edit** swaps the paragraph for a text box (3000 characters) with **Save
  edit** (on only when the text changed) and **Cancel**; a saved edit is what is then shown and copied, the
  footer says it was edited and when, and what the AI wrote stays in the record. **Refresh** on an edited
  summary first asks whether to replace the edited version on screen (the edit stays in the record).
  Then **Interaction Summary** (below), read and reply to the message thread (one compose box, below; each patient message shows its
  category and window, or "Reply"; once the care period has ended the box is replaced by a note,
  because the patient can no longer send messages and one of yours could never be answered; every message
  you send lets the patient send one reply, and the box says so), plus an AI summary:
  - **Interaction Summary** (`components/summary-view.tsx`, badge "AI interaction summary"): opening a patient shows the newest saved summary
    (read back without calling the model; "Refresh summary" asks again). "Summarise"
    calls the api on demand (never automatic). Each line shows chips for the messages it cites; clicking a
    chip scrolls to and highlights that message in the thread. The api has already verified
    every citation, and an unverifiable summary is discarded and shown as an error (any earlier
    summary stays on screen and is labelled as the previous one). It notes when messages
    arrived after the summary was written and when older messages were left out. The summary
    lives only in component state -- the api doesn't store it, and it holds patient content,
    so it is not put in browser storage either (a reload clears it).
  - **Compose box** (`components/compose-box.tsx`, under the thread): the one place a clinician
    writes to the patient, in two states so there is only one way to send at a time.
    *Compose*: a textarea with **Draft a reply with AI** (left) and **Send** (right; disabled
    while the textarea is empty; sends the typed text as-is, same path as before, so the auto
    `message` touchpoint and audit event are unchanged). *Draft view* (after Draft a reply with
    AI): the patient message beside the AI draft with **Approve and send**, **Edit, then send**
    (the edit must differ from the draft) and **Reject**; no compose textarea and no Send. What
    was typed is kept while the draft view is open: Reject returns to compose with it restored;
    approve or edit-and-send returns with an empty textarea. **Draft a reply with AI** is enabled
    only while a patient message awaits a reply (the latest non-system message is the patient's;
    `awaitingReplyCount` in `lib/contact-stats.ts`, shared with the Contact card), otherwise it is
    disabled with the hint "Nothing to reply to yet". If the api answers 409 anyway (the thread
    changed since the page loaded) the same hint is shown; if the model itself declines (422) the
    api's message ("The AI had nothing to draft for this message...") is shown instead. Reloading the page while a
    draft is pending for the latest patient message reopens it in the draft view. Only owners and
    clinicians can decide (delegates can request a draft and see it with a note and a Back button;
    the api enforces it with a 403). Decided drafts are no longer listed on the page (they are kept
    in the database and the audit log). Drafts load separately from the rest of the
    page, so if that call fails the thread, check-ins and plain Send still work.
  - **Contact** (`components/contact-log.tsx`, figures in `lib/contact-stats.ts`): the overview
    spec's Contact card. Counted, never inferred: messages (patient / you), clinic visits, calls
    (with minutes), first contact, time since last contact, patient messages awaiting your reply
    (those with no clinician message after them), "Your time on this patient" (minutes you
    entered) and median reply time (a run of patient messages to your next message). Calls, visits
    and emails are entered with **Log a call, visit or email**; "Contact history" lists every
    entry with an `auto` / `manual` badge (`auto` = the "message" rows the api adds when a
    clinician message is sent; the card counts messages from the thread, not from those rows).
    Not shown yet because the data doesn't exist: post-op day on each contact (no procedure
    date is stored; dates are shown instead) and time measured inside the app (not tracked).
    Loads separately like drafts.
  - **Patient account**, last on the page, since it is set up once and seldom touched again (`components/patient-account.tsx`: status (not invited / invited on a date / active since a date,
    last sign-in / locked / access turned off), the email the invite goes to ("Sign-in codes go to" once the patient is active; change it, or add one;
    refused while active), and the buttons for that state: Send invite, Resend invite, Turn off access
    (with a confirmation), Re-invite. Turning off and re-inviting are owner/clinician only, and the box
    says so to anyone else. "Patient has no email?" opens the single-use link: create it, copy it now
    (it is shown once, works once, lasts 24 hours, and the patient is asked their surgery date), and
    the latest invite email's fate: sent, saved in the Demo inbox, failed, or refused).
  Summary and Draft a reply with AI need AWS credentials set on the api (Bedrock); without them they show the api's error.
- **Patient sign-in (patient accounts).** A patient signs in at `/sign-in` with their email and a
  code (`components/patient-sign-in.tsx`; no password; the answer to "send me a code" is the same
  for every address; a 503 from the api, meaning sign-in is not set up on the server, says "Sign-in isn't available right now" and to call the office). The session lives in its own Supabase client (`lib/patient-session.ts`, storage
  key `ca-patient-auth`), so it never replaces a clinician's session in the same browser and signing
  out of one never signs out the other. Every page a signed-in patient uses sits inside
  `components/patient-gate.tsx`: no session goes to `/sign-in`; the first time, the **surgery-date
  check** and then **consent** (`patient-first-sign-in.tsx`; a wrong date says how many tries are
  left, the third locks the account); a locked or turned-off account gets a plain "call the office"
  screen; then the care page. Every one of those screens has **Sign out**. `/sign-in/link#<secret>`
  is the single-use link for a patient with no email: it asks the surgery date every time. All the
  words are in `lib/patient-strings.ts` (placeholder wording; the consent wording has a version the
  api stores with the answer). The clinician side: the **Demo inbox** (`/dashboard/demo-inbox`, linked
  in the header only for an owner of a demo clinic) shows the emails a demo clinic did not send, with
  the banner "Demo clinic: emails are not sent."
- **Patient screens (Messaging v2).** A signed-in patient's `/care` (the api's `GET /episode` says
  where they stand; `/messages` is the same screen, and `/check-in` is the check-in). The old
  per-episode links (`/care?token=` and the like) are retired: they show "This link has been
  replaced" and nothing else. Nothing here decides what is allowed: the api
  computes it from the schedule and the clock, and the page only shows it.
  - `/care` (`components/patient-home.tsx`), top to bottom: the **urgent notice first** (same words
    as everywhere; with no office phone it still says to call the office or go to the emergency
    department), the first-visit **"How this works"** panel (`patient-explainer.tsx`: three steps, a note on how
    many messages there are ("one message in each scheduled period, and one reply when your care team
    writes"; placeholder wording), and "Got it", remembered per browser in localStorage inside try/catch, so if storage fails it simply
    shows again; a small "How this works" link brings it back), the thread (patient messages carry
    their category, replies say "Reply"), then **one plain status sentence** just above the box it is
    about ("Please complete your check-in first...", "You can send your first message from ...", "You
    can send a message now, until ...", "Thanks, your message was sent. You can send your next one from
    ...", plus "You can reply to your care team's latest message." whenever a reply is open), the
    **timeline of the nine periods** (`patient-timeline.tsx`: a check mark for a period they wrote
    in, a highlighted dot for the one open now, an outline for upcoming ones, and a plain grey dot
    for ones that ended, never a cross or red; ones that ended before their check-in are grey too
    and say "before your check-in" on tap; a glyph as well as a look for each, labelled for screen
    readers; at phone width only the next opening day is written out and a tap on a circle gives its
    dates, wider screens show every opening day under its marker), "You can write once in each
    period. Most people don't need to use them all.", a reply box when the care team has written
    (one reply, outside the periods), and the scheduled form: pick one of the categories, then write.
    Once the care period has ended the whole screen is the urgent notice and "Your follow-up
    through this app has ended. If you need help, call the office or go to the emergency
    department." **Every word of this feature is in `lib/patient-strings.ts`** (placeholder wording
    pending clinical review; "period" for patients, "window" for the clinic), so wording changes and
    translation touch one file. Times are written plainly (`plainTime`, no zone name) in the
    clinic's timezone, not the device's.
  - **Follow-up extensions** (migration 16; **the feature is off until the `extension` schedule row is
    switched on, and all numbers and wording are placeholders pending the surgeon**; rules in
    docs/api). *Patient:* when the care period ended by schedule the api answers phase `closed` plus an
    `extension` block, and `components/ended-screen.tsx` shows "Your follow-up through this app has
    ended." with, by state: **eligible**: "Request more follow-up" (opens a reason box, up to the api's
    `reason_max`, and "You can ask until <date>"); **pending**: "Your request was sent. Your care team
    will review it." and that no notifications are sent, so the answer shows here; **declined**: the
    clinician's message if any and "If you need help, call the office or go to the emergency
    department." (no form); **accepted**: the normal screen, with a green card ("Your care team has
    agreed to more follow-up", the clinician's message) and the timeline of the extra periods
    (`PatientTimeline` with `extra`, any number of periods). A closed episode with no `extension`
    block is the old "closed" screen. All words are in `lib/patient-strings.ts` (`extension`).
    *Clinic:* the queue shows an **"Extension requested"** chip (also on the patient list); the
    Physician Summary shows the request (`components/extension-request.tsx`: their reason and the date,
    optional message, **Accept...** / **Decline...** each with a confirm step; a delegate sees the request
    without buttons) and, after acceptance, the timeline in rows: the nine, then the six marked
    "Extension" (dotted circles; the first reads "accepted" instead of days), with the status badge
    "Extension window 2 of 6 · open". The request and the decision are set apart in the thread
    (they are not messages: they never count as unread or as awaiting a reply, and "Draft a reply with
    AI" ignores them); a window of an extension reads "extension window N".
  - **Your check-in** (`CheckInCard` in `patient-home.tsx`): at the top of the conversation, what the
    patient entered at their check-in (their answers, and "in your own words"), collapsed to a
    one-line reminder that opens to the full answers; shown only once they have checked in. It
    reads `GET /check-ins/mine`, which returns the answers and when, never the clinic's red flag or
    review state.
  - Category labels (`lib/categories.ts`) are placeholders; the api stores the key.
  - `/check-in`: the procedure is the clinic's choice (from the episode), so the picker and the
    typed "days since surgery" are gone; the post-op day is worked out by the api. One check-in
    per episode: a second visit says it was already sent.
  - An old `?token=` link is "This link has been replaced. Your surgeon's office will email you a
    sign-in invite." (`ReplacedLinkScreen`); a closed episode (last window ended, or discharged)
    is "Your follow-up has ended". Every patient screen, locked and closed ones included, ends
    with the urgent notice (call the office, with the clinic's number when the api has it, or go
    to the nearest emergency department): `components/patient-frame.tsx`. All of this wording is
    a first draft.
  Patients are link-only for now; patient sign-in comes before real patients.

Uses the user's first clinic. Polls the api every 10-15s (no realtime yet). `shadcn/ui`
was not initialized.

Env (see `.env.example`): `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY` (public values; the anon key is safe in the browser).
The build tolerates them being unset (sign-in then fails at runtime).

Vercel: Root Directory `apps/web`. On the api side, set `WEB_BASE_URL` (the web app's origin: the invite email points at
`<it>/sign-in`) and add the Vercel origin to `ALLOWED_ORIGINS`.

## Deploying (Vercel + Railway)

1. **Vercel**: import the GitHub repo, set **Root Directory** to `apps/web`, and add
   `NEXT_PUBLIC_API_URL` (the Railway api URL), `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` *before* the first build -- `NEXT_PUBLIC_*` values are
   baked in at build time, so changing them needs a redeploy. The anon key is public by
   design (Vercel warns about it; keep the `NEXT_PUBLIC_` name and mark it as config). Never
   put the `service_role` key here.
2. **Railway** (api service): set `WEB_BASE_URL` and `ALLOWED_ORIGINS` to the Vercel origin
   (no trailing slash). Without `WEB_BASE_URL` the single-use link button returns 503 and the invite email has no address to point at; without
   `ALLOWED_ORIGINS` the browser blocks every call to the api (CORS).
3. Vercel builds every pushed branch as a Preview and `main` as Production. A preview's
   origin differs from production's, so it also needs adding to `ALLOWED_ORIGINS` to work
   against the api.
4. Give each clinician a Supabase Auth user (Auto Confirm) plus a `clinic_members` row.

## Design references already agreed

The Overview doc (Claude Docs artifact, "Patient Messaging Tool — Overview") has
three mocked-up cards worth pulling up before building the dashboard — they were
reviewed and are the current best guess at layout, not final. Two open questions from
that doc that are still genuinely undecided, worth resolving before or during build
rather than picking silently:

- **Expanded vs. collapsed card as the default** (Card A vs. Card B in the mockups).
- **Whether patients get a free-text message box, or checkboxes only** — the Overview
  doc built it with a message box for both sides; Sarah's email leaned toward
  checkboxes-only for patients. Decision 5 in decisions-and-open-questions.md resolved
  this toward free text + structured layer, but confirm this hasn't shifted before
  building the patient-side input.

## Near-term backlog (roughly in order)

1. Clinician auth (Supabase Auth, email/magic link — no patient-facing auth needed
   yet since patients use signed links, not accounts).
2. The card component itself, static first (real data wiring depends on api
   endpoints existing).
3. Patient check-in flow: procedure dropdown + checkbox form, reachable via a token
   in the URL.
4. Wire cards to real api data once the corresponding endpoints exist.
5. Message thread view with read receipts.

## Reference

- `../../claude/decisions-and-open-questions.md` — read the "Decisions" section,
  especially 5 (messaging model) and 7 (v1 scope), before assuming UI behavior.
