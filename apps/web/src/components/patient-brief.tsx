"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  CheckInDetail,
  Message,
  PatientBrief,
  Touchpoint,
  generateBrief,
  getLatestBrief,
} from "@/lib/clinician-api";
import { inZone } from "@/lib/format";
import { StatusBadge } from "./status-badge";

/** The AI patient summary: three labelled bullets about where this patient stands, written by the
 *  model from the check-in, the messages and the calls/visits. Nothing here is computed from the data: it is clearly set apart from
 *  the facts above it, with the same AI badge as the interaction summary.
 *
 *  Only a clinician's button press asks the model ("Generate", then "Refresh"). Opening the patient
 *  shows the newest saved one, read back without the model. Patient content is not kept in browser
 *  storage. */
export function PatientBriefSection({
  clinicId,
  patientId,
  timeZone,
  thread,
  checkIns,
  touchpoints,
}: {
  clinicId: string;
  patientId: string;
  timeZone: string;
  thread: Message[] | null;
  checkIns: CheckInDetail[] | null;
  touchpoints: Touchpoint[] | null;
}) {
  const [brief, setBrief] = useState<PatientBrief | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // What the "new since" counts are made of. When it changes the saved brief is read again (for the
  // counts only; this never calls the model).
  const activity = [
    thread?.length ?? 0,
    checkIns?.length ?? 0,
    (touchpoints ?? []).filter((t) => t.kind !== "message").length,
  ].join("/");
  useEffect(() => {
    let cancelled = false;
    getLatestBrief(clinicId, patientId)
      .then((saved) => {
        // Keep whichever is newer: a brief generated here is never replaced by an older saved one.
        if (!cancelled && saved) {
          setBrief((cur) =>
            cur && Date.parse(cur.generated_at) > Date.parse(saved.generated_at) ? cur : saved,
          );
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [clinicId, patientId, activity]);

  async function handleGenerate() {
    setBusy(true);
    setError(null);
    try {
      setBrief(await generateBrief(clinicId, patientId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't write this summary.");
    } finally {
      setBusy(false);
    }
  }

  const loaded = thread !== null && checkIns !== null;
  const nothingYet = loaded && thread.length === 0 && checkIns.length === 0;
  const fresh = brief
    ? ([
        [brief.new_since.messages, "message"],
        [brief.new_since.check_ins, "check-in"],
        [brief.new_since.touchpoints, "contact"],
      ] as const)
        .filter(([n]) => n > 0)
        .map(([n, word]) => `${n} new ${word}${n === 1 ? "" : "s"}`)
    : [];

  return (
    <div>
      <StatusBadge variant="ai">AI patient summary</StatusBadge>

      {brief === null ? (
        <p className="mt-2 text-sm text-muted">
          Ask the AI for a short summary of where this patient stands: the picture now, trends since
          the check-in, and loose ends.
        </p>
      ) : (
        <>
          <ul className="mt-2 space-y-3">
            {brief.bullets.map((b) => (
              <li key={b.label} className="text-sm text-ink">
                <p>
                  <span className="font-semibold">{b.label}:</span> {b.text}
                </p>
              </li>
            ))}
          </ul>
          {brief.truncated && (
            <p className="mt-2 text-xs text-muted">
              Only the most recent messages and contacts were shown to the AI; older ones are left
              out.
            </p>
          )}
        </>
      )}

      {error && (
        <p className="mt-2 text-sm text-flag-text">
          {error}
          {brief && " The summary above is the previous one."}
        </p>
      )}
      <button
        onClick={handleGenerate}
        disabled={busy || !loaded || nothingYet}
        className="mt-3 rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-sm font-medium text-on-primary disabled:opacity-60"
      >
        {busy ? (brief ? "Refreshing..." : "Generating...") : brief ? "Refresh" : "Generate"}
      </button>
      {brief && (
        <>
          <p className="mt-3 text-xs text-muted">
            Last updated {inZone(brief.generated_at, timeZone)}
            {fresh.length > 0 && ` · ${fresh.join(", ")} since`}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            Written by AI ({brief.model_id}, {brief.prompt_version}). Check anything important
            against the records.
          </p>
        </>
      )}
      {nothingYet && (
        <p className="mt-2 text-xs text-muted">
          There is no check-in and no messages yet, so there is nothing to summarise.
        </p>
      )}
    </div>
  );
}
