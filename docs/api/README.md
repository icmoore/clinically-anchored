# api — service brief

Read this before touching `apps/api`. It's the product context a fresh thread needs;
`apps/api/README.md` has the dev-loop commands.

## What this service owns

`apps/api` (FastAPI, Python, deployed to Railway) is the only thing that talks to
Postgres directly and the only thing that calls out to an LLM. Concretely, it owns:

- **Check-in intake.** Patients submit structured answers (procedure dropdown +
  symptom checkboxes) via a signed link, no login. This service validates the link
  token, writes the check-in, and runs it through the red-flag rules.
- **The red-flag rule engine.** Fixed rules a clinician writes, not model judgement —
  nothing decides on its own that a patient is fine. A check-in or message that
  matches a rule gets pulled to the top of the clinician's queue.
- **Thread summary generation.** On demand only: an explicit endpoint call, never a
  background job and never triggered by a message arriving (so a summary can be stale;
  it says when it was generated). Every generated line links back to the message it
  came from — this is a hard product requirement, not a nice-to-have (see "What you
  would see" in the patient-facing Overview doc: the clinician has to be able to
  check any claim in one tap).
- **The hash-chained audit log writer** (`audit_log` table — see docs/database).
  Every message, check-in, consent, and AI-draft-approval event gets a signed record
  chained to the previous one for that clinic. This is the MVP's actual differentiator,
  not the optional blockchain layer — the product works fully with no chain involved.
  Public anchoring via AnchorRegistry is v2, optional, and a separate downstream
  batcher; don't build toward it yet.

`apps/web` never does any of the above. It only reads/writes through this service's
HTTP API (OpenAPI schema at `/openapi.json`), and it authenticates end users through
Supabase Auth directly for clinicians — this service uses the Supabase *service role*
key, which bypasses row-level security by design. That means this service is what's
responsible for authorization logic that RLS can't express (e.g. validating a
patient's link token), not the database.

The one shared database client (`core/db.py`) talks **HTTP/1.1**, not the HTTP/2 that postgrest-py
defaults to: the endpoints run on a thread pool, and many threads over one HTTP/2 connection
broke under load (`KeyError` in `http2._response_closed`, `ConnectionTerminated`, `Broken pipe`,
and 500s on whatever was in flight). Tested in `tests/test_db_client.py`.

## Current state

Built (on `main`):

- **Messaging v2 (scheduled patient messaging), stage 2.** Read this first: the rules are the
  surgeon's, the placeholders are marked. An **episode** is one patient's post-op period
  (migration 9): surgery date and procedure entered by the clinic when the patient is added
  (`POST /clinics/{id}/patients` now takes `surgery_date` and `procedure_id`, and creates the
  patient and their episode together; `POST .../patients/{pid}/episodes` starts a new surgery
  for an existing patient), on the default schedule: **the newest active `message_schedules`
  version** (highest `version` with `active = true`; `core/episodes.default_schedule`; a 503 if none
  is active). An episode keeps the version it recorded, forever. The schedule is DATA (offsets in
  days from the surgery date, opening time, timezone, close-after), versioned. **v2** (migration 11)
  is the surgeon's confirmed schedule: windows open at 07:00 America/Toronto on days 2, 5, 10, 17,
  24, 31, 38, 45, 52 and the episode closes on day 59; v1 (2,3,5,7,14,21,28,35,42, close day 49)
  was a placeholder and remains only for episodes made on it. Nothing in the api, the engine or the
  web app hard-codes either: counts, offsets and the close day are read from the schedule row. Messaging is locked until the patient completes
  the post-op check-in (one per episode); then they may send ONE message per interval (a
  category key is required: `core/categories.py`, placeholders), intervals open on the clock
  not on the last message, an unused interval is gone when the next opens, and a late check-in
  does not give back passed intervals (they are `missed`). Every clinician message lets the
  patient send one reply (to the latest one) outside the intervals, even before the check-in.
  Clinicians can message any time while the episode is open. The episode closes when the last
  interval ends (lazily "settled" by the first request that notices, audited
  `episode.closed` by `system`) or when an owner/clinician discharges it
  (`POST /clinics/{id}/episodes/{eid}/close`, no undo).
