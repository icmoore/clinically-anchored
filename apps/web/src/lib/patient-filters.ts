// The "All patients" list's view -- status, surgery date, page size, search and page -- as it
// lives in the dashboard's query string, and as the api's query. The URL is the one source of
// truth, so back/forward and a reload keep the view; defaults are left out of the address.

export type StatusFilter = "active" | "ended" | "all";
export type NamedRange = "this_week" | "last_week" | "this_month" | "last_month";
/** "any", a named range, or one calendar month as YYYY-MM. */
export type SurgeryFilter = "any" | NamedRange | `${number}-${number}`;

export const PAGE_SIZES = [5, 10, 20] as const;
export type PageSize = (typeof PAGE_SIZES)[number];

export interface PatientView {
  status: StatusFilter;
  surgery: SurgeryFilter;
  show: PageSize;
  q: string;
  page: number; // 1-based
}

export const DEFAULT_VIEW: PatientView = {
  status: "active",
  surgery: "any",
  show: 5,
  q: "",
  page: 1,
};

export const STATUS_LABEL: Record<StatusFilter, string> = {
  active: "Active",
  ended: "Ended",
  all: "All",
};

export const NAMED_RANGES: { value: NamedRange; label: string }[] = [
  { value: "this_week", label: "This week" },
  { value: "last_week", label: "Last week" },
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
];

export const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const MONTH_VALUE = /^(19|2\d)\d{2}-(0[1-9]|1[0-2])$/;
const MAX_Q = 100;

export const isMonthValue = (value: string): value is `${number}-${number}` =>
  MONTH_VALUE.test(value);

/** The view an address means. Anything unrecognised falls back to the default for that field, so a
 *  hand-edited or stale link never breaks the page. */
export function parseView(params: { get(name: string): string | null }): PatientView {
  const status = params.get("status");
  const surgery = params.get("surgery") ?? "";
  const show = Number(params.get("show"));
  const page = Number(params.get("page"));
  return {
    status: status === "ended" || status === "all" ? status : "active",
    surgery: NAMED_RANGES.some((r) => r.value === surgery)
      ? (surgery as NamedRange)
      : isMonthValue(surgery)
        ? surgery
        : "any",
    show: (PAGE_SIZES as readonly number[]).includes(show) ? (show as PageSize) : 5,
    q: (params.get("q") ?? "").slice(0, MAX_Q),
    page: Number.isInteger(page) && page >= 1 && page <= 100000 ? page : 1,
  };
}

/** The address for a view: defaults are left out; `""` when nothing differs from them. */
export function viewQuery(view: PatientView): string {
  const p = new URLSearchParams();
  if (view.status !== DEFAULT_VIEW.status) p.set("status", view.status);
  if (view.surgery !== DEFAULT_VIEW.surgery) p.set("surgery", view.surgery);
  if (view.show !== DEFAULT_VIEW.show) p.set("show", String(view.show));
  if (view.q.trim()) p.set("q", view.q.trim());
  if (view.page > 1) p.set("page", String(view.page));
  return p.toString();
}

/** The api's query for a view (see `GET /clinics/{id}/patients`). */
export function apiQuery(view: PatientView): string {
  const p = new URLSearchParams({
    status: view.status,
    limit: String(view.show),
    offset: String((view.page - 1) * view.show),
  });
  if (view.surgery !== "any") {
    p.set(isMonthValue(view.surgery) ? "surgery_month" : "surgery_range", view.surgery);
  }
  if (view.q.trim()) p.set("q", view.q.trim());
  return p.toString();
}

/** A change that narrows or widens the list (not a page turn) goes back to page 1. */
export function withFilter(view: PatientView, change: Partial<PatientView>): PatientView {
  return { ...view, ...change, page: 1 };
}

/** Status, surgery date and name: what "Clear filters" resets. The page size is a display choice,
 *  not a filter, so it stays. */
export function clearFilters(view: PatientView): PatientView {
  return { ...DEFAULT_VIEW, show: view.show };
}

export function monthLabel(value: string): string {
  const [year, month] = value.split("-");
  return `${MONTH_NAMES[Number(month) - 1]} ${year}`;
}

export function surgeryLabel(surgery: SurgeryFilter): string {
  if (surgery === "any") return "Any";
  if (isMonthValue(surgery)) return monthLabel(surgery);
  return NAMED_RANGES.find((r) => r.value === surgery)?.label ?? "Any";
}

/** "Oct 26" for a YYYY-MM-DD date. Read as a calendar date, never shifted by the device's zone. */
export function calendarDay(date: string): string {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString("en-CA", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
  });
}

export interface Chip {
  key: "status" | "surgery" | "q";
  label: string;
}

/** One chip per filter that is not at its default. */
export function chips(view: PatientView, span?: { from: string | null; to: string | null }): Chip[] {
  const out: Chip[] = [];
  if (view.status !== DEFAULT_VIEW.status) {
    out.push({ key: "status", label: `Status: ${STATUS_LABEL[view.status]}` });
  }
  if (view.surgery !== "any") {
    const dates = span?.from && span.to ? ` (${calendarDay(span.from)} – ${calendarDay(span.to)})` : "";
    out.push({
      key: "surgery",
      label: `Surgery: ${surgeryLabel(view.surgery)}${isMonthValue(view.surgery) ? "" : dates}`,
    });
  }
  if (view.q.trim()) out.push({ key: "q", label: `Name: “${view.q.trim()}”` });
  return out;
}

/** "1–5 of 48" for the page at `offset`; "3 of 48" for a last page of one; "0 of 0" when empty. */
export function rangeLabel(offset: number, shown: number, total: number): string {
  if (total === 0 || shown === 0) return `0 of ${total}`;
  if (shown === 1) return `${offset + 1} of ${total}`;
  return `${offset + 1}–${offset + shown} of ${total}`;
}

/** Today's year and month (YYYY-MM) on the clinic's calendar, which is Toronto's. */
export function currentMonth(now: Date = new Date()): `${number}-${number}` {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/Toronto",
    year: "numeric",
    month: "2-digit",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}` as `${number}-${number}`;
}
