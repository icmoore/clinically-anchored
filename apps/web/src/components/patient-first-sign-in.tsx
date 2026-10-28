"use client";

import { useState } from "react";
import { ApiError, PatientAccount, acceptConsents, confirmSurgeryDate } from "@/lib/api";
import { PATIENT_TEXT } from "@/lib/patient-strings";
import { FormError, buttonClass, inputClass } from "./patient-form";
import { PatientFrame } from "./patient-frame";

/** Today as YYYY-MM-DD on this device: a surgery date cannot be in the future for a patient who
 *  has had their surgery (the api is the one that decides whether a date matches). */
const today = () => new Date().toLocaleDateString("en-CA");

/** First sign-in, step 1: "enter your surgery date". Asked once, to be sure it is the patient. A
 *  wrong date says how many tries are left; the third locks the account. */
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
        setError(T.wrong(err.body.attempts_left));
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
          className={inputClass}
        />
        {error && <FormError>{error}</FormError>}
        <button type="submit" disabled={busy || !date} className={`${buttonClass} mt-4`}>
          {busy ? T.checking : T.button}
        </button>
      </form>
    </PatientFrame>
  );
}

/** First sign-in, step 2: agree to messaging and to AI-assisted replies. Both are needed. What is
 *  sent back is the wording version the patient was shown (lib/patient-strings.ts), which the api
 *  stores with the answer. */
export function ConsentScreen({
  account,
  onAccount,
}: {
  account: PatientAccount;
  onAccount: (account: PatientAccount) => void;
}) {
  const T = PATIENT_TEXT.consent;
  const [agreed, setAgreed] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The words on screen are for T.version. If the api expects another, say so rather than
  // record an agreement to words the patient did not read.
  const outdated = account.consent_version !== T.version;
  const all = T.items.every((item) => agreed[item.key]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onAccount(await acceptConsents(T.version, T.items.map((item) => item.key)));
    } catch {
      setError(T.couldntSave);
      setBusy(false);
    }
  }

  return (
    <PatientFrame title={T.title} phone={account.office_phone}>
      <p className="text-ink">{T.intro}</p>
      {outdated ? (
        <FormError>{T.reload}</FormError>
      ) : (
        <form onSubmit={submit} className="mt-4">
          <div className="space-y-2">
            {T.items.map((item) => (
              <label
                key={item.key}
                className={`flex items-start gap-3 rounded-md border px-3 py-3 text-sm text-ink ${
                  agreed[item.key] ? "border-primary bg-primary-tint" : "border-border bg-surface"
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
          <button type="submit" disabled={busy || !all} className={`${buttonClass} mt-4`}>
            {busy ? T.saving : T.button}
          </button>
        </form>
      )}
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
