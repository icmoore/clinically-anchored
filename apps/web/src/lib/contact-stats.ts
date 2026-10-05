// The numbers on the Contact card. Everything here is counted from rows we hold (the
// thread and the contact log), never inferred by a model, so each figure is exact.
// Pure functions so they can be checked without rendering anything.
import type { Message, Touchpoint } from "./clinician-api";

export interface ContactStats {
  messagesTotal: number;
  fromPatient: number;
  fromClinic: number;
  visits: number;
  lastVisitAt: string | null;
  calls: number;
  callMinutes: number; // total of the calls that have a duration
  lastCallAt: string | null;
  emails: number;
  firstContactAt: string | null; // earliest message or logged contact
  lastContactAt: string | null; // latest message or logged contact
  awaitingReply: number; // patient messages with no clinician message after them
  medianReplyMs: number | null; // patient message -> clinician's next message
  enteredMinutes: number; // minutes typed in by hand, across all manual kinds
  enteredEntries: number; // how many manual entries carried a duration
}

const ms = (iso: string) => new Date(iso).getTime();

function latest(rows: Touchpoint[]): string | null {
  return rows.reduce<string | null>(
    (best, t) => (best === null || ms(t.occurred_at) > ms(best) ? t.occurred_at : best),
    null,
  );
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export function contactStats(thread: Message[], touchpoints: Touchpoint[]): ContactStats {
  const messages = thread
    .filter((m) => m.sender !== "system")
    .sort((a, b) => ms(a.created_at) - ms(b.created_at));
  // `message` touchpoints are the api's own record of a clinician message being sent;
  // the thread is the source for messages, so counting them again would double up.
  const logged = touchpoints.filter((t) => t.kind !== "message");
  const visits = logged.filter((t) => t.kind === "visit");
  const calls = logged.filter((t) => t.kind === "call");
  const withDuration = logged.filter((t) => t.duration_minutes !== null);
  const sum = (rows: Touchpoint[]) => rows.reduce((n, t) => n + (t.duration_minutes ?? 0), 0);

  // Walk the thread once: a run of patient messages is answered by the clinician's next
  // message; the reply time is measured from the first message in the run.
  let runStart: number | null = null;
  let awaiting = 0;
  const replies: number[] = [];
  for (const m of messages) {
    if (m.sender === "patient") {
      if (runStart === null) runStart = ms(m.created_at);
      awaiting += 1;
    } else if (runStart !== null) {
      replies.push(ms(m.created_at) - runStart);
      runStart = null;
      awaiting = 0;
    }
  }

  const times = [...messages.map((m) => m.created_at), ...logged.map((t) => t.occurred_at)].sort(
    (a, b) => ms(a) - ms(b),
  );

  return {
    messagesTotal: messages.length,
    fromPatient: messages.filter((m) => m.sender === "patient").length,
    fromClinic: messages.filter((m) => m.sender === "clinician").length,
    visits: visits.length,
    lastVisitAt: latest(visits),
    calls: calls.length,
    callMinutes: sum(calls),
    lastCallAt: latest(calls),
    emails: logged.filter((t) => t.kind === "email").length,
    firstContactAt: times[0] ?? null,
    lastContactAt: times[times.length - 1] ?? null,
    awaitingReply: awaiting,
    medianReplyMs: median(replies),
    enteredMinutes: sum(withDuration),
    enteredEntries: withDuration.length,
  };
}

/** "2 d", "5 h", "12 min" -- how long ago, without the word "ago" (the card labels it). */
export function span(fromIso: string, now: number = Date.now()): string {
  const minutes = Math.max(0, Math.floor((now - ms(fromIso)) / 60000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h`;
  return `${Math.floor(hours / 24)} d`;
}

/** A duration as "4 h 10 m", "35 m" or "2 d 3 h". */
export function duration(millis: number): string {
  const minutes = Math.max(0, Math.round(millis / 60000));
  if (minutes < 60) return `${minutes} m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours} h ${minutes % 60} m` : `${hours} h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
}
