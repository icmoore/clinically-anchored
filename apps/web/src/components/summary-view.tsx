"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { Message, Summary, generateSummary, getLatestSummary } from "@/lib/clinician-api";
import { clock, timeAgo } from "@/lib/format";
import { Section } from "./section";
import { StatusBadge } from "./status-badge";

/** On-demand AI summary of the thread. Every line cites the messages it came from;
 *  the api has already verified each citation against this patient's thread, and
 *  here each one is a link that jumps to (and highlights) that message.
 *
 *  The api saves every summary it generates. Opening the patient shows the newest saved one
 *  (read back without calling the model), with how long ago it was written and a notice if
 *  messages have arrived since; "Refresh summary" asks the model again. Patient content is not
 *  kept in browser storage. */
export function SummarySection({
  clinicId,
  patientId,
  thread,
  onJump,
}: {
  clinicId: string;
  patientId: string;
  thread: Message[] | null;
  onJump: (messageId: string) => void;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Show the last saved summary when the patient is opened. If it can't be loaded the section
  // simply starts empty, as before; a summary generated meanwhile is never overwritten by it.
  useEffect(() => {
    let cancelled = false;
    getLatestSummary(clinicId, patientId)
      .then((saved) => {
        if (!cancelled && saved) setSummary((current) => current ?? saved);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [clinicId, patientId]);

  async function handleGenerate() {
    setBusy(true);
    setError(null);
    try {
      setSummary(await generateSummary(clinicId, patientId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't summarise this thread.");
    } finally {
      setBusy(false);
    }
  }

  const byId = new Map((thread ?? []).map((m) => [m.id, m]));
  const newer = summary
    ? (thread ?? []).filter((m) => new Date(m.created_at) > new Date(summary.generated_at)).length
    : 0;

  return (
    <Section title="Interaction Summary">
      {summary === null ? (
        <p className="text-sm text-muted">
          Ask the AI to summarise this conversation. Every line links to the messages it came
          from, so you can check it.
        </p>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
            <StatusBadge variant="ai">AI interaction summary</StatusBadge>
            <span className="text-muted">
              {timeAgo(summary.generated_at)} &middot; covers {summary.covers_messages}{" "}
              {summary.covers_messages === 1 ? "message" : "messages"}
            </span>
          </div>

          {newer > 0 && (
            <p className="mb-3 rounded-md border border-info-border bg-info-bg px-3 py-2 text-sm text-info-text">
              {newer} new {newer === 1 ? "message has" : "messages have"} arrived since this
              summary was written.
            </p>
          )}
          {summary.truncated && (
            <p className="mb-3 text-sm text-muted">
              Only the most recent {summary.covers_messages} messages are covered; older ones
              are left out.
            </p>
          )}

          <ul className="space-y-3">
            {summary.lines.map((line, i) => (
              <li key={i} className="text-sm text-ink">
                <span>{line.text}</span>{" "}
                <span>
                  {line.citations.map((id) => (
                    <CitationLink key={id} id={id} message={byId.get(id) ?? null} onJump={onJump} />
                  ))}
                </span>
              </li>
            ))}
          </ul>

          <p className="mt-3 text-xs text-muted">
            Written by AI ({summary.model_id}, {summary.prompt_version}). Check anything
            important against the linked messages.
          </p>
        </>
      )}

      {error && (
        <p className="mt-3 text-sm text-flag-text">
          {error}
          {summary && " The summary above is the previous one."}
        </p>
      )}
      <button
        onClick={handleGenerate}
        disabled={busy || thread === null || thread.length === 0}
        className="mt-3 rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-sm font-medium text-on-primary disabled:opacity-60"
      >
        {busy ? "Summarising..." : summary ? "Refresh summary" : "Summarise"}
      </button>
      {thread !== null && thread.length === 0 && (
        <p className="mt-2 text-xs text-muted">There are no messages to summarise yet.</p>
      )}
    </Section>
  );
}

/** One citation: a link to the source message in the thread below. */
function CitationLink({
  id,
  message,
  onJump,
}: {
  id: string;
  message: Message | null;
  onJump: (messageId: string) => void;
}) {
  if (!message) {
    // The api verified this id, so this only happens while the thread is still loading.
    return (
      <span className="mr-1 inline-block rounded border border-dashed border-border-strong px-1.5 py-0.5 text-xs text-muted">
        message not loaded
      </span>
    );
  }
  const who = message.sender === "clinician" ? "You" : "Patient";
  return (
    <a
      href={`#message-${id}`}
      onClick={(e) => {
        e.preventDefault();
        onJump(id);
      }}
      title={message.body.length > 120 ? `${message.body.slice(0, 120)}...` : message.body}
      className="mr-1 inline-block rounded border border-border-strong px-1.5 py-0.5 text-xs text-link hover:text-link-hover hover:bg-primary-tint"
    >
      {who} &middot; {clock(message.created_at)}
    </a>
  );
}