- **The engine** (`core/schedule.py`) is a pure function of (surgery date, schedule, `now`,
  check-in time, close, used intervals, reply available) -> `EpisodeState` (phase `locked_checkin`
  / `waiting` / `open` / `used` / `closed`, the open interval, when the next opens, missed
  intervals, and `windows`: every interval with a status `used` / `missed` / `open` / `locked`
  (open by the clock, check-in not done) / `upcoming` / `cut_short` (the episode closed first) and,
  for a missed one, `missed_reason`: `closed_before_check_in` (it ended before the check-in, so
  nothing could be sent) or `no_message` (the patient could have written and did not). `windows` is
  additive; `missed` is unchanged). Interval state is computed, never stored. Tested with a pinned clock including the
  Toronto clock changes (`tests/test_schedule.py`). `core/episodes.py` gathers the facts and
  settles; `core/clock.py` is the one source of "now". The database enforces what a race could
  break (one scheduled message per interval, one reply per doctor message, nothing into a
  closed episode) through service-role functions (migration 9); the api decides which interval is
  open. `tests/db/` runs the api against a real Postgres end to end (not in CI yet).
- **The patient's link** (`core/security.py`): ONE signed token per episode carrying the episode
  id and `link_generation`. On every request the api loads the episode: a reset link (generation
  bumped) is 401, a closed episode is 410. `GET /episode?token=` is the one patient route that
  still answers once closed, and says only `closed` plus the clinic's `office_phone` (and an empty
  `windows`). While open it also lists the patient's message periods as `windows`: each one's
  `number`, `status` (`used`, `open`, `upcoming`, `passed` = ended unused, `before_check_in` = ended
  before they did their check-in), `opens_at` and `closes_at`, next to the existing `next_opens_at`,
  `can_reply` and `episode_closes_at`. It is the clinic's window list reduced on purpose: no missed
  reason wording, no `locked` or `cut_short`, no schedule version, review state or red flags, and a
  period running by the clock before the check-in reads `upcoming`. Additive; an older client
  ignores it. The token
  has no short expiry (a 120-day backstop); `POST .../episodes/{eid}/link` returns the same URL
  each time (`/care?token=`, the web page is stage 3), `.../link/reset` revokes it. The old
  `checkin` / `messages` scopes, their separate expiries and `/dev/check-in-links` are gone
  (dev: `POST /dev/episode-links`). Patient routes: `GET /episode`, `GET`/`POST /messages`
  (`kind` scheduled|reply, `category`), `GET /check-ins/context`, `POST /check-ins` (the
  post-op day is computed, the procedure is the clinic's), `/consents`.
- Check-in intake: `POST /check-ins`, `GET /check-ins/context`, and `GET /check-ins/mine` (the patient reads
  back their own answers: null until they have checked in; no red flag or review state) (see above).
- **Demo data** (`seed_trial_patients.py`, run by hand, never by CI): 13 synthetic patients in a
  separate demo clinic, one per engine state, written with backdated rows and the same audit
  events the api writes (tagged `seed`). Its plan is replayed through `core/schedule.py` and the
  database's own schedules before anything is written (`validate_plan`), and `tests/db/test_seed.py`
  runs it against Postgres: every patient lands in the declared state, the chain verifies,
  `reconcile` is clean, and the readable metadata of each event matches what the api writes
  (a drift guard: change an event in the api and that test says to change the seed).
  All 13 are on v2; `--include-v1-patient` adds a 14th on v1. The seed never changes the schedules
  or which one new patients get.
- **AI prompts under Messaging v2:** `draft_reply_v4` and `summary_v4` show each patient message's
  topic key and window next to the speaker, "patient (symptom_or_wound, window 3)" or "patient
  (reply)" (`core/thread_text.py`; parentheses, never square brackets, which the summary format
  keeps for citations). The draft prompt also says not to invite the patient to message again and
  to point them to the office or the emergency department for anything urgent. **Wording is a
  placeholder pending clinical review**; older versions stay as they were. A draft is refused (409)
  for a patient whose care period has closed.
