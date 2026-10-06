"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import { awaitingReplyCount } from "@/lib/contact-stats";
import {
  Draft,
  Message,
  approveDraft,
  editDraft,
  generateDraft,
  rejectDraft,
  sendMessage,
} from "@/lib/clinician-api";
import { clock, timeAgo } from "@/lib/format";

// Matches the api's DECIDER_ROLES: delegates can read and request drafts, not decide.
const DECIDER_ROLES = ["owner", "clinician"];

const NOTHING_TO_REPLY_TO = "Nothing to reply to yet";

const OUTCOME: Record<Draft["status"], string> = {
  pending: "Waiting",
  approved: "Sent as written",
  edited: "Edited, then sent",
  rejected: "Rejected, nothing sent",
};

type Closed =
  | { kind: "sent" } // approved or edited: the reply went out
  | { kind: "rejected" } // nothing was sent
  | { kind: "stale"; message: string }; // someone else decided it first

/** The one place a clinician writes to the patient. Two states, so there is only ever
 *  one way to send at a time:
 *   - compose: a textarea with "Draft a reply with AI" and "Send" side by side;
 *   - draft:   the AI draft beside the patient message it answers, to approve, edit or
 *              reject. No textarea and no Send here.
 *  What was typed lives here, so it survives a visit to the draft view: Reject restores
 *  it, while sending (typed, approved or edited) clears it. */
export function ComposeBox({
  clinicId,
  patientId,
  role,
  drafts,
  thread,
  onChanged,
}: {
  clinicId: string;
  patientId: string;
  role: string;
  drafts: Draft[] | null; // null = not loaded or unavailable
  thread: Message[] | null;
  onChanged: () => void;
}) {
  const canDecide = DECIDER_ROLES.includes(role);
  const awaiting = thread !== null && awaitingReplyCount(thread) > 0;
  const [text, setText] = useState("");
  const [open, setOpen] = useState<Draft | null>(() => {
    // Opened on a draft that is still waiting for this thread's latest patient message
    // (e.g. after a reload): offer the decision again rather than leave it unseen.
    if (!canDecide || !awaiting || !thread) return null;
    const latest = [...thread].reverse().find((m) => m.sender === "patient");
    return (drafts ?? []).find((d) => d.status === "pending" && d.source_message_id === latest?.id) ?? null;
  });
  const [busy, setBusy] = useState<"send" | "draft" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false); // the api said there's nothing to reply to

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy("send");
    setError(null);
    try {
      await sendMessage(clinicId, patientId, text.trim());
      setText("");
      setStale(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't send that message.");
    } finally {
      setBusy(null);
    }
  }

  async function handleDraft() {
    setBusy("draft");
    setError(null);
    setStale(false);
    try {
      setOpen(await generateDraft(clinicId, patientId));
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // The thread changed since this page loaded, or the model had nothing to write.
        setStale(true);
        onChanged();
      } else {
        setError(err instanceof ApiError ? err.message : "Couldn't draft a reply.");
      }
    } finally {
      setBusy(null);
    }
  }

  function handleClosed(result: Closed) {
    setOpen(null);
    if (result.kind === "sent") setText("");
    if (result.kind === "stale") setError(result.message);
    onChanged();
  }

  if (open) {
    return (
      <PendingDraft
        draft={open}
        source={thread?.find((m) => m.id === open.source_message_id) ?? null}
        clinicId={clinicId}
        canDecide={canDecide}
        onClosed={handleClosed}
        onBack={() => setOpen(null)}
      />
    );
  }

  const draftHint =
    drafts === null
      ? "AI drafts aren\u2019t available right now."
      : stale || (thread !== null && !awaiting)
        ? NOTHING_TO_REPLY_TO
        : null;
  const draftDisabled = busy !== null || drafts === null || !awaiting;

  return (
    <form onSubmit={handleSend}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        maxLength={5000}
        placeholder="Write a message to the patient..."
        className="block w-full rounded-md border border-zinc-300 px-3 py-2 text-base"
      />
      {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleDraft}
          disabled={draftDisabled}
          className="rounded-md border border-zinc-300 bg-white px-4 py-2 text-sm font-medium text-zinc-800 disabled:opacity-60"
        >
          {busy === "draft" ? "Drafting..." : "Draft a reply with AI"}
        </button>
        <button
          type="submit"
          disabled={busy !== null || !text.trim()}
          className="ml-auto rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {busy === "send" ? "Sending..." : "Send"}
        </button>
      </div>
      {draftHint && <p className="mt-1 text-xs text-zinc-500">{draftHint}</p>}
    </form>
  );
}

