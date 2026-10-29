"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  CareProviderSummary,
  CheckInDetail,
  Message,
  Touchpoint,
  editCareProviderSummary,
  generateCareProviderSummary,
  getLatestCareProviderSummary,
} from "@/lib/clinician-api";
import { inZone } from "@/lib/format";
import { StatusBadge } from "./status-badge";

const MAX_CHARS = 3000; // the api's limit for an edit

/** The AI care provider summary: one short paragraph, written by the model from the check-in, the
 *  messages and the calls/visits, for the physician to pass on to the patient's other care
 *  provider. Unlike the AI patient summary it has no citations (it is meant to be copied as
 *  written), so the footer asks the physician to check it against the records first. It is set
 *  apart with the same AI badge.
 *
 *  The physician can edit the text before passing it on. A saved edit is kept beside what the AI
 *  wrote (the api never overwrites the AI's text) and is what is shown and copied from then on.
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
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  const [draft, setDraft] = useState<string | null>(null); // the text being edited; null = not editing
  const [saving, setSaving] = useState(false);
  const [confirmRefresh, setConfirmRefresh] = useState(false);

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
    setConfirmRefresh(false);
    setBusy(true);
    setError(null);
    try {
      setSummary(await generateCareProviderSummary(clinicId, patientId));
      setCopied(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't write this summary.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSave() {
    if (!summary || draft === null) return;
    setSaving(true);
    setError(null);
    try {
      setSummary(await editCareProviderSummary(clinicId, patientId, summary.summary_id, draft));
      setDraft(null);
      setCopied(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save your edit.");
    } finally {
      setSaving(false);
    }
  }

  // What the physician sees and copies: their edit if there is one, else what the AI wrote.
  const shown = summary ? (summary.edited_text ?? summary.text) : "";

  // The paragraph as shown, for an email or a message. A new summary clears the "Copied" note.
  async function handleCopy() {
    if (!summary) return;
    try {
      await navigator.clipboard.writeText(shown);
      setCopied("yes");
      window.setTimeout(() => setCopied((cur) => (cur === "yes" ? null : cur)), 2500);
    } catch {
      // No clipboard access (a blocked permission, an insecure page): the text can still be selected.
      setCopied("failed");
    }
  }

  const loaded = thread !== null && checkIns !== null;
  const nothingYet = loaded && thread.length === 0 && checkIns.length === 0;
  const editing = draft !== null;
  const changed = editing && draft.trim() !== "" && draft.trim() !== shown.trim();
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
          clinic did about it, and anything still unresolved. You can edit it before you send it
          on. Read it against the records first.
        </p>
      ) : editing ? (
        <div className="mt-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={8}
            maxLength={MAX_CHARS}
            aria-label="Edit the care provider summary"
            className="block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
          />
          <p className="mt-1 text-xs text-muted">
            {draft.length} of {MAX_CHARS} characters. Saving keeps what the AI wrote in the
            record, next to your version.
          </p>
        </div>
      ) : (
        <>
          <div className="relative mt-2 rounded-lg border border-border bg-surface p-3 pr-12">
            <p className="whitespace-pre-wrap text-sm text-ink">{shown}</p>
            <button
              onClick={handleCopy}
              aria-label="Copy the summary"
              title="Copy the summary"
              className="absolute right-2 top-2 rounded-md p-1.5 text-muted hover:bg-primary-tint hover:text-link"
            >
              {copied === "yes" ? <CheckIcon /> : <CopyIcon />}
            </button>
          </div>
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
          {summary && !editing && " The summary above is the previous one."}
        </p>
      )}

      {confirmRefresh && (
        <div className="mt-3 rounded-md border border-watch-border bg-watch-bg p-3 text-sm text-watch-text">
          <p>
            Refreshing writes a new summary and replaces your edited version on screen. Your edit
            stays in the record.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              onClick={handleGenerate}
              className="rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-3 py-1.5 text-sm font-medium text-on-primary"
            >
              Write a new summary
            </button>
            <button
              onClick={() => setConfirmRefresh(false)}
              className="rounded-md border border-link bg-surface text-link enabled:hover:border-link-hover enabled:hover:text-link-hover enabled:hover:bg-primary-tint px-3 py-1.5 text-sm font-medium"
            >
              Keep my version
            </button>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {editing ? (
          <>
            <button
              onClick={handleSave}
              disabled={saving || !changed}
              className="rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-sm font-medium text-on-primary disabled:opacity-60"
            >
              {saving ? "Saving..." : "Save edit"}
            </button>
            <button
              onClick={() => {
                setDraft(null);
                setError(null);
              }}
              disabled={saving}
              className="rounded-md border border-link bg-surface text-link enabled:hover:border-link-hover enabled:hover:text-link-hover enabled:hover:bg-primary-tint px-4 py-2 text-sm font-medium disabled:opacity-60"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => (summary?.edited_text ? setConfirmRefresh(true) : handleGenerate())}
              disabled={busy || !loaded || nothingYet || confirmRefresh}
              className="rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-sm font-medium text-on-primary disabled:opacity-60"
            >
              {busy ? (summary ? "Refreshing..." : "Generating...") : summary ? "Refresh" : "Generate"}
            </button>
            {summary && (
              <button
                onClick={() => {
                  setDraft(shown);
                  setError(null);
                  setConfirmRefresh(false);
                }}
                disabled={busy}
                className="rounded-md border border-link bg-surface text-link enabled:hover:border-link-hover enabled:hover:text-link-hover enabled:hover:bg-primary-tint px-4 py-2 text-sm font-medium disabled:opacity-60"
              >
                Edit
              </button>
            )}
          </>
        )}
        <span role="status" aria-live="polite" className="text-sm text-muted">
          {copied === "yes" && "Copied."}
          {copied === "failed" && "Couldn't copy. Select the text above and copy it."}
        </span>
      </div>
      {summary && (
        <>
          <p className="mt-3 text-xs text-muted">
            {summary.edited_at
              ? `Edited ${inZone(summary.edited_at, timeZone)}`
              : `Last updated ${inZone(summary.generated_at, timeZone)}`}
            {fresh.length > 0 && ` · ${fresh.join(", ")} since`}
          </p>
          <p className="mt-1 text-[11px] text-muted">
            {summary.edited_text
              ? `Written by AI (${summary.model_id}, ${summary.prompt_version}) on ${inZone(summary.generated_at, timeZone)}, then edited by the clinic. `
              : `Written by AI (${summary.model_id}, ${summary.prompt_version}). `}
            It has no source links: check anything important against the records before passing it
            on.
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

/** Two overlapping squares: the usual "copy" mark. */
function CopyIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V6a2 2 0 0 1 2-2h9" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  );
}
