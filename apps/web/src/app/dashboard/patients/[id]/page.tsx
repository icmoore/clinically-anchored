"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { AppHeader } from "@/components/app-header";
import { ClinicianProblem } from "@/components/clinician-problem";
import { ComposeBox } from "@/components/compose-box";
import { ContactLogSection } from "@/components/contact-log";
import { PatientAccountSection } from "@/components/patient-account";
import { PhysicianSummarySection } from "@/components/physician-summary";
import { SummarySection } from "@/components/summary-view";
import {
  BriefSource,
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
} from "@/lib/clinician-api";
import { categoryLabel } from "@/lib/categories";
import { isExtensionEntry } from "@/lib/contact-stats";
import { LockedTag } from "@/components/locked-tag";
import { clock, contactLine } from "@/lib/format";
import { useClinician } from "@/lib/use-clinician";
import { usePolling } from "@/lib/use-polling";

const REFRESH_MS = 10000;
// The element id each kind of cited record has on this page.
const ANCHOR: Record<BriefSource["kind"], string> = {
  message: "message",
  check_in: "checkin",
  touchpoint: "touchpoint",
};

export default function PatientPage() {
  const clinician = useClinician();
  const params = useParams<{ id: string }>();
  if (clinician.status === "loading") return <p className="p-6 text-muted">Loading...</p>;
  if (clinician.status === "error") return <ClinicianProblem problem={clinician} />;
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

  // Scroll a cited record into view and flash it: a message in the thread, a check-in card, or a
  // contact-history entry (the check-ins and the contact history are folded away, so the fold is
  // opened first). Used by the summary citations and by the AI patient summary's source chips.
  const jumpTo = useCallback((source: BriefSource) => {
    const domId = `${ANCHOR[source.kind]}-${source.id}`;
    const el = document.getElementById(domId);
    if (!el) return;
    const folded = el.closest("details");
    if (folded) folded.open = true;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlighted(domId);
    window.setTimeout(() => setHighlighted((cur) => (cur === domId ? null : cur)), 2500);
  }, []);
  const jumpToMessage = useCallback(
    (messageId: string) => jumpTo({ kind: "message", id: messageId }),
    [jumpTo],
  );

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
      // An extension request is answered by deciding it; it is never "unread".
      if (m.sender === "patient" && !isExtensionEntry(m) && !m.read_at && !marked.current.has(m.id)) {
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
        {contactLine(patient) && <p className="text-sm text-muted">{contactLine(patient)}</p>}
        {patient.account_status === "locked" && (
          <div className="mt-2">
            <LockedTag />
          </div>
        )}
        {error && <p className="mt-1 text-sm text-flag-text">{error}</p>}
      </div>

      <PhysicianSummarySection
        clinicId={clinicId}
        role={role}
        episode={episode}
        patientId={patientId}
        checkIns={checkIns}
        thread={thread}
        touchpoints={touchpoints}
        highlighted={highlighted}
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
      </section>

      <ContactLogSection
        clinicId={clinicId}
        patientId={patientId}
        touchpoints={touchpoints}
        thread={thread}
        highlighted={highlighted}
        onChanged={load}
      />

      {/* Set up once and seldom touched again, so it sits last. */}
      <PatientAccountSection clinicId={clinicId} patientId={patientId} role={role} onChanged={load} />
    </main>
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
          thread.map((m) =>
            isExtensionEntry(m) ? (
              <ExtensionEntry key={m.id} m={m} />
            ) : (
            <div
              key={m.id}
              id={`message-${m.id}`}
              className={`rounded-lg p-1 transition-shadow ${
                m.sender === "clinician" ? "text-right" : "text-left"
              } ${highlighted === `message-${m.id}` ? "bg-primary-tint ring-2 ring-primary" : ""}`}
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
            ),
          )
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
  const window =
    m.interval_no !== null ? `${(m.extension_no ?? 0) > 0 ? "extension window" : "window"} ${m.interval_no}` : null;
  return [topic, window].filter(Boolean).join(", ") || null;
}

/** The patient's request for more follow-up, or the clinic's answer to it, set apart from the
 *  messages: it is an event in the care period, not something either side wrote in a window. */
function ExtensionEntry({ m }: { m: Message }) {
  const request = m.origin === "extension_request";
  return (
    <div id={`message-${m.id}`} className="mx-auto max-w-[90%] rounded-lg border border-watch-border bg-watch-bg px-3 py-2 text-sm text-watch-text">
      <p className="text-xs font-medium">
        {request ? "Patient asked for more follow-up" : "Extension answered"} &middot; {clock(m.created_at)}
      </p>
      <p className="mt-0.5 whitespace-pre-wrap">{m.body}</p>
    </div>
  );
}
