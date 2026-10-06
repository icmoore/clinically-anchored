"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { AppHeader } from "@/components/app-header";
import { ComposeBox, EarlierDrafts } from "@/components/compose-box";
import { ContactLogSection } from "@/components/contact-log";
import { StatusBadge } from "@/components/status-badge";
import { SummarySection } from "@/components/summary-view";
import { ApiError } from "@/lib/api";
import {
  CheckInDetail,
  Draft,
  IssuedLink,
  Message,
  Patient,
  Touchpoint,
  issueLink,
  listCheckIns,
  listDrafts,
  listPatients,
  listThread,
  listTouchpoints,
  markRead,
  reviewCheckIn,
} from "@/lib/clinician-api";
import { clock, timeAgo } from "@/lib/format";
import { buildSummary } from "@/lib/symptoms";
import { useClinician } from "@/lib/use-clinician";
import { usePolling } from "@/lib/use-polling";

const REFRESH_MS = 10000;

export default function PatientPage() {
  const clinician = useClinician();
  const params = useParams<{ id: string }>();
  if (clinician.status === "loading") return <p className="p-6 text-muted">Loading...</p>;
  if (clinician.status === "error") return <p className="p-6 text-flag-text">{clinician.message}</p>;
  return (
    <>
      <AppHeader clinicName={clinician.clinic.name} email={clinician.email} />
      <PatientView
        clinicId={clinician.clinic.id}
        role={clinician.clinic.role}
        patientId={params.id}
      />
    </>
  );
}

