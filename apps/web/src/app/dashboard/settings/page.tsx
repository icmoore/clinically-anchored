"use client";

import Link from "next/link";
import { useState } from "react";
import { AppHeader } from "@/components/app-header";
import { ApiError } from "@/lib/api";
import { Clinic, setOfficePhone } from "@/lib/clinician-api";
import { useClinician } from "@/lib/use-clinician";

export default function SettingsPage() {
  const clinician = useClinician();
  if (clinician.status === "loading") return <p className="p-6 text-muted">Loading...</p>;
  if (clinician.status === "error") return <p className="p-6 text-flag-text">{clinician.message}</p>;
  return (
    <>
      <AppHeader clinician={clinician} />
      <main className="mx-auto max-w-3xl space-y-8 px-4 py-6">
        <div>
          <Link href="/dashboard" className="text-sm text-muted hover:underline">
            &larr; Dashboard
          </Link>
          <h1 className="mt-1 text-xl font-semibold text-ink">Clinic settings</h1>
          <p className="text-sm text-muted">{clinician.clinic.name}</p>
        </div>
        <OfficePhone clinic={clinician.clinic} />
      </main>
    </>
  );
}

/** The number patients are told to call on every urgent notice. Anyone in the clinic can see it;
 *  only an owner can change it (the api enforces that, this just doesn't offer the form). */
function OfficePhone({ clinic }: { clinic: Clinic }) {
  const canEdit = clinic.role === "owner";
  const [saved, setSaved] = useState<string | null>(clinic.office_phone);
  const [text, setText] = useState(clinic.office_phone ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function save(value: string | null) {
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      const updated = await setOfficePhone(clinic.id, value);
      setSaved(updated.office_phone);
      setText(updated.office_phone ?? "");
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the phone number.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">Office phone number</h2>
      <p className="mt-1 text-sm text-muted">
        Shown to patients on every urgent notice (&ldquo;call your surgeon&apos;s office at
        &hellip; or go to the nearest emergency department&rdquo;), including when messaging is
        locked or their follow-up has ended.
      </p>

      {saved === null && (
        <p
          role="alert"
          className="mt-3 rounded-md border border-watch-border bg-watch-bg px-3 py-2 text-sm text-watch-text"
        >
          No number is set. Patients are told to call the office but are not shown a number.
        </p>
      )}

      {canEdit ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            save(text.trim() === "" ? null : text);
          }}
          className="mt-3 space-y-2"
        >
          <label htmlFor="office-phone" className="block text-xs text-muted">
            North American number, for example 905-555-0100
          </label>
          <div className="flex flex-wrap gap-2">
            <input
              id="office-phone"
              type="tel"
              inputMode="tel"
              autoComplete="off"
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                setDone(false);
              }}
              className="min-w-0 flex-1 rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
            />
            <button
              type="submit"
              disabled={busy || text.trim() === (saved ?? "")}
              className="rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-base font-medium text-white disabled:opacity-60"
            >
              {busy ? "Saving..." : text.trim() === "" && saved ? "Remove number" : "Save"}
            </button>
          </div>
          {error && <p className="text-sm text-flag-text">{error}</p>}
          {done && (
            <p className="text-sm text-clear-text">
              {saved ? `Saved. Patients now see ${saved}.` : "Removed. Patients see no number."}
            </p>
          )}
        </form>
      ) : (
        <>
          <p className="mt-3 text-base text-ink">{saved ?? "Not set"}</p>
          <p className="mt-1 text-xs text-muted">Only a clinic owner can change this.</p>
        </>
      )}
    </section>
  );
}
