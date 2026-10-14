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

/** "Day 4", or "Surgery in 3 days" before the surgery date. */
export function dayLabel(postOpDay: number): string {
  if (postOpDay >= 0) return `Day ${postOpDay}`;
  return `Surgery in ${-postOpDay} day${postOpDay === -1 ? "" : "s"}`;
}

export type Tone = "flag" | "watch" | "clear" | "info" | "quiet";

/** One line for the patient list and the episode card, with a tone for its badge. */
export function episodeSummary(e: Episode): { label: string; detail: string; tone: Tone } {
  const s = e.state;
  const at = (iso: string | null) => (iso ? inZone(iso, e.timezone) : "");
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
        label: `Window ${s.interval_no} of ${s.intervals_total} open`,
        detail: `No message yet in this window; it closes ${at(s.interval_closes_at)}.`,
        tone: "clear",
      };
    case "used":
      return {
        label: `Window ${s.interval_no} of ${s.intervals_total} used`,
        detail: s.next_opens_at
          ? `The patient has written in this window. The next opens ${at(s.next_opens_at)}.`
          : `That was the last window. The care period ends ${at(s.episode_closes_at)}.`,
        tone: "info",
      };
    case "closed":
      return s.close_reason === "discharged"
        ? { label: "Discharged", detail: `Discharged ${at(s.closed_at)}. The patient's link no longer works.`, tone: "quiet" }
        : { label: "Care period ended", detail: `The schedule ended ${at(s.closed_at)}. The patient's link no longer works.`, tone: "quiet" };
  }
}
