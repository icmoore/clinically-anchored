"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import {
  ManualTouchpointKind,
  Touchpoint,
  TouchpointKind,
  logTouchpoint,
} from "@/lib/clinician-api";
import { clock } from "@/lib/format";

const KINDS: { value: ManualTouchpointKind; label: string }[] = [
  { value: "call", label: "Call" },
  { value: "visit", label: "Visit" },
  { value: "email", label: "Email" },
];

const KIND_LABEL: Record<TouchpointKind, string> = {
  call: "Call",
  visit: "Visit",
  email: "Email",
  message: "Message",
};

/** Every contact with this patient: calls, visits and emails logged here by hand, and
 *  a "message" row the api adds on its own each time a clinician message is sent. */
export function ContactLogSection({
  clinicId,
  patientId,
  touchpoints,
  onChanged,
}: {
  clinicId: string;
  patientId: string;
  touchpoints: Touchpoint[] | null;
  onChanged: () => void;
}) {
  const [kind, setKind] = useState<ManualTouchpointKind>("call");
  const [minutes, setMinutes] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLog(e: React.FormEvent) {
    e.preventDefault();
    const duration = minutes.trim() === "" ? undefined : Number(minutes);
    if (duration !== undefined && (!Number.isInteger(duration) || duration < 1 || duration > 1440)) {
      setError("Duration must be a whole number of minutes, 1 to 1440.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await logTouchpoint(clinicId, patientId, {
        kind,
        duration_minutes: duration,
        note: note.trim() || undefined,
      });
      setMinutes("");
      setNote("");
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't log that contact.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
        Contact log
      </h2>
      <div className="rounded-lg border border-zinc-200 bg-white">
        <form onSubmit={handleLog} className="space-y-2 p-3">
          <div className="flex flex-wrap gap-2">
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as ManualTouchpointKind)}
              aria-label="Type of contact"
              className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-base"
            >
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
            <input
              type="number"
              inputMode="numeric"
              min={1}
              max={1440}
              step={1}
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              aria-label="Duration in minutes (optional)"
              placeholder="Minutes (optional)"
              className="w-44 rounded-md border border-zinc-300 px-3 py-2 text-base"
            />
          </div>
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={5000}
            aria-label="Note (optional)"
            placeholder="Note (optional)"
            className="block w-full rounded-md border border-zinc-300 px-3 py-2 text-base"
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {busy ? "Saving..." : "Log contact"}
          </button>
        </form>
        <div className="border-t border-zinc-200 p-3">
          {touchpoints === null ? (
            <p className="text-sm text-zinc-600">Loading...</p>
          ) : touchpoints.length === 0 ? (
            <p className="text-sm text-zinc-600">No contact logged yet.</p>
          ) : (
            <ul className="divide-y divide-zinc-100">
              {touchpoints.map((t) => (
                <li key={t.id} className="py-2">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="font-medium text-zinc-900">{KIND_LABEL[t.kind]}</span>
                    <span className="text-zinc-500">{clock(t.occurred_at)}</span>
                    {t.duration_minutes !== null && (
                      <span className="text-zinc-500">&middot; {t.duration_minutes} min</span>
                    )}
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${
                        t.source === "auto"
                          ? "bg-blue-100 text-blue-800"
                          : "bg-zinc-100 text-zinc-700"
                      }`}
                    >
                      {t.source}
                    </span>
                  </div>
                  {t.note && (
                    <p className="mt-1 whitespace-pre-wrap text-sm text-zinc-700">{t.note}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