/** Drafts that were decided, newest first, with what the AI wrote and what went out. */
export function EarlierDrafts({ drafts }: { drafts: Draft[] | null }) {
  const decided = (drafts ?? []).filter((d) => d.status !== "pending").slice(0, 10);
  if (decided.length === 0) return null;
  return (
    <details className="mt-3 rounded-lg border border-zinc-200 bg-white">
      <summary className="cursor-pointer px-4 py-3 text-sm text-zinc-700">
        Earlier drafts ({decided.length})
      </summary>
      <ul className="space-y-3 border-t border-zinc-200 p-4">
        {decided.map((d) => (
          <DecidedDraft key={d.id} draft={d} />
        ))}
      </ul>
    </details>
  );
}

function PendingDraft({
  draft,
  source,
  clinicId,
  canDecide,
  onClosed,
  onBack,
}: {
  draft: Draft;
  source: Message | null;
  clinicId: string;
  canDecide: boolean;
  onClosed: (result: Closed) => void;
  onBack: () => void; // leave the draft pending and return to compose (for those who can't decide)
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(draft.draft_text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // An edit must actually change the draft (otherwise approve it as written).
  const edited = text.trim() !== "" && text.trim() !== draft.draft_text.trim();

  async function run(action: () => Promise<unknown>, kind: "sent" | "rejected", fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onClosed({ kind });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Someone else decided first: leave the draft view and say so.
        onClosed({ kind: "stale", message: err.message });
      } else {
        setError(err instanceof ApiError ? err.message : fallback);
        setBusy(false);
      }
    }
  }

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/40 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-full bg-blue-100 px-2 py-0.5 font-medium text-blue-800">
          AI draft
        </span>
        <span className="text-zinc-500">
          {timeAgo(draft.created_at)} &middot; {draft.model_id} &middot; {draft.prompt_version}
        </span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            Patient wrote
          </p>
          <div className="rounded-lg border border-zinc-200 bg-white p-3 text-sm text-zinc-900">
            {source ? (
              <>
                <p className="whitespace-pre-wrap">{source.body}</p>
                <p className="mt-1 text-xs text-zinc-400">{clock(source.created_at)}</p>
              </>
            ) : (
              <p className="text-zinc-500">Original message not loaded yet.</p>
            )}
          </div>
        </div>

        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            {editing ? "Your edit" : "Draft reply"}
          </p>
          {editing ? (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={6}
              maxLength={5000}
              className="block w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-base"
            />
          ) : (
            <div className="whitespace-pre-wrap rounded-lg border border-zinc-200 bg-white p-3 text-sm text-zinc-900">
              {draft.draft_text}
            </div>
          )}
        </div>
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      {canDecide ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {editing ? (
            <>
              <button
                onClick={() => run(() => editDraft(clinicId, draft.id, text.trim()), "sent", "Couldn't send that edit.")}
                disabled={busy || !edited}
                className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                {busy ? "Sending..." : "Send edited reply"}
              </button>
              <button
                onClick={() => {
                  setEditing(false);
                  setText(draft.draft_text);
                }}
                disabled={busy}
                className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 disabled:opacity-60"
              >
                Cancel edit
              </button>
              {!edited && (
                <span className="text-xs text-zinc-500">
                  Change the text to send an edit, or cancel and approve it as written.
                </span>
              )}
            </>
          ) : (
            <>
              <button
                onClick={() => run(() => approveDraft(clinicId, draft.id), "sent", "Couldn't send that reply.")}
                disabled={busy}
                className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
              >
                {busy ? "Sending..." : "Approve and send"}
              </button>
              <button
                onClick={() => setEditing(true)}
                disabled={busy}
                className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700 disabled:opacity-60"
              >
                Edit, then send
              </button>
              <button
                onClick={() => run(() => rejectDraft(clinicId, draft.id), "rejected", "Couldn't reject that draft.")}
                disabled={busy}
                className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-red-700 disabled:opacity-60"
              >
                Reject
              </button>
            </>
          )}
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <p className="text-sm text-zinc-600">
            Only a clinician can approve, edit or reject a draft.
          </p>
          <button
            onClick={onBack}
            className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-700"
          >
            Back
          </button>
        </div>
      )}
    </div>
  );
}

function DecidedDraft({ draft }: { draft: Draft }) {
  const edited = draft.status === "edited";
  return (
    <li className="text-sm">
      <p className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <span
          className={`rounded-full px-2 py-0.5 font-medium ${
            draft.status === "rejected"
              ? "bg-zinc-100 text-zinc-700"
              : "bg-green-100 text-green-800"
          }`}
        >
          {OUTCOME[draft.status]}
        </span>
        {draft.decided_at ? clock(draft.decided_at) : clock(draft.created_at)}
      </p>
      <p className="mt-1 whitespace-pre-wrap text-zinc-700">
        <span className="text-zinc-400">AI wrote: </span>
        {draft.draft_text}
      </p>
      {edited && draft.final_text && (
        <p className="mt-1 whitespace-pre-wrap text-zinc-900">
          <span className="text-zinc-400">Sent instead: </span>
          {draft.final_text}
        </p>
      )}
    </li>
  );
}
