// The numbers the public patient guide (/guide) states. A public page cannot ask the database, so
// they live in two small JSON files that apps/api/tests/db/test_guide_schedule.py checks against the
// real active schedule and the api's defaults: if one drifts, that test fails and says what to fix.
import type { PatientWindow, PatientWindowStatus } from "./api";
import facts from "./guide-facts.json";
import schedule from "./active-schedule.json";

export interface GuidePeriod {
  number: number;
  opensDay: number; // days after the surgery date
  closesDay: number; // days after the surgery date; the next period opens then
}

/** "07:00" as "7:00 a.m." (the wording the patient screens use). */
function clock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")} ${h < 12 ? "a.m." : "p.m."}`;
}

const ZONE_NAMES: Record<string, string> = { "America/Toronto": "Toronto time" };

const offsets = schedule.offsets_days;

export const GUIDE_PERIODS: GuidePeriod[] = offsets.map((opensDay, i) => ({
  number: i + 1,
  opensDay,
  closesDay: i + 1 < offsets.length ? offsets[i + 1] : opensDay + schedule.close_after_days,
}));

export interface GuideFacts {
  codeDigits: number;
  codeValidMinutes: number;
  sessionDays: number;
  wrongDatesBeforeLock: number;
  linkValidHours: number;
  periodCount: number;
  /** The day after surgery on which the last period closes and follow-up ends. */
  endsOnDay: number;
  /** "7:00 a.m." and "Toronto time". */
  opensAt: string;
  zoneName: string;
}

export const GUIDE_FACTS: GuideFacts = {
  ...facts,
  periodCount: offsets.length,
  endsOnDay: GUIDE_PERIODS[GUIDE_PERIODS.length - 1].closesDay,
  opensAt: clock(schedule.opens_at_local),
  zoneName: ZONE_NAMES[schedule.timezone] ?? schedule.timezone,
};

// ---- the example timeline: made-up data in the real component ----------------------------------

/** A made-up patient, for the picture only: surgery on 4 Jan 2027, check-in on day 6, and "today"
 *  is day 19. Winter, so every period opens at 07:00 standard time (12:00 UTC); no clock change
 *  falls inside it. The states show every mark the real timeline has. */
const EXAMPLE_STATES: PatientWindowStatus[] = [
  "before_check_in",
  "used",
  "passed",
  "open",
  "upcoming",
  "upcoming",
  "upcoming",
  "upcoming",
  "upcoming",
];

export const EXAMPLE_TIMEZONE = schedule.timezone;

const at = (dayAfterSurgery: number) =>
  new Date(Date.UTC(2027, 0, 4 + dayAfterSurgery, 12, 0)).toISOString();

export const EXAMPLE_WINDOWS: PatientWindow[] = GUIDE_PERIODS.map((p, i) => ({
  number: p.number,
  status: EXAMPLE_STATES[i] ?? "upcoming",
  opens_at: at(p.opensDay),
  closes_at: at(p.closesDay),
}));

/** When the example's next period opens (shown under the row on a phone). */
export const EXAMPLE_NEXT_OPENS_AT = EXAMPLE_WINDOWS.find((w) => w.status === "upcoming")?.opens_at ?? null;
