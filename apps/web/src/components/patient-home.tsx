"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  MyCheckIn,
  PatientExtension,
  PatientMessage,
  PatientState,
  getMyCheckIn,
  getPatientState,
  getPatientThread,
  sendPatientMessage,
} from "@/lib/api";
import { categoryLabel } from "@/lib/categories";
import { inZone, plainTime } from "@/lib/format";
import { PATIENT_TEXT } from "@/lib/patient-strings";
import { buildSummary } from "@/lib/symptoms";
import { usePolling } from "@/lib/use-polling";
import { PatientExplainer } from "./patient-explainer";
import { SignedInPatient } from "./patient-gate";
import { EndedScreen } from "./ended-screen";
import { ClosedScreen, PatientFrame, ReplacedLinkScreen } from "./patient-frame";
import { PatientTimeline } from "./patient-timeline";

const REFRESH_MS = 10000;

/** The patient's home, inside the gate (components/patient-gate.tsx): the first sign-in steps come
 *  first. A `?token=` in the address is one of the retired per-episode links: it says so and points
 *  to the sign-in page instead of opening anything. */
export function PatientHome() {
  const retiredLink = useSearchParams().get("token");
  if (retiredLink) return <ReplacedLinkScreen />;
  return <SignedInPatient>{({ recheck }) => <PatientMessages onRefused={recheck} />}</SignedInPatient>;
}

/** Their thread with the care team, when they can next write, and the forms to do it. Everything
 *  about WHAT they may do comes from the api's state (it is computed from the schedule and the
 *  clock there); this only shows it. `onRefused` is told when the api turns the patient away. */
function PatientMessages({ onRefused }: { onRefused: () => void }) {
  const fetchAll = useCallback(async () => {
    const state = await getPatientState();
    // A closed care period opens nothing but its own "closed" answer.
    if (state.phase === "closed") return { state, thread: [], checkIn: null };
    const [thread, checkIn] = await Promise.all([
      getPatientThread(),
      // Their own check-in, shown back to them. Not essential: if it fails the rest still works.
      state.check_in_done ? getMyCheckIn().catch(() => null) : Promise.resolve(null),
    ]);
    return { state, thread, checkIn };
  }, []);
  const { data, failed, error, reload } = usePolling(fetchAll, REFRESH_MS);

  // Signed in, but the api no longer lets this patient in (signed out elsewhere, access turned off,
  // locked): the gate looks at the account again and decides what to show.
  const refused = error instanceof ApiError && (error.status === 401 || error.status === 403);
  useEffect(() => {
    if (refused) onRefused();
  }, [refused, onRefused]);

  if (data === null) {
    if (refused) return <p className="p-6 text-muted">Loading...</p>;
    if (error instanceof ApiError && error.status === 410) return <ClosedScreen />;
    return (
      <PatientFrame title="Messages with your care team">
        <p className={failed ? "text-flag-text" : "text-muted"}>
          {failed ? "Couldn't load your messages. Retrying..." : "Loading..."}
        </p>
      </PatientFrame>
    );
  }

  const { state, thread, checkIn } = data;
  if (state.phase === "closed") {
    // Ended by schedule and still inside the limited "ended" mode (api: `extension` is set).
    if (state.extension) {
      return (
        <EndedScreen
          phone={state.office_phone}
          timeZone={state.timezone}
          extension={state.extension}
          onChanged={reload}
        />
      );
    }
    return <ClosedScreen phone={state.office_phone} />;
  }

  return (
    <PatientFrame title="Messages with your care team" phone={state.office_phone} noticeFirst>
      <PatientExplainer />
      {failed && <p className="mb-3 text-sm text-flag-text">Couldn&apos;t refresh. Retrying...</p>}
      {state.extension?.standing === "accepted" && <AcceptedCard extension={state.extension} state={state} />}
      {checkIn && <CheckInCard checkIn={checkIn} timeZone={state.timezone} />}
      <Thread thread={thread} timeZone={state.timezone} />
      {/* The notice sits just above the box it is about: the message window's form when there is
          one (a reply box, if any, comes before it), otherwise above the reply box, otherwise at
          the end of the thread. */}
      {state.can_send_scheduled ? (
        <>
          {state.can_reply && <ReplyForm onSent={reload} />}
          <StatusPanel state={state} />
          <ScheduledForm state={state} onSent={reload} />
        </>
      ) : (
        <>
          <StatusPanel state={state} />
          {state.can_reply && <ReplyForm onSent={reload} />}
        </>
      )}
    </PatientFrame>
  );
}

