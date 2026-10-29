"use client";

import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore } from "react";
import { ApiError, redeemFallbackLink } from "@/lib/api";
import { PATIENT_TEXT } from "@/lib/patient-strings";
import { savePatientSession } from "@/lib/patient-session";
import { FormError, buttonClass, inputClass } from "./auth-form";
import { LockedScreen } from "./patient-first-sign-in";
import { PatientFrame } from "./patient-frame";

const T = PATIENT_TEXT.link;
const IDENTITY = PATIENT_TEXT.identity;

// The link's secret is in the part of the address after "#", which the browser never sends to any
// server. `null` until the browser has been asked (server render).
const subscribe = (notify: () => void) => {
  window.addEventListener("hashchange", notify);
  return () => window.removeEventListener("hashchange", notify);
};
const readSecret = () => window.location.hash.slice(1);

/** The single-use link the office gives a patient who has no email. It works once, and it asks for
 *  the surgery date every time it is opened: a link that was forwarded or seen is not enough on its
 *  own. A wrong date does not use the link up; three wrong dates lock the account. */
export function PatientLinkSignIn() {
  const router = useRouter();
  const secret = useSyncExternalStore<string | null>(subscribe, readSecret, () => null);
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!secret) return;
    setBusy(true);
    setError(null);
    try {
      await savePatientSession(await redeemFallbackLink(secret, date));
      router.replace("/care");
    } catch (err) {
      if (err instanceof ApiError && err.body.locked === true) {
        setLocked(true);
        return;
      }
      if (err instanceof ApiError && err.status === 422 && typeof err.body.attempts_left === "number") {
        setError(IDENTITY.wrong(err.body.attempts_left));
      } else if (err instanceof ApiError && err.status === 401) {
        setError(T.expired);
      } else {
        setError(T.couldntSignIn);
      }
      setBusy(false);
    }
  }

  if (secret === null) return <p className="p-6 text-muted">Loading...</p>;
  if (locked) return <LockedScreen phone={null} />;
  if (!secret) {
    return (
      <PatientFrame title={T.title}>
        <p className="text-ink">{T.incomplete}</p>
      </PatientFrame>
    );
  }

  return (
    <PatientFrame title={T.title}>
      <p className="text-ink">{T.intro}</p>
      <form onSubmit={submit} className="mt-4">
        <label htmlFor="surgery-date" className="block text-sm font-medium text-ink">
          {IDENTITY.label}
        </label>
        <input
          id="surgery-date"
          type="date"
          required
          max={new Date().toLocaleDateString("en-CA")}
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className={inputClass}
        />
        {error && <FormError>{error}</FormError>}
        <button type="submit" disabled={busy || !date} className={`${buttonClass} mt-4`}>
          {busy ? PATIENT_TEXT.signIn.signingIn : T.button}
        </button>
      </form>
    </PatientFrame>
  );
}
