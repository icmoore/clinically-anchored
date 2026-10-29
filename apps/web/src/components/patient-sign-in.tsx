"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, requestSignInCode, verifySignInCode } from "@/lib/api";
import { PATIENT_TEXT } from "@/lib/patient-strings";
import { savePatientSession, usePatientSignIn } from "@/lib/patient-session";
import { FormError, buttonClass, inputClass, linkButtonClass } from "./patient-form";
import { PatientFrame } from "./patient-frame";

const T = PATIENT_TEXT.signIn;

/** Sign in with an email and a code. No password. Two steps on one page: the email, then the code
 *  that was sent to it. Nothing here says whether an email has an account: the api answers every
 *  request for a code the same way, and so does this page. A device that is already signed in goes
 *  straight on. */
export function PatientSignIn() {
  const router = useRouter();
  const signedIn = usePatientSignIn();
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (signedIn === "signed-in") router.replace("/care");
  }, [signedIn, router]);

  async function sendCode(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await requestSignInCode(email.trim());
      setStep("code");
      setCode("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError(T.tooMany);
      else if (err instanceof ApiError && err.status === 422) setError(T.invalidEmail);
      else if (err instanceof ApiError && err.status === 503) setError(T.unavailable);
      else setError(T.couldntSend);
    } finally {
      setBusy(false);
    }
  }

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await verifySignInCode(email.trim(), code.replace(/\D/g, ""));
      await savePatientSession(session);
      router.replace("/care");
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError(T.tooMany);
      else if (err instanceof ApiError && err.status === 401) setError(T.codeFailed);
      else if (err instanceof ApiError && err.status === 503) setError(T.unavailable);
      else setError(T.couldntSend);
      setBusy(false);
    }
  }

  if (signedIn !== "signed-out") {
    return <p className="p-6 text-muted">Loading...</p>;
  }

  if (step === "email") {
    return (
      <PatientFrame title={T.title} underNav>
        <p className="text-ink">{T.intro}</p>
        <form onSubmit={sendCode} className="mt-4">
          <label htmlFor="email" className="block text-sm font-medium text-ink">
            {T.emailLabel}
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            inputMode="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputClass}
          />
          {error && <FormError>{error}</FormError>}
          <button type="submit" disabled={busy || !email.trim()} className={`${buttonClass} mt-4`}>
            {busy ? T.sending : T.sendCode}
          </button>
        </form>
        <p className="mt-4 text-sm text-muted">{T.noEmail}</p>
        <p className="mt-4 text-sm text-muted">
          {T.careTeamPrompt}{" "}
          <Link href="/login" className="text-link underline hover:text-link-hover">
            {T.careTeamLink}
          </Link>
        </p>
      </PatientFrame>
    );
  }

  return (
    <PatientFrame title={T.codeTitle} underNav>
      <p className="text-ink">{T.codeSent}</p>
      <p className="mt-1 text-sm text-muted">{T.codeValid}</p>
      <form onSubmit={signIn} className="mt-4">
        <label htmlFor="code" className="block text-sm font-medium text-ink">
          {T.codeLabel}
        </label>
        <input
          id="code"
          required
          autoComplete="one-time-code"
          inputMode="numeric"
          pattern="[0-9 ]*"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className={`${inputClass} text-xl tracking-widest`}
        />
        {error && <FormError>{error}</FormError>}
        <button
          type="submit"
          disabled={busy || code.replace(/\D/g, "").length < 6}
          className={`${buttonClass} mt-4`}
        >
          {busy ? T.signingIn : T.signIn}
        </button>
      </form>
      <p className="mt-3 text-sm text-muted">{T.staySignedIn}</p>
      <div className="mt-4 flex flex-col items-start gap-2">
        <button type="button" onClick={() => sendCode()} disabled={busy} className={linkButtonClass}>
          {T.newCode}
        </button>
        <button
          type="button"
          onClick={() => {
            setStep("email");
            setError(null);
          }}
          className={linkButtonClass}
        >
          {T.otherEmail}
        </button>
      </div>
    </PatientFrame>
  );
}
