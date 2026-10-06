"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import {
  ManualTouchpointKind,
  Message,
  Touchpoint,
  TouchpointKind,
  logTouchpoint,
} from "@/lib/clinician-api";
import { contactStats, duration, span } from "@/lib/contact-stats";
import { clock, shortDate } from "@/lib/format";

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

/** The Contact card from the overview spec: how much contact this patient has had, what
 *  is waiting on you, and the time you have put in. Every figure is counted from the
 *  thread and the contact log (messages are counted automatically; calls, visits and
 *  emails are entered by the clinician), so none of it is a model's guess.
 *
 *  Not shown yet, because the data doesn't exist: post-op day on each contact (the
 *  procedure date isn't stored) and time measured inside the app (not tracked). */
export function ContactLogSection({
  clinicId,
  patientId,
  touchpoints,
  thread,
  onChanged,
}: {
  clinicId: string;
  patientId: string;
  touchpoints: Touchpoint[] | null;
  thread: Message[] | null;
  onChanged: () => void;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [kind, setKind] = useState<ManualTouchpointKind>("call");
  const [minutes, setMinutes] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleLog(e: React.FormEvent) {
    e.preventDefault();
    const mins = minutes.trim() === "" ? undefined : Number(minutes);
    if (mins !== undefined && (!Number.isInteger(mins) || mins < 1 || mins > 1440)) {
      setError("Duration must be a whole number of minutes, 1 to 1440.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await logTouchpoint(clinicId, patientId, {
        kind,
        duration_minutes: mins,
        note: note.trim() || undefined,
      });
      setMinutes("");
      setNote("");
      setFormOpen(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't log that contact.");
    } finally {
      setBusy(false);
    }
  }

  const loaded = touchpoints !== null && thread !== null;
  const s = loaded ? contactStats(thread, touchpoints) : null;

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">Contact</h2>
      <div className="rounded-lg border border-border bg-surface p-4">
        {s === null ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : (
          <>
            <div className="mb-3 flex justify-end">
              <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-xs text-muted">
                counted
              </span>
            </div>
            <div className="grid grid-cols-3 gap-x-4 gap-y-4">
              <Stat
                value={String(s.messagesTotal)}
                label={`messages · ${s.fromPatient} patient / ${s.fromClinic} you`}
              />
              <Stat
                value={String(s.visits)}
                label={`clinic ${s.visits === 1 ? "visit" : "visits"}${
                  s.lastVisitAt ? ` · ${shortDate(s.lastVisitAt)}` : ""
                }`}
              />
              <Stat
                value={String(s.calls)}
                label={[
                  s.calls === 1 ? "call" : "calls",
                  s.callMinutes > 0 ? `${s.callMinutes} min` : null,
                  s.lastCallAt ? shortDate(s.lastCallAt) : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              />
              <Stat
                value={s.firstContactAt ? shortDate(s.firstContactAt) : "-"}
                label="first contact"
              />
              <Stat
                value={s.lastContactAt ? span(s.lastContactAt) : "-"}
                label="since last contact"
              />
              <Stat
                value={String(s.awaitingReply)}
                label="awaiting your reply"
                tone={s.awaitingReply > 0 ? "warn" : "normal"}
              />
              {s.emails > 0 && (
                <Stat value={String(s.emails)} label={s.emails === 1 ? "email" : "emails"} />
              )}
            </div>

            <div className="mt-4 rounded-md border border-border bg-surface-subtle p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted">
                Your time on this patient
              </p>
              <div className="mt-2 flex flex-wrap items-baseline gap-x-3 text-sm">
                {s.enteredEntries > 0 ? (
                  <>
                    <span className="font-mono font-semibold text-ink">
                      {s.enteredMinutes} min
                    </span>
                    <span className="text-muted">
                      across {s.enteredEntries} logged{" "}
                      {s.enteredEntries === 1 ? "entry" : "entries"}
                    </span>
                    <span className="rounded-full border border-dashed border-border-strong px-2 py-0.5 text-xs text-muted">
                      entered
                    </span>
                  </>
                ) : (
                  <span className="text-muted">No time entered yet.</span>
                )}
              </div>
              {s.medianReplyMs !== null && (
                <p className="mt-2 text-right font-mono text-xs text-muted">
                  median reply {duration(s.medianReplyMs)}
                </p>
              )}
            </div>
            <p className="mt-3 text-xs text-muted">
              Coverage: messages counted automatically · calls, visits and emails entered by you ·
              time in the app is not measured
            </p>
          </>
        )}

        <div className="mt-4 border-t border-border pt-3">
          {formOpen ? (
            <form onSubmit={handleLog} className="space-y-2">
              <div className="flex flex-wrap gap-2">
                <select
                  value={kind}
                  onChange={(e) => setKind(e.target.value as ManualTouchpointKind)}
                  aria-label="Type of contact"
                  className="rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
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
                  className="w-44 rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
                />
              </div>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                maxLength={5000}
                aria-label="Note (optional)"
                placeholder="Note (optional)"
                className="block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
              />
              {error && <p className="text-sm text-flag-text">{error}</p>}
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={busy}
                  className="rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
                >
                  {busy ? "Saving..." : "Log contact"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setFormOpen(false);
                    setError(null);
                  }}
                  className="rounded-md border border-primary bg-surface text-primary enabled:hover:bg-primary-tint px-4 py-2 text-sm font-medium"
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <button
              onClick={() => setFormOpen(true)}
              className="rounded-md border border-primary bg-surface text-primary enabled:hover:bg-primary-tint px-3 py-2 text-sm font-medium"
            >
              Log a call, visit or email
            </button>
          )}
        </div>

        {touchpoints !== null && (
          <details className="mt-3 border-t border-border pt-3">
            <summary className="cursor-pointer text-sm text-muted">
              Contact history ({touchpoints.length})
            </summary>
            {touchpoints.length === 0 ? (
              <p className="mt-2 text-sm text-muted">No contact logged yet.</p>
            ) : (
              <ul className="mt-2 divide-y divide-border">
                {touchpoints.map((t) => (
                  <li key={t.id} className="py-2">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium text-ink">{KIND_LABEL[t.kind]}</span>
                      <span className="text-muted">{clock(t.occurred_at)}</span>
                      {t.duration_minutes !== null && (
                        <span className="text-muted">&middot; {t.duration_minutes} min</span>
                      )}
                      <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-xs text-muted">
                        {t.source}
                      </span>
                    </div>
                    {t.note && (
                      <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{t.note}</p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </details>
        )}
      </div>
    </section>
  );
}

function Stat({
  value,
  label,
  tone = "normal",
}: {
  value: string;
  label: string;
  tone?: "normal" | "warn";
}) {
  return (
    <div>
      <p
        className={`font-mono text-2xl font-semibold tabular-nums ${
          tone === "warn" ? "text-watch-text" : "text-ink"
        }`}
      >
        {value}
      </p>
      <p className="text-xs text-muted">{label}</p>
    </div>
  );
}
