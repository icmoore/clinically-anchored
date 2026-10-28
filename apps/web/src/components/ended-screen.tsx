"use client";

import { useState } from "react";
import { ApiError, PatientExtension, requestExtension } from "@/lib/api";
import { plainDay } from "@/lib/format";
import { PATIENT_TEXT } from "@/lib/patient-strings";
import { PatientFrame } from "./patient-frame";

const T = PATIENT_TEXT.extension;
const buttonClass =
  "rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2.5 text-base font-medium text-white disabled:opacity-60";

/** The care period ended by schedule, and the patient's screen is still in its limited "ended" mode: the ended
 *  message, then what the patient can do about it. Nothing else opens. One of three states, all
 *  from the api: they may ask (a short reason), they asked and are waiting, or the care team said
 *  no (final). The urgent notice in the frame is the same as on every patient screen. */
export function EndedScreen({
  phone,
  timeZone,
  extension,
  onChanged,
}: {
  phone: string | null;
  timeZone: string | null;
  extension: PatientExtension;
  onChanged: () => void;
}) {
  return (
    <PatientFrame title={T.endedTitle} phone={phone} noticeFirst>
      <p className="text-ink">{T.ended}</p>
      {extension.standing === "eligible" && (
        <RequestForm timeZone={timeZone} extension={extension} onSent={onChanged} />
      )}
      {extension.standing === "pending" && (
        <section aria-live="polite" className="mt-4 rounded-lg border border-info-border bg-info-bg p-4 text-info-text">
          <p className="font-medium">{T.pending}</p>
          <p className="mt-2 text-sm">{T.pendingNote}</p>
        </section>
      )}
      {extension.standing === "declined" && (
        <section className="mt-4 rounded-lg border border-border bg-surface p-4 text-ink">
          <p className="font-medium">{T.declined}</p>
          {extension.decision_message && (
            <>
              <p className="mt-3 text-xs font-medium text-muted">{T.messageFromTeam}</p>
              <p className="mt-0.5 whitespace-pre-wrap rounded-md bg-surface-subtle p-3 text-sm">
                {extension.decision_message}
              </p>
            </>
          )}
          <p className="mt-3 text-sm">{T.callOrGo}</p>
        </section>
      )}
    </PatientFrame>
  );
}

function RequestForm({
  timeZone,
  extension,
  onSent,
}: {
  timeZone: string | null;
  extension: PatientExtension;
  onSent: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await requestExtension(reason.trim());
      onSent();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : T.couldntSend);
      setBusy(false);
      onSent(); // the answer may have changed under us (already asked, or out of time)
    }
  }

  return (
    <section className="mt-4 rounded-lg border border-border bg-surface p-4">
      <p className="text-ink">{T.ask}</p>
      {extension.request_by && <p className="mt-1 text-sm text-muted">{T.until(plainDay(extension.request_by, timeZone))}</p>}
      {!open ? (
        <button onClick={() => setOpen(true)} className={`${buttonClass} mt-3 w-full`}>
          {T.askButton}
        </button>
      ) : (
        <form onSubmit={send} className="mt-3">
          <label htmlFor="extension-reason" className="text-sm font-medium text-ink">
            {T.reasonLabel}
          </label>
          <p className="mb-1 text-xs text-muted">{T.reasonHint(extension.reason_max)}</p>
          <textarea
            id="extension-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={4}
            maxLength={extension.reason_max}
            required
            disabled={busy}
            className="block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
          />
          {error && <p className="mt-1 text-sm text-flag-text">{error}</p>}
          <button type="submit" disabled={busy || !reason.trim()} className={`${buttonClass} mt-2 w-full`}>
            {busy ? T.sending : T.send}
          </button>
        </form>
      )}
    </section>
  );
}
