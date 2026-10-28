// How the clinician screens describe an episode. The api computes the state (from the schedule
// and the clock); this only words it. Times are shown in the episode's own timezone.
import type { Episode } from "./clinician-api";
import { inZone } from "./format";

/** "Oct 1, 2026" from a plain date: no timezone is involved, so no shifting by a day. */
export function plainDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-CA", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** "Oct 3" in the given zone (the clinic's own calendar, not the browser's). */
export function zoneDate(iso: string, timeZone: string | null): string {
  return new Date(iso).toLocaleDateString("en-CA", {
    timeZone: timeZone ?? "America/Toronto",
    month: "short",
    day: "numeric",
  });
}

/** "Oct 3, 8:00 a.m." in the given zone, for a record's own time. */
export function zoneClock(iso: string, timeZone: string | null): string {
  return new Date(iso).toLocaleString("en-CA", {
    timeZone: timeZone ?? "America/Toronto",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** "Post-op day 4", or "Surgery in 3 days" before the surgery date. */
export function dayLabel(postOpDay: number): string {
  if (postOpDay >= 0) return `Post-op day ${postOpDay}`;
  return `Surgery in ${-postOpDay} day${postOpDay === -1 ? "" : "s"}`;
}

export type Tone = "flag" | "watch" | "clear" | "info" | "quiet";

/** One line for the patient list and the Physician Summary, with a tone for its badge. */
export function episodeSummary(e: Episode): { label: string; detail: string; tone: Tone } {
  const s = e.state;
  const at = (iso: string | null) => (iso ? inZone(iso, e.timezone) : "");
  // A follow-up extension numbers its windows from 1 again and says so in the label.
  const ext = e.extension;
  const window = ext && ext.number > 0 ? "Extension window" : "Window";
  const request = ext?.request ?? null;
  switch (s.phase) {
    case "locked_checkin":
      return {
        label: "Check-in pending",
        detail: "Messaging stays locked until the patient completes the check-in.",
        tone: "watch",
      };
    case "waiting":
      return {
        label: "Waiting for window 1",
        detail: `Checked in. The first message window opens ${at(s.next_opens_at)}.`,
        tone: "info",
      };
    case "open":
      return {
        label: `${window} ${s.interval_no} of ${s.intervals_total} \u00b7 open`,
        detail: `No message yet in this window; it closes ${at(s.interval_closes_at)}.`,
        tone: "clear",
      };
    case "used":
      return {
        label: `${window} ${s.interval_no} of ${s.intervals_total} \u00b7 message received`,
        detail: s.next_opens_at
          ? `The patient has written in this window. The next opens ${at(s.next_opens_at)}.`
          : `That was the last window. The ${ext && ext.number > 0 ? "extension" : "care period"} ends ${at(s.episode_closes_at)}.`,
        tone: "info",
      };
    case "closed": {
      if (s.close_reason === "discharged")
        return { label: "Discharged", detail: `Discharged ${at(s.closed_at)}. The patient can no longer send messages.`, tone: "quiet" };
      const what = ext && ext.number > 0 ? "extension" : "care period";
      if (request?.status === "pending")
        return {
          label: "Extension requested",
          detail: `The patient asked for more follow-up on ${at(request.requested_at)}. Their screen shows only that request until you decide.`,
          tone: "watch",
        };
      const answered =
        request?.status === "declined" ? ` Declined an extension request on ${at(request.decided_at)}.` : "";
      const offer =
        !request && ext?.can_request && ext.request_by
          ? ` The patient can ask for more follow-up until ${at(ext.request_by)}.`
          : "";
      const closedNote = request || offer ? "" : " The patient can no longer send messages.";
      return {
        label: ext && ext.number > 0 ? "Extension ended" : "Care period ended",
        detail: `The ${what === "extension" ? "extension" : "schedule"} ended ${at(s.closed_at)}.${answered}${offer}${closedNote}`,
        tone: "quiet",
      };
    }
  }
}
