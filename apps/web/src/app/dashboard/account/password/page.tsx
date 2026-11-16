"use client";

import Link from "next/link";
import { useState } from "react";
import { AppHeader } from "@/components/app-header";
import { FormError, buttonClass, inputClass, labelClass } from "@/components/auth-form";
import { ClinicianProblem } from "@/components/clinician-problem";
import { MIN_PASSWORD_LENGTH, changePassword } from "@/lib/change-password";
import { recordPasswordChanged } from "@/lib/clinician-api";
import { supabase } from "@/lib/supabase";
import { useClinician } from "@/lib/use-clinician";

/** A clinician changes their own password: the current one is checked by signing in with it again,
 *  then the new one is set (lib/change-password.ts). No email is involved. */
export default function ChangePasswordPage() {
  const clinician = useClinician();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ recorded: boolean } | null>(null);

  if (clinician.status === "loading") return <p className="p-6 text-muted">Loading...</p>;
  if (clinician.status === "error") return <ClinicianProblem problem={clinician} />;
  const { email } = clinician;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) {
      setError("Your account has no email address to check the password against.");
      return;
    }
    setBusy(true);
    setError(null);
    const result = await changePassword(
      supabase.auth,
      email,
      { current, next, confirm },
      recordPasswordChanged,
    );
    setBusy(false);
    // Whatever happened, the typed passwords do not stay in the form.
    setCurrent("");
    setNext("");
    setConfirm("");
    if (result.ok) setDone({ recorded: result.recorded });
    else setError(result.message);
  }

  return (
    <>
      <AppHeader clinician={clinician} />
      <main className="mx-auto w-full max-w-3xl space-y-4 px-4 py-6">
        <Link href="/dashboard" className="text-sm text-muted hover:underline">
          &larr; Dashboard
        </Link>
        <h1 className="text-xl font-semibold text-ink">Change password</h1>
        {done ? (
          <div role="status" className="space-y-2 text-ink">
            <p>Your password was changed.</p>
            {!done.recorded && (
              <p className="text-flag-text">
                The change could not be written to the audit log. It still took effect; tell the
                person who runs the system.
              </p>
            )}
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="max-w-md space-y-4">
            <label className="block">
              <span className={labelClass}>Current password</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                className={inputClass}
              />
            </label>
            <label className="block">
              <span className={labelClass}>New password</span>
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={MIN_PASSWORD_LENGTH}
                value={next}
                onChange={(e) => setNext(e.target.value)}
                className={inputClass}
              />
              <span className="mt-1 block text-xs text-muted">
                At least {MIN_PASSWORD_LENGTH} characters.
              </span>
            </label>
            <label className="block">
              <span className={labelClass}>New password again</span>
              <input
                type="password"
                autoComplete="new-password"
                required
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className={inputClass}
              />
            </label>
            {error && <FormError>{error}</FormError>}
            <button type="submit" disabled={busy} className={buttonClass}>
              {busy ? "Changing..." : "Change password"}
            </button>
          </form>
        )}
      </main>
    </>
  );
}
