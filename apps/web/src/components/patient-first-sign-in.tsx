"use client";

import { useState } from "react";
import { ApiError, PatientAccount, answerBeforeYouStart, confirmSurgeryDate } from "@/lib/api";
import { LEGAL, consentText, consentVersion, sha256Hex } from "@/lib/legal";
import { PATIENT_TEXT } from "@/lib/patient-strings";
import { signOutPatient } from "@/lib/patient-session";
import { FormError, buttonClass, inputClass } from "./auth-form";
import { PatientFrame } from "./patient-frame";

/** Today as YYYY-MM-DD on this device: a surgery date cannot be in the future for a patient who
 *  has had their surgery (the api is the one that decides whether a date matches). */
const today = () => new Date().toLocaleDateString("en-CA");

/** First sign-in, step 1: "enter your surgery date". Asked once, to be sure it is the patient. A
 *  wrong date says how many tries are left and the office phone; the third wrong one locks the
 *  account. */
export function IdentityScreen({
  account,
  onAccount,
}: {
  account: PatientAccount;
  onAccount: (account: PatientAccount) => void;
}) {
  const T = PATIENT_TEXT.identity;
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onAccount(await confirmSurgeryDate(date));
    } catch (err) {
      if (err instanceof ApiError && err.status === 422 && typeof err.body.attempts_left === "number") {
        setError(T.wrongWithHelp(err.body.attempts_left, account.office_phone));
      } else if (err instanceof ApiError && err.body.locked === true) {
        onAccount({ ...account, status: "locked" });
        return;
      } else {
        setError(T.couldntCheck);
      }
      setBusy(false);
    }
  }

  return (
    <PatientFrame title={T.title} phone={account.office_phone}>
      <p className="text-ink">{T.intro}</p>
      <form onSubmit={submit} className="mt-4">
        <label htmlFor="surgery-date" className="block text-sm font-medium text-ink">
          {T.label}
        </label>
        <input
          id="surgery-date"
          type="date"
          required
          max={today()}
          value={date}
          onChange={(e) => setDate(e.target.value)}
          aria-describedby="surgery-date-example"
          className={inputClass}
        />
        <p id="surgery-date-example" className="mt-1 text-sm text-muted">
          {T.example}
        </p>
        {error && <FormError>{error}</FormError>}
        <button type="submit" disabled={busy || !date} className={`${buttonClass} mt-4`}>
          {busy ? T.checking : T.button}
        </button>
      </form>
    </PatientFrame>
  );
}

/** "Before you start": the Terms, the Privacy Notice and the two consents, with two separate boxes
 *  (neither ticked for the patient) and Continue only once both are ticked. The same screen comes
 *  back after the documents change (`account.needs_acceptance` on an active patient). What is sent
 *  with an answer is the versions shown and the hash of the consent sentences shown
 *  (lib/legal.ts); the api checks both against its own and stores its own hashes. "I don't want to
 *  use the app" records a decline and signs the patient out. Both boxes are also required by the
 *  api, not only here. */
