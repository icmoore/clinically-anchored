"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { AppHeader } from "@/components/app-header";
import { ComposeBox, EarlierDrafts } from "@/components/compose-box";
import { ContactLogSection } from "@/components/contact-log";
import { PatientLinksSection } from "@/components/patient-links";
import { PhysicianSummarySection } from "@/components/physician-summary";
import { StatusBadge } from "@/components/status-badge";
import { SummarySection } from "@/components/summary-view";
import {
  CheckInDetail,
  Draft,
  Episode,
  Message,
  Patient,
  Touchpoint,
  getEpisode,
  listCheckIns,
  listDrafts,
  listPatients,
  listThread,
  listTouchpoints,
  markRead,
  reviewCheckIn,
} from "@/lib/clinician-api";
import { categoryLabel } from "@/lib/categories";
import { dayLabel } from "@/lib/episode";
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
      <AppHeader clinician={clinician} />
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
    const [patients, episode, checkIns, thread, drafts, touchpoints] = await Promise.all([
      listPatients(clinicId),
      // The latest episode, settled if its schedule has run out. No episode: the card says so.
      getEpisode(clinicId, patientId).catch(() => null),
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
      episode,
      checkIns,
      thread,
      drafts,
      touchpoints,
    };
  }, [clinicId, patientId]);
  const { data, failed, reload } = usePolling(fetchAll, REFRESH_MS);

  const patient: Patient | null | undefined = data ? data.patient : undefined;
  const episode: Episode | null = data?.episode ?? null;
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

      <PatientLinksSection clinicId={clinicId} episode={episode} />

      <PhysicianSummarySection
        clinicId={clinicId}
        role={role}
        episode={episode}
        checkIns={checkIns}
        onChanged={load}
      />

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
            No check-in yet. Send this patient their link above: it opens the check-in first.
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
          episodeClosed={episode?.state.phase === "closed"}
          replyAvailable={episode?.state.reply_available ?? false}
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
        {[checkIn.procedure_name, checkIn.post_op_day !== null && dayLabel(checkIn.post_op_day)]
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
  episodeClosed,
  replyAvailable,
  highlighted,
  onChanged,
}: {
  clinicId: string;
  patientId: string;
  role: string;
  thread: Message[] | null;
  drafts: Draft[] | null;
  episodeClosed: boolean;
  replyAvailable: boolean;
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
                {m.sender === "clinician" ? "You" : "Patient"}
                {messageKind(m) ? ` \u00b7 ${messageKind(m)}` : ""} &middot; {clock(m.created_at)}
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
          episodeClosed={episodeClosed}
          replyAvailable={replyAvailable}
          onChanged={onChanged}
        />
      </div>
    </div>
  );
}

/** What a patient message was: a reply to the clinic, or the message for a window, with the
 *  topic they picked. Clinic messages have nothing to add. */
function messageKind(m: Message): string | null {
  if (m.sender !== "patient") return null;
  if (m.origin === "reply") return "Reply";
  const topic = m.category ? categoryLabel(m.category) : null;
  const window = m.interval_no !== null ? `window ${m.interval_no}` : null;
  return [topic, window].filter(Boolean).join(", ") || null;
}
