"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  CareProviderSummary,
  CheckInDetail,
  Message,
  Touchpoint,
  generateCareProviderSummary,
  getLatestCareProviderSummary,
} from "@/lib/clinician-api";
import { inZone } from "@/lib/format";
import { StatusBadge } from "./status-badge";

/** The AI care provider summary: one short paragraph, written by the model from the check-in, the
 *  messages and the calls/visits, for the physician to pass on to the patient's other care
 *  provider. Unlike the AI patient summary it has no citations (it is meant to be copied as
 *  written), so the footer asks the physician to check it against the records first. It is set
 *  apart with the same AI badge.
 *
 *  Only a clinician's button press asks the model ("Generate", then "Refresh"). Opening the patient
 *  shows the newest saved one, read back without the model. Patient content is not kept in browser
 *  storage. */
export function CareProviderSummarySection({
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
  const [summary, setSummary] = useState<CareProviderSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // What the "new since" counts are made of. When it changes the saved summary is read again (for
  // the counts only; this never calls the model).
  const activity = [
    thread?.length ?? 0,
    checkIns?.length ?? 0,
    (touchpoints ?? []).filter((t) => t.kind !== "message").length,
  ].join("/");
  useEffect(() => {
    let cancelled = false;
    getLatestCareProviderSummary(clinicId, patientId)
      .then((saved) => {
        // Keep whichever is newer: one generated here is never replaced by an older saved one.
        if (!cancelled && saved) {
          setSummary((cur) =>
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
      setSummary(await generateCareProviderSummary(clinicId, patientId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't write this summary.");
    } finally {
      setBusy(false);
    }
  }

  const loaded = thread !== null && checkIns !== null;
  const nothingYet = loaded && thread.length === 0 && checkIns.length === 0;
  const fresh = summary
    ? ([
        [summary.new_since.messages, "message"],
        [summary.new_since.check_ins, "check-in"],
        [summary.new_since.touchpoints, "contact"],
      ] as const)
        .filter(([n]) => n > 0)
        .map(([n, word]) => `${n} new ${word}${n === 1 ? "" : "s"}`)
    : [];

  return (
    <div>
      <StatusBadge variant="ai">Care provider summary</StatusBadge>

      {summary === null ? (
        <p className="mt-2 text-sm text-muted">
          Ask the AI for a short, concise summary of this patient that you can pass on to their
          care provider: the procedure, how recovery has gone so far, any red flag and what the
          clinic did about it, and anything still unresolved. Read it against the records before
          you send it on.
        </p>
      ) : (
        <>
          <p className="mt-2 whitespace-pre-wrap text-sm text-ink">{summary.text}</p>
          {summary.truncated && (
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
          {summary && " The summary above is the previous one."}
        </p>
      )}
      <button
        onClick={handleGenerate}
        disabled={busy || !loaded || nothingYet}
        className="mt-3 rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
      >
        {busy ? (summary ? "Refreshing..." : "Generating...") : summary ? "Refresh" : "Generate"}
      </button>
      {summary && (
        <>
          <p className="mt-3 text-xs text-muted">
            Last updated {inZone(summary.generated_at, timeZone)}
            {fresh.length > 0 && ` · ${fresh.join(", ")} since`}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            Written by AI ({summary.model_id}, {summary.prompt_version}). It has no source links:
            check anything important against the records before passing it on.
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