- Red-flag rules (`core/rules.py`) -- **placeholder logic**, not Sarah's real list.
- Clinician auth (`core/auth.py`): Supabase JWT + `clinic_members` check per clinic.
- Messages: clinician side (list thread, send, mark read; JWT auth) and patient side
  (`GET`/`POST /messages?token=`; sender forced to `patient`). Sends go through the database
  functions `send_clinician_message` / `send_patient_message`. Mark-read is audited
  (`message.read`, first read only). Every message records its episode, `origin`
  (scheduled / reply / clinician), interval and category.
- Rate limiting on every link-token route (`core/ratelimit.py`, applied in the token
  dependency in `core/security.py`): per episode, sliding 60s window, 60 reads and 10 writes
  per minute (`PATIENT_LINK_READS_PER_MINUTE` / `PATIENT_LINK_WRITES_PER_MINUTE`; 0 disables).
  429 with `Retry-After`. **In-memory, single instance:** counters reset on restart and are
  not shared across instances/workers -- Redis-backed limiting is a Pro-tier job and `hit()`
  is the seam to swap. Keyed by the verified token's episode, not by IP: behind Railway's proxy `request.client.host` isn't the real client unless
  uvicorn is told to trust forwarded headers. Invalid tokens aren't counted (rejected by HMAC
  before any DB work). Clinician routes are not rate limited.
- Consent records (`api/consents.py`, migration 5, data-catalogue D12) -- **storage and audit
  only, nothing is gated.** Patient side, via the episode link (open episodes only): `GET /consents`, `POST /consents`
  (`consent_type` slug + `consent_text_version`), `POST /consents/{type}/revoke`. Clinician side:
  `GET /clinics/{id}/patients/{pid}/consents` (current `active` types + full history).
  One row per grant; withdrawing sets `revoked_at` on every live grant of that type, a later
  re-grant is a new row. Grants are idempotent per (type, version); a new wording version is
  a new grant. Each grant/withdrawal is audited (`consent.granted` / `consent.revoked`, ref
  type `consent`). **Open, Sarah's call -- deliberately not decided here:** the consent
  wording (lives outside this service, referenced by version), whether any consent blocks
  check-in or messaging, and how many types there are (messaging vs. AI-assisted
  communication per the catalogue). Until the list is settled any well-formed slug is accepted;
  set `CONSENT_TYPES` (comma-separated) to enforce a list. Not built: a clinician-side
  "record a withdrawal on the patient's behalf" route (who may do that is part of the same
  open question).