function PatientView({
  clinicId,
  role,
  patientId,
}: {
  clinicId: string;
  role: string;
  patientId: string;
}) {
  const marked = useRef<Set<string>>(new Set());
  const [highlighted, setHighlighted] = useState<string | null>(null);

  // Scroll a cited message into view in the thread and flash it, so a summary
  // citation lands on the exact message.
  const jumpToMessage = useCallback((messageId: string) => {
    const el = document.getElementById(`message-${messageId}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlighted(messageId);
    window.setTimeout(() => setHighlighted((cur) => (cur === messageId ? null : cur)), 2500);
  }, []);

  const fetchAll = useCallback(async () => {
    const [patients, checkIns, thread, drafts, touchpoints] = await Promise.all([
      listPatients(clinicId),
      listCheckIns(clinicId, patientId),
      listThread(clinicId, patientId),
      // Drafts are an add-on: if they fail to load, the rest of the page still works.
      listDrafts(clinicId, patientId).catch(() => null),
      // Likewise the contact log.
      listTouchpoints(clinicId, patientId).catch(() => null),
    ]);
    // Viewing the thread counts as reading the patient's new messages.
    for (const m of thread) {
      if (m.sender === "patient" && !m.read_at && !marked.current.has(m.id)) {
        marked.current.add(m.id);
        markRead(clinicId, m.id).catch(() => marked.current.delete(m.id));
      }
    }
    return {
      patient: patients.find((p) => p.id === patientId) ?? null,
      checkIns,
      thread,
      drafts,
      touchpoints,
    };
  }, [clinicId, patientId]);
  const { data, failed, reload } = usePolling(fetchAll, REFRESH_MS);

  const patient: Patient | null | undefined = data ? data.patient : undefined;
  const checkIns: CheckInDetail[] | null = data?.checkIns ?? null;
  const thread: Message[] | null = data?.thread ?? null;
  const drafts: Draft[] | null = data?.drafts ?? null;
  const touchpoints: Touchpoint[] | null = data?.touchpoints ?? null;
  const error = failed ? "Couldn't refresh. Retrying..." : null;
  const load = reload;

  if (patient === undefined) return <p className="p-6 text-muted">Loading...</p>;
  if (patient === null) {
    return (
      <p className="p-6 text-muted">
        Patient not found. <Link href="/dashboard" className="underline">Back to dashboard</Link>
      </p>
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-6">
      <div>
        <Link href="/dashboard" className="text-sm text-muted hover:underline">
          &larr; Dashboard
        </Link>
        <h1 className="mt-1 text-xl font-semibold text-ink">{patient.full_name}</h1>
        {patient.contact && <p className="text-sm text-muted">{patient.contact}</p>}
        {error && <p className="mt-1 text-sm text-flag-text">{error}</p>}
      </div>

      <LinksCard clinicId={clinicId} patientId={patientId} />

      <SummarySection
        clinicId={clinicId}
        patientId={patientId}
        thread={thread}
        onJump={jumpToMessage}
      />

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
          Check-ins
        </h2>
        {checkIns === null ? (
          <p className="text-muted">Loading...</p>
        ) : checkIns.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border-strong p-4 text-muted">
            No check-ins yet. Send this patient their check-in link above.
          </p>
        ) : (
          <ul className="space-y-2">
            {checkIns.map((c) => (
              <CheckInCard key={c.id} checkIn={c} clinicId={clinicId} onChanged={load} />
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
          Messages
        </h2>
        <Thread
          clinicId={clinicId}
          patientId={patientId}
          role={role}
          thread={thread}
          drafts={drafts}
          highlighted={highlighted}
          onChanged={load}
        />
        <EarlierDrafts drafts={drafts} />
      </section>

      <ContactLogSection
        clinicId={clinicId}
        patientId={patientId}
        touchpoints={touchpoints}
        thread={thread}
        onChanged={load}
      />
    </main>
  );
}

function LinksCard({ clinicId, patientId }: { clinicId: string; patientId: string }) {
  const [link, setLink] = useState<IssuedLink | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function issue(scope: "checkin" | "messages") {
    setBusy(scope);
    setError(null);
    setCopied(false);
    try {
      const issued = await issueLink(clinicId, patientId, scope);
      setLink(issued);
      try {
        await navigator.clipboard.writeText(issued.url);
        setCopied(true);
      } catch {
        // Clipboard can be blocked (e.g. non-HTTPS); the URL is shown below to copy by hand.
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create that link.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">Send this patient a link</h2>
      <p className="mt-1 text-sm text-muted">
        Creates a private link and copies it. Text or email it to the patient yourself; anyone with
        the link can use it until it expires.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          onClick={() => issue("checkin")}
          disabled={busy !== null}
          className="rounded-md border border-primary text-primary enabled:hover:bg-primary-tint px-3 py-2 text-sm font-medium disabled:opacity-60"
        >
          Copy check-in link
        </button>
        <button
          onClick={() => issue("messages")}
          disabled={busy !== null}
          className="rounded-md border border-primary text-primary enabled:hover:bg-primary-tint px-3 py-2 text-sm font-medium disabled:opacity-60"
        >
          Copy messages link
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-flag-text">{error}</p>}
      {link && (
        <div className="mt-3 space-y-1">
          <p className="text-xs text-muted">
            {copied ? "Copied. " : "Copy this link: "}
            {link.scope === "checkin" ? "Check-in" : "Messages"} link, valid until{" "}
            {clock(link.expires_at)}.
          </p>
          <input
            readOnly
            value={link.url}
            onFocus={(e) => e.currentTarget.select()}
            className="w-full rounded-md border border-border-strong bg-surface-subtle px-2 py-1.5 font-mono text-xs"
          />
        </div>
      )}
    </section>
  );
}

function CheckInCard({
  checkIn,
  clinicId,
  onChanged,
}: {
  checkIn: CheckInDetail;
  clinicId: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  // Patient/procedure/day are already shown by this card -- the summary
  // here is just the symptom detail, built from the same definitions the
  // check-in form itself used (apps/web's lib/symptoms.ts), so it can't
  // drift out of sync with what the patient was actually asked.
  const summary = buildSummary({ answers: checkIn.answers });
  const reviewed = checkIn.reviewed_at !== null;

  async function review() {
    setBusy(true);
    try {
      await reviewCheckIn(clinicId, checkIn.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      className={`rounded-lg border p-4 ${
        checkIn.is_red_flag && !reviewed ? "border-flag-border bg-flag-bg" : "border-border bg-surface"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">
          {clock(checkIn.created_at)}
          <span className="text-muted"> &middot; {timeAgo(checkIn.created_at)}</span>
        </p>
        <div className="flex gap-2 text-xs">
          {checkIn.is_red_flag && (
            <StatusBadge variant="flag">Red flag</StatusBadge>
          )}
          {reviewed && (
            <StatusBadge variant="clear">Reviewed</StatusBadge>
          )}
        </div>
      </div>
      <p className="mt-1 text-sm text-muted">
        {[checkIn.procedure_name, checkIn.post_op_day !== null && `post-op day ${checkIn.post_op_day}`]
          .filter(Boolean)
          .join(" · ") || "No procedure or day given"}
      </p>
      <p className="mt-2 whitespace-pre-wrap text-sm text-ink">{summary}</p>
      {!reviewed && (
        <button
          onClick={review}
          disabled={busy}
          className="mt-3 rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
        >
          {busy ? "Saving..." : "Mark reviewed"}
        </button>
      )}
    </li>
  );
}

function Thread({
  clinicId,
  patientId,
  role,
  thread,
  drafts,
  highlighted,
  onChanged,
}: {
  clinicId: string;
  patientId: string;
  role: string;
  thread: Message[] | null;
  drafts: Draft[] | null;
  highlighted: string | null;
  onChanged: () => void;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="max-h-96 space-y-2 overflow-y-auto p-4">
        {thread === null ? (
          <p className="text-muted">Loading...</p>
        ) : thread.length === 0 ? (
          <p className="text-muted">No messages yet.</p>
        ) : (
          thread.map((m) => (
            <div
              key={m.id}
              id={`message-${m.id}`}
              className={`rounded-lg p-1 transition-shadow ${
                m.sender === "clinician" ? "text-right" : "text-left"
              } ${highlighted === m.id ? "bg-primary-tint ring-2 ring-primary" : ""}`}
            >
              <div
                className={`inline-block max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-left text-sm ${
                  m.sender === "clinician" ? "bg-ink text-white" : "bg-surface-subtle text-ink"
                }`}
              >
                {m.body}
              </div>
              <p className="mt-0.5 text-xs text-muted">
                {m.sender === "clinician" ? "You" : "Patient"} &middot; {clock(m.created_at)}
              </p>
            </div>
          ))
        )}
      </div>
      <div className="border-t border-border p-3">
        <ComposeBox
          clinicId={clinicId}
          patientId={patientId}
          role={role}
          drafts={drafts}
          thread={thread}
          onChanged={onChanged}
        />
      </div>
    </div>
  );
}