export function AcceptanceScreen({
  account,
  onAccount,
  onSignedOut,
}: {
  account: PatientAccount;
  onAccount: (account: PatientAccount) => void;
  /** After a decline, the patient is signed out: the gate shows the declined message. */
  onSignedOut: () => void;
}) {
  const T = PATIENT_TEXT.acceptance;
  const [agreed, setAgreed] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<"accept" | "decline" | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The words on screen are for the versions in lib/legal.ts. If the api expects others (the web app
  // and the api were updated at different moments), say so rather than record an answer to words
  // the patient did not read.
  const outdated =
    account.terms_version !== LEGAL.terms.version ||
    account.privacy_version !== LEGAL.privacy.version ||
    account.consent_version !== consentVersion();
  const all = T.items.every((item) => agreed[item.key]);
  // An active patient is here because the documents changed; a new patient is here for the first time.
  const changed = account.status === "active";

  async function answer(action: "accepted" | "declined") {
    setBusy(action === "accepted" ? "accept" : "decline");
    setError(null);
    try {
      const updated = await answerBeforeYouStart({
        action,
        terms_version: LEGAL.terms.version,
        privacy_version: LEGAL.privacy.version,
        consent_version: consentVersion(),
        consent_sha256: await sha256Hex(consentText()),
        consents: action === "accepted" ? T.items.map((item) => item.key) : [],
      });
      if (action === "declined") {
        await signOutPatient();
        onSignedOut();
        return;
      }
      onAccount(updated);
    } catch {
      setError(T.couldntSave);
      setBusy(null);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (all) void answer("accepted");
  }

  return (
    <PatientFrame title={T.title} phone={account.office_phone}>
      <p className="text-ink">{changed ? T.changed : T.intro}</p>
      {outdated ? (
        <FormError>{T.reload}</FormError>
      ) : (
        <form onSubmit={submit} className="mt-4">
          <ul className="space-y-1 text-sm">
            {[
              { href: "/terms", label: T.readTerms },
              { href: "/privacy", label: T.readPrivacy },
            ].map((doc) => (
              <li key={doc.href}>
                <a
                  href={doc.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-link underline hover:text-link-hover"
                >
                  {doc.label}
                </a>{" "}
                <span className="text-muted">{T.opensNewTab}</span>
              </li>
            ))}
          </ul>
          <div className="mt-4 space-y-2">
            {T.items.map((item) => (
              <label
                key={item.key}
                className={`flex items-start gap-3 rounded-md border px-3 py-3 text-sm text-ink ${
                  agreed[item.key] ? "border-link bg-primary-tint" : "border-border bg-surface"
                }`}
              >
                <input
                  type="checkbox"
                  aria-labelledby={`consent-${item.key}`}
                  checked={agreed[item.key] ?? false}
                  onChange={(e) => setAgreed({ ...agreed, [item.key]: e.target.checked })}
                  className="mt-0.5 h-5 w-5 shrink-0"
                />
                <span id={`consent-${item.key}`}>{item.text}</span>
              </label>
            ))}
          </div>
          {error && <FormError>{error}</FormError>}
          <button
            type="submit"
            disabled={busy !== null || !all}
            aria-describedby={all ? undefined : "need-both"}
            className={`${buttonClass} mt-4`}
          >
            {busy === "accept" ? T.saving : T.button}
          </button>
          {!all && (
            <p id="need-both" className="mt-2 text-sm text-muted">
              {T.needBoth}
            </p>
          )}
          <p className="mt-6 text-center">
            <button
              type="button"
              onClick={() => void answer("declined")}
              disabled={busy !== null}
              className="text-sm text-link underline hover:text-link-hover disabled:opacity-60"
            >
              {busy === "decline" ? T.declining : T.decline}
            </button>
          </p>
        </form>
      )}
    </PatientFrame>
  );
}

/** After "I don't want to use the app": nothing but the message. The patient is already signed out. */
export function DeclinedScreen() {
  const T = PATIENT_TEXT.acceptance.declined;
  return (
    <PatientFrame title={T.title}>
      <p className="text-ink">{T.body}</p>
    </PatientFrame>
  );
}

/** After "Stop using the app": the patient is signed out and their messages are turned off. */
export function StoppedScreen() {
  const T = PATIENT_TEXT.stopUsing.done;
  return (
    <PatientFrame title={T.title}>
      <p className="text-ink">{T.body}</p>
    </PatientFrame>
  );
}

/** Three wrong surgery dates. Only the office can unlock it, so the screen says to call. */
export function LockedScreen({ phone }: { phone: string | null }) {
  const T = PATIENT_TEXT.locked;
  return (
    <PatientFrame title={T.title} phone={phone} noticeFirst>
      <p className="text-ink">{T.body}</p>
    </PatientFrame>
  );
}

/** The office turned sign-in off for this account. */
export function TurnedOffScreen({ phone }: { phone: string | null }) {
  const T = PATIENT_TEXT.turnedOff;
  return (
    <PatientFrame title={T.title} phone={phone} noticeFirst>
      <p className="text-ink">{T.body}</p>
    </PatientFrame>
  );
}