- Clinician queue (`api/queue.py`): `GET /clinics/{id}/queue` (patients needing attention:
  unreviewed check-ins + unread patient messages, unreviewed red flags first; each item
  carries today's post-op day from the surgery date and the categories of unread messages),
  `GET /clinics/{id}/check-ins` (filters: unreviewed / red flag / patient), and
  `POST /clinics/{id}/check-ins/{id}/review` (idempotent, audited as `check_in.reviewed`).
  Migration 4 adds `check_ins.reviewed_at/by`. Queue reads cap at 1000 rows per query.
- Clinic/patient management for the web app: `GET /me`, `GET`/`POST /clinics/{id}/patients`
  (audited `patient.created` and `episode.created`; the list carries each patient's latest
  episode and its computed state), and the episode routes above. Link issuance (audited
  `link.issued`, token never logged) stands in for SMS/email delivery during trials; needs
  `WEB_BASE_URL` outside development.
  Patients stay link-only (no patient sign-in) -- decision for the trial; revisit with Sarah.
- Startup guard: outside `ENVIRONMENT=development` the app refuses to start unless
  `CHECKIN_LINK_SECRET`, `AUDIT_SIGNING_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` are set.
- Audit log writer (`core/audit.py`): salted payload hash, per-clinic hash chain,
  Ed25519 signature, atomic append via `append_audit_event()` (migration 3), and
  `GET /clinics/{id}/audit-log/verify` (checks each row against the public key named by its
  `key_id`; keys other than the current one come from `AUDIT_PUBLIC_KEYS`). Audited events:
  `check_in.submitted`/`.reviewed`, `message.sent`/`.read`, `patient.created`, `link.issued`,
  `episode.created`/`.corrected`/`.closed`/`.link_rotated`, `consent.granted`/`.revoked`,
  `touchpoint.logged`, `extension.requested`/`.accepted`/`.declined`. `message.sent` carries the episode, origin, interval and category key in
  its readable metadata (a menu choice, not content) and the text only in the hashed payload;
  `check_in.submitted` records the intervals that had already passed (`missed_intervals`).
  Known gap: the business row and its audit row are separate writes, so a failure
  between them leaves an unaudited row (the request returns 500 and logs it; the row stays).
  Reconciliation is a script, not an endpoint: `python -m clinically_anchored_api.core.reconcile
  [--clinic ID] [--since ISO] [--json]` (needs the api's Supabase env, e.g. `railway run`)
  matches every row against `audit_log` by clinic, `event_type` and `metadata.ref_id`, and
  reports unaudited rows (exit 1) plus, informationally, audit events whose row is gone. It is
  read-only and selects ids/timestamps only; what to do about an orphan is a human decision.
  Links can't orphan: they have no row, and the URL is returned only after its audit write
  (so `link.issued` and `episode.link_rotated` have no reconcile check; episodes do).
  Rows that predate an event type (seed-migration patients, messages read before `message.read`
  was audited) show as unaudited -- scope with `--since`.

- AI wrapper (`core/ai.py`, used only by the drafts routes below): thin AWS Bedrock Converse client
  (boto3, standard credential chain), always `ca-central-1` (fixed in code; `AWS_REGION` is ignored
  so inference can't drift out of Canada), model from `BEDROCK_MODEL_ID` (default Claude Haiku
  4.5). `generate()` takes the caller's `prompt_version`, sends it to Bedrock as request metadata
  and returns it with the model id and token usage, so the *caller* logs all three to the audit
  trail (this module writes no audit events). Per-clinic daily call cap
  (`AI_DAILY_CALL_CAP_PER_CLINIC`, default 200; 0 blocks everything) is checked before Bedrock
  is called and raises `AIDailyCapExceeded`; in-memory, per instance, UTC day, failed calls
  count. Prompt/reply text is never logged. Versioned prompt templates live in
  `clinically_anchored_api/templates/<name>_<version>.md` (`## System` + `## User`, `{{vars}}`),
  loaded with `load_template(name, "v1")`; the shipped ones (`draft_reply_v1`-`v4`, `summary_v1`-`v4`; the newest of each is in use) are
  **placeholder wording pending clinical review**. A wording change is a new version file.
  Inference location remains a product decision (see the hard constraints below): this wrapper
  assumes Bedrock in ca-central-1.
- Thread summaries (`api/summaries.py`, `core/citations.py`): `POST
  /clinics/{id}/patients/{pid}/summaries` -- on demand only, any clinic member, nothing automatic.
  The model (template `summary_v4`) is shown the patient's last 50 messages as `[M1] speaker: text`
  (short labels that the server maps back to message ids) and must write one claim per line, each
  ending in the label(s) in brackets. **Every
  citation is verified before anything is returned:** it must parse as a message id and exist in
  *this clinic's thread with this patient* (checked in the database, not just against the prompt).
  An unverifiable summary -- uncited line, intro line, malformed or empty citation, bracket
  mid-line, or an id that doesn't exist / belongs to another patient or clinic -- is discarded
  whole (502; nothing shown, nothing recorded as generated); no citation is silently dropped or
  repaired. A verified summary is returned (`lines[{text, citations}]`, `covers_messages`,
  `truncated`) and audited as `summary.generated` (model id and prompt version in readable
  metadata, text and citations in the hashed payload); if that audit write fails it is not shown
  either. **Saved after it is audited** (migration 10, `thread_summaries`): one row per generation, never
  edited or deleted, `id` = the audit event's `summary_id`, so what a clinician was shown can be
  reproduced and checked against the hash. `GET /clinics/{id}/patients/{pid}/summaries/latest` reads
  the newest back (null if none) without calling the model, so the web app shows the last summary
  when a patient is opened; each "Refresh" still spends a call from the daily cap and adds a row. If
  saving fails the clinician still gets the summary and the failure is logged; reconcile checks
  saved rows against `summary.generated`. Same data-flow caveats as drafts: sends thread
  text to Bedrock, inert without AWS credentials. Older versions are kept but unused; the
  template pins the output format the parser expects. Placeholder wording, pending clinical review.
- AI patient summary / "brief" (`api/briefs.py`, `core/brief.py`, migration 13): `POST
  /clinics/{id}/patients/{pid}/patient-brief` -- on demand only (a clinician's button), any clinic
  member, nothing automatic. Three labelled bullets, in this order: **The picture now**, **Trends**,
  **Loose ends**, each one or two short sentences ending in the sources it came from. The model
  (template `patient_brief_v1`, placeholder wording pending clinical review) is shown the care
  period's computed state, the care period's check-in with its answers, own words and the red-flag
  rules it matched, the care period's last 50 messages, and the calls/visits/emails logged since it
  began (last 30), as `[M1]` messages, `[C1]` check-ins, `[T1]` contacts. It is **never** shown the
  conversation summary or any earlier AI output. **Every source is verified before anything is
  returned:** each label must be one it was given, and each record must exist for *this clinic and
  this patient* (checked in the database). Anything other than exactly the three bullets, a bullet
  with no citation, an unknown label, a missing or misordered bullet label, or a source that is
  another patient's or clinic's discards the whole summary (502, nothing shown, nothing audited);
  nothing is repaired. "Loose ends: None." still cites the most recent item the model checked. A
  verified brief is audited as `patient_brief.generated` (model id, prompt version, counts and tokens
  in readable metadata; text and sources only in the hashed payload) and then **saved** (`patient_briefs`,
  one row per generation, never edited; `id` = the event's `brief_id`). 422 when the patient has no
  care period, or no check-in and no messages yet. `GET .../patient-brief/latest` reads the newest
  back (null if none) without calling the model, with `new_since`: how many messages and check-ins
  of that care period, and calls/visits/emails, were added after it was generated. Reconcile
  checks saved rows against `patient_brief.generated`. Same data-flow caveats as drafts.
- AI care provider summary (`api/care_provider_summaries.py`, `core/provider_summary.py`, migration
  14): `POST /clinics/{id}/patients/{pid}/care-provider-summary` -- on demand only (a clinician's
  button), any clinic member, nothing automatic. One short plain paragraph (at most about 120 words)
  the physician can pass on to the patient's other care provider, with **no citations**. It reads the
  same record as the AI patient summary (`gather_record` in `api/briefs.py`: the care period's
  computed state, check-in answers and own words, last 50 messages, calls/visits/emails since it
  began) plus the procedure name, which the AI patient summary leaves out because the page shows it.
  The model (template `care_provider_summary_v1`, placeholder wording pending clinical review) is
  **never** shown the conversation summary, the AI patient summary or any earlier AI output. Because
  there are no sources to verify against the database, the server checks the shape instead: empty,
  longer than 1500 characters, several paragraphs, a bullet or heading, or a leaked record label
  (`[M1]`, `[C1]`, `[T1]`) discards the whole thing (502, nothing shown, nothing audited); nothing is
  trimmed or repaired. A checked summary is audited as `care_provider_summary.generated` (model id,
  prompt version, counts and tokens in readable metadata; the text only in the hashed payload) and then
  **saved** (`care_provider_summaries`, one row per generation, never edited; `id` = the event's
  `summary_id`). 422 when the patient has no care period, or no check-in and no messages yet.
  `GET .../care-provider-summary/latest` reads the newest back (null if none) without calling the model,
  with `new_since` counts as for the AI patient summary. Reconcile checks saved rows against
  `care_provider_summary.generated`. Same data-flow caveats as drafts.
  **Editing:** `POST .../care-provider-summary/{summary_id}/edit` with `{"text": ...}` (1 to 3000
  characters, not blank, different from what is shown now, else 422) saves the physician's version. The
  AI's row is never changed: the edit is a new row in `care_provider_summary_edits` (migration 15), audited
  as `care_provider_summary.edited` *before* it is saved (text only in the hashed payload; `ref_id` = the
  edit's id), and a failed audit or save returns 500 with nothing saved. Only the patient's newest summary
  can be edited (409 if a newer one exists, 404 for an id that is not this patient's), any clinic member
  may, and the model is never called. The text is the physician's own, so it is not shape-checked like the
  AI's. `GET .../latest` and the edit response carry `edited_text` / `edited_at` (null until edited) next
  to `text`, which stays what the AI wrote. Reconcile checks saved edits against
  `care_provider_summary.edited`.
- **Follow-up extensions** (`api/extensions.py`, `core/extension.py`, migration 16). **All numbers and
  all wording are PLACEHOLDERS pending the surgeon's confirmation**; the numbers are data (the
  `extension` schedule row, version 3, "extension v1"), not code. **The feature ships OFF**: the row is
  inserted `active = false`; `update message_schedules set active = true where version = 3` switches it on,
  `false` switches it off (requests already made keep the version they were made under).
  *Rules.* Only an episode that ended **by schedule** can ask: not a discharge, not while the term still
  runs. The patient has `request_window_days` (30) after the end. Until then the link works in a limited
  "ended" mode (`GET /episode` answers phase `closed` plus an `extension` block and `ended_at`; every other
  patient route is still 410). The reason is required, 1-500 characters (`REASON_MAX`; also a table check).
  One request per ended episode: a pending request blocks a second and **a decline is final** for that
  care period. A pending request keeps the link in ended mode however long the clinic takes; a decision
  stays readable until the later of "end + 30 days" and "decision + 30 days" (so a late decision is still
  seen). After that the link says only "closed" and the office phone, as before.
  *Accepting* creates a **new episode** (`kind = 'extension'`, `extends_episode_id`, `started_at` = the
  acceptance instant) that continues the ended one: same patient, procedure and surgery date, the check-in
  counts, same link generation. Closed episodes never change, so nothing is reopened. The patient's
  original URL follows the chain (`episodes.resolve_tail`) to the newest episode; its generation is checked
  against that episode, so resetting the link there revokes every older URL. Extension v1: window 1 opens
  at the moment of acceptance; windows 2-6 open at 07:00 America/Toronto on days 7, 14, 21, 28, 35 after
  the acceptance **date** (wall-clock, so a clock change moves nothing); the extension ends on day 42.
  `offsets_days` of the row list windows 2-6 only. Everything else is the main period's rule: one message
  per window, unused windows are gone, clinician messages any time and each grants one reply. Windows
  number from 1 inside each extension; the clinic's timeline (`extension.earlier_windows`, plus
  `extension_no` on each window) shows the nine and then the six. When an extension ends by schedule the
  same rules apply to it (a new 30 days, a new request, a new approval). A discharge during an extension
  ends it, as today.
  *Thread and AI.* The reason and the decision are `messages` rows of their own origins
  (`extension_request`, `extension_decision`), not ordinary messages: no window, no category, no reply
  target, never unread (the queue ignores them and mark-read leaves them alone) and **neither grants a
  reply** (`reply_available` only looks at clinician messages, so a decline can't invite one). The decision
  entry's text is "Extension request accepted/declined." plus the clinician's own words; the patient is
  shown the clinician's words from the request row. They are in the thread, so the Interaction Summary, the
  AI patient summary and the care provider summary read them: `thread_text.speaker` labels them "patient
  (extension request)" / "clinician (extension decision)" and a window of an extension "extension window N";
  the patient summary and care provider summary now read the whole chain (all episodes from the first, the
  check-in, both periods' messages, calls and visits since the first began). The prompts that read the
  thread have new versions saying these entries are about follow-up, not symptoms: `summary_v5`,
  `patient_brief_v2`, `care_provider_summary_v2`, `draft_reply_v5` (older versions kept; placeholder wording).
  *Routes.* Patient: `POST /extension-request` `{reason}` (ended-mode dependency; 409 if already asked, 410
  if not eligible or out of time, 422 for a bad reason). Clinician: `POST
  /clinics/{id}/extension-requests/{rid}/accept` and `/decline`, body `{message?}` (optional, up to 5000
  characters), **owner and clinician only** (403 for a delegate; 409 if already decided or, for accept, the
  patient has a newer care period). The episode response carries `extension` (number, the request with
  its reason, `request_by`, `earlier_windows`); the queue item carries `extension_requested`.
  *Audit.* `extension.requested` / `extension.accepted` / `extension.declined`, ref_type
  `extension_request`, ref_id the request id, written **before** the save under ids the api generated and
  passed to the database function; the reason and the clinician's words are in the hashed payload only. A
  failed audit saves nothing (500). If the function then loses a race, the log holds one event with no row:
  reconcile lists it as informational "dangling". An acceptance also audits `episode.created` for the new
  episode (after the save, like any episode). Reconcile checks the three events against
  `extension_requests` and does not expect `message.sent` for the two thread entry kinds.
  *Database.* The functions `request_extension` and `decide_extension` lock the episode and enforce who may
  ask, once, within the window, and that nothing newer has replaced the episode; real-Postgres tests
  are in `tests/db/test_follow_up_extensions.py`.
- AI message drafts (`api/drafts.py`, migration 6, data-catalogue D8): `POST
  /clinics/{id}/patients/{pid}/drafts` asks the model for a reply to the patient's latest message
  and stores it `pending` -- it sends nothing. It is refused with **409 before any model call**
  (no draft row, no Bedrock call, no audit event) when nothing awaits a reply: the thread is empty
  or its latest non-system message isn't the patient's. The optional body `message_id` targets one
  specific patient message instead and skips that check (an explicit override; the web app doesn't
  use it). The model is also told (template `draft_reply_v3`) to answer exactly `NO_DRAFT` only when a
  clinician message already follows the patient's latest one; a response starting with that token
  (`NO_DRAFT_SENTINEL`, matched as a token, never by reading prose) is a **422** ("The AI had
  nothing to draft for this message.") with nothing stored and a content-free warning in the log.
  422 (model declined) is deliberately different from the 409 above (the thread has nothing to
  answer), so the web app can tell them apart; both differ from the 409 on a second decision.
  **Template versions:** v2 told the model to decline whenever there was "nothing left to reply
  to", and in practice it declined on short patient updates that did need a reply, so v3 makes a
  draft the default (v1 and v2 stay on disk, unused). `draft.*` audit events record
  `prompt_version: "draft_reply_v3"` for drafts made from now on; existing drafts and their events
  keep the version they were made with. `GET .../drafts[?status=]` lists them.
  A draft leaves `pending` only by an explicit clinician action: `POST /clinics/{id}/drafts/{did}/`
  `approve` (sends the draft as written), `edit` (body `{final_text}`; sends the edit, keeps the
  model's original; the edit must differ), or `reject` (sends nothing). The decision is one DB
  function (`decide_message_draft`) so a draft can't be sent twice or marked sent without a
  message; a second decision is a 409. Approve/edit/reject need role `owner` or `clinician`
  (`DECIDER_ROLES`); delegates can read and request drafts. Asking again for a message that
  already has a pending draft returns it without another model call; daily cap -> 429, model
  failure -> 502. Audited: `draft.generated` / `.approved` / `.edited` / `.rejected`, each with
  `model_id` and `prompt_version` in readable metadata (plus token counts on generated, and the
  sent message id on approve/edit), and an ordinary `message.sent` for the message itself. Drafts
  are kept forever: no delete route, and the database refuses deletes and changes to a decided
  draft or to the original text. **Data flow:** generating a draft sends the last 20 messages of
  that patient's thread to Bedrock (ca-central-1). It stays inert until `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` are set on Railway (no credentials -> 502), and the synthetic-data-only
  rule still applies. Clinic protocol notes (D13) aren't modelled, so that template variable is
  a fixed placeholder, and the template wording is still pending clinical review.
- Contact/time log (`api/touchpoints.py` + `core/touchpoints.py`, migration 8, data-catalogue D11):
  `GET /clinics/{id}/patients/{pid}/touchpoints` lists a patient's contact log, most recent
  `occurred_at` first; `POST` logs a call, visit or email by hand (body `kind`, optional
  `occurred_at` -- timezone required, not in the future -- `duration_minutes` 1-1440, `note` up
  to 5000). Any clinic member may log (delegates too: it sends nothing). `kind` `message` is not
  accepted from the client: a `message` / `source: auto` row (`logged_by` null, actor
  `{"type": "system"}`) is written by the api itself whenever a *clinician* message is sent --
  a direct send or an approved/edited draft -- never for a patient's message. That write is
  best-effort: the message is already sent, so a failed insert or audit is logged and the send
  still succeeds (a missing row is a gap in the log, not a lost message; an unaudited row shows
  up in reconcile). Audited `touchpoint.logged` (touchpoint fields, incl. the note, in the hashed
  payload; ids and actor in metadata). Rows can't be edited or deleted through the api; correct
  a mistake with a new entry. Not built: calendar import, and any baseline/outcome metrics
  rollup (decision 8) -- this is only the log those will read. The auto row isn't linked to the
  message that caused it (no `message_id` column); add one if the metrics need that join.
- **Email sending, part 1 of patient accounts** (`core/mailer.py`, `core/email_templates.py`,
  `api/demo_inbox.py`, migration 17; setup steps in [email.md](email.md)). Amazon SES in
  `ca-central-1`, an `email_outbox` row for every attempt (no body, no sign-in code), demo clinics
  (`clinics.is_demo`, set only by SQL) that send nothing and keep the rendered email for the
  owner-only `GET /clinics/{id}/demo-inbox`, and real email refused to reserved test domains.
  Nothing calls it yet: invites and sign-in codes arrive with the account PRs.

Not built: auto-refreshing ("rolling") summaries -- by decision, summaries are on demand only --
and storing summaries (see above).
Tests and lint (`pytest`, `ruff`) run in CI.

## Hard constraints (not negotiable without a product conversation first)

- **No hospital chart or ConnectingOntario data, at all, in any form.** Not a v1
  scope note — an explicit line Sarah drew. If a feature seems to need hospital data,
  stop and raise it rather than building toward it.
- **Structured check-ins, not free text, for the patient side.** Messages remain
  free-form text from both sides (decision 5 in decisions-and-open-questions.md);
  check-ins are dropdown + checkboxes only, because many patients struggle to
  describe symptoms in words.
- **AI never auto-sends.** Drafts are reviewed and approved by the clinician; the
  audit log records who approved what, when, from which protocol/template version.
- **Inference location is still an open question** (local vs. Canadian-region
  zero-retention vs. minimal-fact cloud) — see data-catalogue.md's open items. Don't
  wire up a specific LLM vendor without checking whether that decision has been made
  since this doc was written.
- **Synthetic data only** until the privacy review is done. Nothing in this service
  should be pointed at a real patient yet.

## Near-term backlog (roughly in order)

1. ~~Check-in intake endpoint + link-token validation.~~ Done.
2. ~~Red-flag rule engine~~ Placeholder done; needs Sarah's list (start with a hardcoded rule set; make it clinician-editable
   later, once there's a clinician using it).
3. ~~Message send/receive endpoints~~ Done, both sides.
4. ~~Thread summary generation, with per-line provenance links back to source messages.~~ Done,
   on demand, with every citation verified (not stored; no auto-refresh by decision).
5. ~~Audit log writer~~ Done for check-ins, messages, patients, links, consent and AI drafts.
   Wire every new write through it (summaries) rather than bolting it on later, and add
   its (table, event) pair to `CHECKS` in `core/reconcile.py`.

## Reference

- `../../claude/data-catalogue.md` — the full data classification (what's stored,
  where, how long, at what sensitivity). D29 (check-in answers), D6/D7 (messages),
  D16/D17 (audit log) are the ones this service touches first.
- `../../claude/decisions-and-open-questions.md` — product decisions and what's still
  open. Read the "Decisions" section before assuming how something should behave.
