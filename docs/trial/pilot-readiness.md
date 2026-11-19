# Clinically Anchored: Pilot readiness (first real patients)

Target: first handful of real patients around 2026-11-13 (Ian, 2026-10-30), after two weeks of stress testing and adjustments.

Until every **blocker** below is done, the app holds synthetic data only.

## Blockers (no real patient until done)

1. **AI and data residency (decision 10).** Bedrock reaches Claude through a cross-region profile that can process in US regions. Decision 10's hard gate says no real patient text goes to it until resolved. Practical route for the pilot: **turn AI features off for the real clinic** (a per-clinic switch: AI drafts, Interaction Summary, AI patient summary, care provider summary), unless Sarah, as custodian, signs off in writing on cross-border processing after a PHIPA review. The rest of the product (check-in, red flags, scheduled messaging, computed Physician Summary) works without AI.
2. **Separate production database.** Dev, demo and production share one Supabase project, and the reset script deletes every patient in every clinic. Real patients need their own Supabase project (Pro plan, `ca-central-1`, daily backups or point-in-time recovery), with the reset script unable to run against it. Re-point Railway and Vercel production at it; keep the current project for dev and demo.
3. **Email that reaches patients.** SES is in sandbox (it only delivers to verified addresses, so real patients would get no codes) and the sender is a Gmail address (spam). Needs: a domain, SPF/DKIM/DMARC, SES production access. Test delivery to Gmail, Outlook/Hotmail, Yahoo and iCloud.
4. **Account settings:** Supabase public sign-ups off; Send Email hook and secret configured; `TRUSTED_PROXY_HOPS=1` on Railway.
5. **Clinical content validated by Sarah:** red-flag rules (`core/rules.py` still says placeholder, not for real patients), check-in questions and procedure categories, message categories, patient-screen wording, urgent notice, consent text, the patient guide. Office phone set.
6. **PHIPA paperwork:** a written agent/service agreement between Sarah's practice (custodian) and the provider (Ian / EchoLedger), a patient-facing privacy notice, and a breach-response plan (who is told, how fast).
7. **Research or clinical service?** If any results from these patients may be published or presented, REB approval, disclosure of the family conflict and trial registration must come before the first patient is enrolled (see the pilot study section of the decision log). If it's purely clinical quality improvement, write that down.

## Should do before go-live

8. **Baseline metrics** (decision 8): office calls, callbacks and follow-up visits for comparable patients before the app. Can't be recovered later.
9. **Monitoring:** an uptime check on the API and web app, alerts on API errors, and a named person who looks when it breaks. Patients told what to do if the app is down (call the office).
10. **Restore drill** on the production project: take a backup, restore it to a scratch project, confirm the audit chain verifies.
11. **Audit signing key** out of a Railway env var into a key-management store (hardening list).
12. **GitHub branch protection** on `main`: required checks before merge.
13. **Real phones:** iPhone Safari and an Android phone, end to end.
14. **Response expectations** agreed with Sarah: how often she checks, and what patients are told.

## Stress-testing plan (the two weeks)

- **Dress rehearsal:** Ian, Sarah and two or three family members act as patients on their own phones in the dev clinic, invited by real email, through check-in, a red-flag check-in, messages in several windows, a doctor reply and reply, discharge, and an extension request.
- **Time travel:** patients seeded with back-dated surgery dates so every window, the day-59 end and the extension can be seen without waiting.
- **Edge cases:** wrong surgery date three times, expired code, two devices, sign-out on a shared phone, changed email, no email (single-use link), clock change on 2026-11-01.
- **Failure cases:** API down (what patients see), slow network, a deploy while someone is mid-check-in.
- **Load is not a concern** at a handful of patients; correctness and clarity are.
- Log every problem found, fix the clear ones, and leave anything clinical for Sarah.

The decision log referred to above (decision 8, decision 10, the pilot study section) lives in the claude.ai Project, not in this repo.