/** Shown while the patient is inside extra follow-up: that the care team agreed, in their words if
 *  they wrote any, and how many extra periods there are. */
function AcceptedCard({ extension, state }: { extension: PatientExtension; state: PatientState }) {
  const X = PATIENT_TEXT.extension;
  return (
    <section className="mb-4 rounded-lg border border-clear-border bg-clear-bg p-4 text-clear-text">
      <p className="font-medium">{X.accepted}</p>
      {state.intervals_total ? <p className="mt-1 text-sm">{X.acceptedMore(state.intervals_total)}</p> : null}
      {extension.decision_message && (
        <>
          <p className="mt-3 text-xs font-medium">{X.messageFromTeam}</p>
          <p className="mt-0.5 whitespace-pre-wrap rounded-md bg-surface p-3 text-sm text-ink">
            {extension.decision_message}
          </p>
        </>
      )}
    </section>
  );
}

const PANEL = {
  clear: "border-clear-border bg-clear-bg text-clear-text",
  info: "border-info-border bg-info-bg text-info-text",
  watch: "border-watch-border bg-watch-bg text-watch-text",
};

const buttonClass =
  "rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2.5 text-base font-medium text-white disabled:opacity-60";

/** The one plain sentence about what the patient can do now, the row of their message periods
 *  under it, and (when there is one) the care team's message they can reply to. All the words are
 *  in lib/patient-strings.ts; the api says which state the patient is in. */
function StatusPanel({ state }: { state: PatientState }) {
  const T = PATIENT_TEXT.status;
  const when = (iso: string | null) => (iso ? plainTime(iso, state.timezone) : "");
  const day =
    state.post_op_day === null
      ? null
      : state.post_op_day >= 0
        ? T.dayAfterSurgery(state.post_op_day)
        : T.surgeryIn(-state.post_op_day);

  let tone: keyof typeof PANEL = "info";
  let sentence = "";
  let action: React.ReactNode = null;

  if (state.phase === "locked_checkin") {
    tone = "watch";
    sentence = T.beforeCheckIn;
    action = (
      <Link href="/check-in" className={`${buttonClass} mt-3 inline-block`}>
        {T.startCheckIn}
      </Link>
    );
  } else if (state.phase === "waiting") {
    sentence = T.waiting(when(state.next_opens_at));
  } else if (state.phase === "open") {
    tone = "clear";
    sentence = T.open(when(state.interval_closes_at));
  } else if (state.phase === "used") {
    sentence = state.next_opens_at ? T.used(when(state.next_opens_at)) : T.usedLast;
  }

  return (
    <>
      <section aria-live="polite" className={`mb-3 rounded-lg border p-4 ${PANEL[tone]}`}>
        <p className="font-medium">{sentence}</p>
        {state.can_reply && <p className="mt-2 text-sm">{T.canReply}</p>}
        {action}
      </section>
      {(state.windows ?? []).length > 0 && (
        <section className="mb-4 rounded-lg border border-border bg-surface p-3">
          <PatientTimeline
            windows={state.windows ?? []}
            timeZone={state.timezone}
            nextOpensAt={state.next_opens_at}
            extra={state.extension?.standing === "accepted"}
          />
        </section>
      )}
      {day && <p className="mb-4 text-xs text-muted">{day}</p>}
    </>
  );
}

/** What the patient entered at their check-in, at the top of the conversation: people forget what
 *  they told the care team. Collapsed to a one-line reminder; opens to the full answers. */
