"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import {
  Episode,
  ExtensionRequest,
  acceptExtension,
  declineExtension,
} from "@/lib/clinician-api";
import { inZone } from "@/lib/format";
import { danger, primary, secondary } from "./buttons";
import { StatusBadge } from "./status-badge";

// Matches the api: only owners and clinicians may accept or decline.
export const DECIDE_ROLES = ["owner", "clinician"];

const MESSAGE_MAX = 5000;

/** A patient's request for more follow-up after the care period ended by schedule: their reason and
 *  the date, and (for an owner or clinician) Accept and Decline with an optional message to the
 *  patient and a confirm step. Nothing is sent until the confirm button is pressed. A delegate
 *  sees the request and who can answer it. The wording is a placeholder pending clinical review. */
export function ExtensionRequestCard({
  clinicId,
  role,
  episode,
  request,
  onChanged,
}: {
  clinicId: string;
  role: string;
  episode: Episode;
  request: ExtensionRequest;
  onChanged: () => void;
}) {
  const when = (iso: string) => inZone(iso, episode.timezone);
  if (request.status !== "pending") return null; // a decided request is shown in the status line and the thread
  return (
    <div className="mt-4 rounded-lg border border-watch-border bg-watch-bg p-4 text-watch-text">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium">Extension requested</p>
        <StatusBadge variant="watch">Needs a decision</StatusBadge>
      </div>
      <p className="mt-1 text-xs">
        The care period ended by schedule. The patient asked for more follow-up on{" "}
        {when(request.requested_at)}. If you accept, they get another 6 weeks starting now: one message
        in each of 6 windows (1 now, then every 7 days).
      </p>
      <p className="mt-3 text-xs font-medium">Their reason</p>
      <p className="mt-0.5 whitespace-pre-wrap rounded-md bg-surface p-2 text-sm text-ink">
        {request.reason ?? "(no reason shown)"}
      </p>
      {DECIDE_ROLES.includes(role) ? (
        <Decide clinicId={clinicId} request={request} onChanged={onChanged} />
      ) : (
        <p className="mt-3 text-xs">Only a clinician or the owner can accept or decline this request.</p>
      )}
    </div>
  );
}

function Decide({
  clinicId,
  request,
  onChanged,
}: {
  clinicId: string;
  request: ExtensionRequest;
  onChanged: () => void;
}) {
  const [message, setMessage] = useState("");
  const [choice, setChoice] = useState<"accept" | "decline" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    if (!choice) return;
    setBusy(true);
    setError(null);
    try {
      const text = message.trim() || null;
      if (choice === "accept") await acceptExtension(clinicId, request.id, text);
      else await declineExtension(clinicId, request.id, text);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save that decision.");
      setBusy(false);
      onChanged(); // someone else may have decided it: show what is true now
    }
  }

  return (
    <div className="mt-3">
      <label htmlFor="extension-message" className="text-xs font-medium">
        Message to the patient (optional)
      </label>
      <textarea
        id="extension-message"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        rows={2}
        maxLength={MESSAGE_MAX}
        disabled={busy}
        className="mt-1 block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base text-ink"
      />
      <p className="mt-1 text-xs">
        They see this with your answer. Neither answer lets them send a reply.
      </p>
      {choice === null ? (
        <div className="mt-2 flex flex-wrap gap-2">
          <button onClick={() => setChoice("accept")} className={primary}>
            Accept...
          </button>
          <button onClick={() => setChoice("decline")} className={danger}>
            Decline...
          </button>
        </div>
      ) : (
        <div
          role="alertdialog"
          aria-label={choice === "accept" ? "Confirm accepting" : "Confirm declining"}
          className="mt-2 rounded-md border border-border-strong bg-surface p-3 text-sm text-ink"
        >
          <p className="font-medium">
            {choice === "accept" ? "Accept this request?" : "Decline this request?"}
          </p>
          <p className="mt-1 text-xs text-muted">
            {choice === "accept"
              ? "A new 6-week follow-up starts now and the patient can use their account again. This can't be undone (you can discharge them later)."
              : "This is final for this care period: the patient can't ask again in the app. They are shown your message, if any, and told to call the office if they need help."}
          </p>
          <div className="mt-2 flex gap-2">
            <button onClick={confirm} disabled={busy} className={choice === "accept" ? primary : danger}>
              {busy ? "Saving..." : choice === "accept" ? "Yes, accept" : "Yes, decline"}
            </button>
            <button onClick={() => setChoice(null)} disabled={busy} className={secondary}>
              Back
            </button>
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-flag-text">{error}</p>}
    </div>
  );
}