function CheckInCard({ checkIn, timeZone }: { checkIn: MyCheckIn; timeZone: string | null }) {
  const { concerns_text: concerns, ...answers } = checkIn.answers;
  const reported = Array.isArray(answers.symptoms) ? answers.symptoms.length : 0;
  const details = buildSummary({ answers });
  return (
    <details className="mb-4 rounded-lg border border-border bg-surface">
      <summary className="cursor-pointer px-4 py-3 text-sm text-ink">
        <span className="font-medium">Your check-in</span>
        <span className="text-muted">
          {" "}
          &middot; {inZone(checkIn.submitted_at, timeZone)}
          {" "}&middot; {reported > 0 ? `${reported} symptom${reported === 1 ? "" : "s"} reported` : "no symptoms reported"}
        </span>
      </summary>
      <div className="border-t border-border px-4 py-3 text-sm text-ink">
        <p className="mb-2 text-xs text-muted">
          What you told the care team{checkIn.procedure_name ? ` after your ${checkIn.procedure_name}` : ""}.
        </p>
        {details.split("\n").filter(Boolean).map((line, i) => {
          // "Pain -- Rate your pain...: 6/10; How is it changing...: Same" -> a heading and a list.
          const [label, rest] = line.split(" -- ");
          return rest ? (
            <div key={i} className="mt-2">
              <p className="font-medium">{label}</p>
              <ul className="ml-4 list-disc space-y-0.5">
                {rest.split("; ").map((part, j) => (
                  <li key={j}>{part}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p key={i}>{line}</p>
          );
        })}
        {typeof concerns === "string" && concerns && (
          <p className="mt-2 whitespace-pre-wrap">
            <span className="text-muted">In your own words: </span>
            {concerns}
          </p>
        )}
      </div>
    </details>
  );
}

function Thread({ thread, timeZone }: { thread: PatientMessage[]; timeZone: string | null }) {
  if (thread.length === 0) {
    return <p className="mb-4 text-sm text-muted">No messages yet.</p>;
  }
  return (
    <div className="mb-4 space-y-2">
      {thread.map((m) => {
        const mine = m.sender === "patient";
        const kind =
          m.origin === "reply" ? "Reply" : m.category ? categoryLabel(m.category) : null;
        return (
          <div key={m.id} className={mine ? "text-right" : "text-left"}>
            <div
              className={`inline-block max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 text-left text-sm ${
                mine ? "bg-ink text-white" : "bg-surface-subtle text-ink"
              }`}
            >
              {m.body}
            </div>
            <p className="mt-0.5 text-xs text-muted">
              {mine ? "You" : "Care team"}
              {kind ? ` · ${kind}` : ""} · {inZone(m.created_at, timeZone)}
            </p>
          </div>
        );
      })}
    </div>
  );
}

function useSender(onSent: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function send(body: { body: string; kind: "scheduled" | "reply"; category?: string }) {
    setBusy(true);
    setError(null);
    try {
      await sendPatientMessage(body);
      onSent();
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't send your message.");
      onSent(); // the window may have changed under us: show the new state
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, send };
}

const textarea = "block w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base";

function ReplyForm({ onSent }: { onSent: () => void }) {
  const [text, setText] = useState("");
  const { busy, error, send } = useSender(onSent);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (await send({ body: text.trim(), kind: "reply" })) setText("");
      }}
      className="mb-4 rounded-lg border border-border bg-surface p-4"
    >
      <label htmlFor="reply" className="text-sm font-medium text-ink">
        Reply to your care team
      </label>
      <p className="mb-2 text-xs text-muted">One reply. It doesn&apos;t use up your message window.</p>
      <textarea
        id="reply"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        maxLength={5000}
        className={textarea}
      />
      {error && <p className="mt-1 text-sm text-flag-text">{error}</p>}
      <button type="submit" disabled={busy || !text.trim()} className={`${buttonClass} mt-2 w-full`}>
        {busy ? "Sending..." : "Send reply"}
      </button>
    </form>
  );
}

function ScheduledForm({
  state,
  onSent,
}: {
  state: PatientState;
  onSent: () => void;
}) {
  const [category, setCategory] = useState("");
  const [text, setText] = useState("");
  const { busy, error, send } = useSender(onSent);
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (await send({ body: text.trim(), kind: "scheduled", category })) {
          setText("");
          setCategory("");
        }
      }}
      className="rounded-lg border border-border bg-surface p-4"
    >
      <fieldset>
        <legend className="mb-2 text-sm font-medium text-ink">What is your message about?</legend>
        <div className="flex flex-col gap-1.5">
          {state.categories.map((key) => (
            <label
              key={key}
              className={`flex items-center gap-2 rounded-md border px-3 py-2 text-sm text-ink ${
                category === key ? "border-primary bg-primary-tint" : "border-border"
              }`}
            >
              <input
                type="radio"
                name="category"
                checked={category === key}
                onChange={() => setCategory(key)}
              />
              {categoryLabel(key)}
            </label>
          ))}
        </div>
      </fieldset>
      <label htmlFor="message" className="mt-4 block text-sm font-medium text-ink">
        Your message
      </label>
      <textarea
        id="message"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        maxLength={5000}
        className={`${textarea} mt-1`}
      />
      {error && <p className="mt-1 text-sm text-flag-text">{error}</p>}
      <button
        type="submit"
        disabled={busy || !text.trim() || !category}
        className={`${buttonClass} mt-2 w-full`}
      >
        {busy ? "Sending..." : "Send message"}
      </button>
      <p className="mt-2 text-xs text-muted">You can send one message in each window.</p>
    </form>
  );
}
