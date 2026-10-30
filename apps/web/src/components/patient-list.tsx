"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { LockedTag } from "@/components/locked-tag";
import { StatusBadge } from "@/components/status-badge";
import { Patient, PatientPage, listPatients } from "@/lib/clinician-api";
import { dayLabel, episodeSummary } from "@/lib/episode";
import { contactLine } from "@/lib/format";
import {
  MONTH_NAMES,
  NAMED_RANGES,
  PAGE_SIZES,
  PatientView,
  STATUS_LABEL,
  StatusFilter,
  SurgeryFilter,
  apiQuery,
  chips,
  clearFilters,
  currentMonth,
  isMonthValue,
  parseView,
  rangeLabel,
  viewQuery,
  withFilter,
} from "@/lib/patient-filters";

// The list reloads when the view changes, after a patient is added (`version`), and otherwise
// once a minute. The "Needs attention" queue above it has its own, faster, refresh.
const REFRESH_MS = 60000;
const SEARCH_DEBOUNCE_MS = 300;

const secondary =
  "rounded-md border border-link bg-surface text-link enabled:hover:border-link-hover enabled:hover:text-link-hover enabled:hover:bg-primary-tint px-4 py-2 text-sm font-medium disabled:opacity-60";

/** "All patients": the clinic's patients one page at a time, with filters and a name search. The
 *  view (status, surgery date, page size, search, page) lives in the address, so back/forward and
 *  a reload keep it; the database does the filtering, sorting, counting and paging. */
export function AllPatients({ clinicId, version }: { clinicId: string; version: number }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const view = parseView(searchParams);
  const key = apiQuery(view);
  const viewKey = viewQuery(view);

  // The view the next change builds on. The address only catches up after a navigation commits,
  // so two quick changes (two taps in the panel) must not both start from the old address.
  const latest = useRef(view);
  useEffect(() => {
    latest.current = parseView(new URLSearchParams(viewKey));
  }, [viewKey]);

  const go = useCallback(
    (next: PatientView, how: "push" | "replace" = "push") => {
      latest.current = next;
      const query = viewQuery(next);
      const href = query ? `/dashboard?${query}` : "/dashboard";
      if (how === "push") router.push(href, { scroll: false });
      else router.replace(href, { scroll: false });
    },
    [router],
  );

  // --- the data ------------------------------------------------------------------------------
  const [data, setData] = useState<{ key: string; page: PatientPage } | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      listPatients(clinicId, key, controller.signal)
        .then((page) => {
          if (controller.signal.aborted) return;
          setData({ key, page });
          setFailed(false);
        })
        .catch(() => {
          if (!controller.signal.aborted) setFailed(true);
        });
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [clinicId, key, version, retry]);

  const page = data?.page ?? null;
  const loading = data?.key !== key; // the view changed and its page has not arrived
  const total = page?.total ?? 0;

  // A page past the end (patients left the list since it was opened): step back to the last one.
  const lastPage = page ? Math.max(1, Math.ceil(page.total / view.show)) : 1;
  const stranded = !loading && page !== null && page.items.length === 0 && view.page > lastPage;
  useEffect(() => {
    if (stranded) go({ ...view, page: lastPage }, "replace");
  }, [stranded, view, lastPage, go]);

  // --- the search box ------------------------------------------------------------------------
  // What is typed is local; the address (and so the list) follows 300 ms after the last key. A
  // change to the address that did not come from here (back/forward) replaces what is typed.
  const [typed, setTyped] = useState(view.q);
  const [pushedQ, setPushedQ] = useState(view.q);
  const [seenQ, setSeenQ] = useState(view.q);
  if (view.q !== seenQ) {
    setSeenQ(view.q);
    if (view.q !== pushedQ) {
      setPushedQ(view.q);
      setTyped(view.q);
    }
  }
  useEffect(() => {
    if (typed.trim() === view.q.trim()) return;
    const timer = setTimeout(() => {
      setPushedQ(typed.trim());
      go(withFilter(latest.current, { q: typed.trim() }), "replace");
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [typed, view.q, go]);

  const change = (next: Partial<PatientView>) => go(withFilter(latest.current, next));
  const reset = (next: PatientView) => {
    setTyped(next.q);
    setPushedQ(next.q);
    go(next);
  };
  const removeChip = (field: "status" | "surgery" | "q") => {
    const defaults = clearFilters(latest.current);
    reset(withFilter(latest.current, { [field]: defaults[field] } as Partial<PatientView>));
  };
  const active = chips(view, page && !loading ? { from: page.surgery_from, to: page.surgery_to } : undefined);

  // --- the filter panel ----------------------------------------------------------------------
  const [open, setOpen] = useState(false);
  const holder = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => {
      if (holder.current && !holder.current.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      toggle.current?.focus();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <section>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">All patients</h2>

      <div className="flex flex-wrap items-center gap-2">
        <div ref={holder} className="relative">
          <button
            ref={toggle}
            type="button"
            aria-expanded={open}
            aria-controls="patient-filters"
            onClick={() => setOpen((o) => !o)}
            className={`${secondary} min-h-11`}
          >
            Filters{active.filter((c) => c.key !== "q").length > 0
              ? ` (${active.filter((c) => c.key !== "q").length})`
              : ""}
          </button>
          {open && (
            <FilterPanel
              view={view}
              onChange={change}
              onClose={() => {
                setOpen(false);
                toggle.current?.focus();
              }}
            />
          )}
        </div>
        <input
          type="search"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          maxLength={100}
          placeholder="Search by name"
          aria-label="Search patients by name"
          className="min-h-11 min-w-0 basis-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base sm:basis-0 sm:flex-1"
        />
      </div>

      {active.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {active.map((chip) => (
            <span
              key={chip.key}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-subtle py-0.5 pl-3 pr-1 text-xs text-ink"
            >
              {chip.label}
              <button
                type="button"
                onClick={() => removeChip(chip.key)}
                aria-label={`Remove filter ${chip.label}`}
                className="relative flex h-6 w-6 items-center justify-center rounded-full text-link after:absolute after:-inset-2.5 hover:bg-primary-tint hover:text-link-hover"
              >
                <span aria-hidden="true">×</span>
              </button>
            </span>
          ))}
          <button
            type="button"
            onClick={() => reset(clearFilters(latest.current))}
            className="px-2 py-3 text-xs font-medium text-link underline hover:text-link-hover"
          >
            Clear filters
          </button>
        </div>
      )}

      <div aria-busy={loading} className={loading && page ? "opacity-60" : undefined}>
        {page === null ? (
          failed ? (
            <p className="mt-3 text-sm text-flag-text">
              Couldn&apos;t load patients.{" "}
              <button
                type="button"
                onClick={() => setRetry((n) => n + 1)}
                className="font-medium text-link underline hover:text-link-hover"
              >
                Try again
              </button>
            </p>
          ) : (
            <p className="mt-3 text-muted">Loading...</p>
          )
        ) : page.items.length === 0 && !stranded ? (
          <div className="mt-3 rounded-lg border border-dashed border-border-strong p-4">
            <p className="text-ink">No patients match.</p>
            {active.length > 0 ? (
              <button type="button" onClick={() => reset(clearFilters(latest.current))} className={`${secondary} mt-3`}>
                Clear filters
              </button>
            ) : (
              <p className="mt-1 text-sm text-muted">
                Nobody is in follow-up right now. Add a patient under Patient management, or open
                Filters and choose Status: All.
              </p>
            )}
          </div>
        ) : (
          <>
            <ul className="mt-3 divide-y divide-border rounded-lg border border-border bg-surface">
              {page.items.map((p) => (
                <PatientRow key={p.id} patient={p} />
              ))}
            </ul>
            <div className="mt-3 flex items-center justify-between gap-3">
              <p aria-live="polite" className="text-sm text-muted">
                {rangeLabel(page.offset, page.items.length, page.total)}
              </p>
              {total > view.show && (
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={view.page <= 1}
                    onClick={() => go({ ...latest.current, page: latest.current.page - 1 })}
                    className={`${secondary} min-h-11`}
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    disabled={view.page >= lastPage}
                    onClick={() => go({ ...latest.current, page: latest.current.page + 1 })}
                    className={`${secondary} min-h-11`}
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
      {failed && page !== null && (
        <p className="mt-2 text-sm text-flag-text">Couldn&apos;t refresh this list. Retrying...</p>
      )}
    </section>
  );
}

function PatientRow({ patient: p }: { patient: Patient }) {
  return (
    <li className="relative flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-subtle">
      {/* The link covers the whole row (the ::after); the locked tag sits above it, so a tap on
          the tag shows its reason instead of opening the patient. */}
      <div className="min-w-0">
        <Link href={`/dashboard/patients/${p.id}`} className="block after:absolute after:inset-0">
          <span
            className={`block font-medium ${
              p.episode?.state.phase === "closed" ? "text-muted" : "text-ink"
            }`}
          >
            {p.full_name}
          </span>
          {contactLine(p) && (
            <span className="block truncate text-sm text-muted">{contactLine(p)}</span>
          )}
        </Link>
        {p.account_status === "locked" && (
          <div className="mt-1">
            <LockedTag />
          </div>
        )}
      </div>
      <EpisodeTag patient={p} />
    </li>
  );
}

/** The patient's care-period status on the list: the phase, and how many days since surgery. */
function EpisodeTag({ patient }: { patient: Patient }) {
  if (!patient.episode) return null;
  const { label, tone } = episodeSummary(patient.episode);
  const closed = patient.episode.state.phase === "closed";
  const variant = tone === "quiet" ? null : tone;
  return (
    <span className="flex shrink-0 flex-col items-end gap-1 text-xs text-muted">
      {variant ? <StatusBadge variant={variant}>{label}</StatusBadge> : <span>{label}</span>}
      {!closed && <span>{dayLabel(patient.episode.state.post_op_day)}</span>}
    </span>
  );
}

// --- the filter panel -----------------------------------------------------------------------------

function FilterPanel({
  view,
  onChange,
  onClose,
}: {
  view: PatientView;
  onChange: (change: Partial<PatientView>) => void;
  onClose: () => void;
}) {
  const picked = isMonthValue(view.surgery);
  const [year, month] = picked ? view.surgery.split("-") : currentMonth().split("-");
  const thisYear = Number(currentMonth().slice(0, 4));
  const years = Array.from(
    new Set([thisYear - 3, thisYear - 2, thisYear - 1, thisYear, thisYear + 1, Number(year)]),
  ).sort((a, b) => a - b);
  const setMonth = (y: string, m: string) =>
    onChange({ surgery: `${y}-${m}` as SurgeryFilter });
  const select =
    "min-h-11 min-w-0 flex-1 rounded-md border border-border-strong bg-surface px-3 py-2 text-base text-ink";

  return (
    <div
      id="patient-filters"
      role="group"
      aria-label="Filters"
      className="absolute left-0 top-full z-30 mt-2 w-[min(22rem,calc(100vw-2rem))] space-y-4 rounded-lg border border-border bg-surface p-4 shadow-lg"
    >
      <Group legend="Status" hint="Ended includes discharged and finished care periods.">
        {(Object.keys(STATUS_LABEL) as StatusFilter[]).map((value) => (
          <Choice
            key={value}
            name="patient-status"
            label={STATUS_LABEL[value]}
            checked={view.status === value}
            onChange={() => onChange({ status: value })}
          />
        ))}
      </Group>

      <Group legend="Surgery date" cols="grid-cols-2" hint="Weeks run Monday to Sunday, Toronto time.">
        <Choice
          name="patient-surgery"
          label="Any"
          checked={view.surgery === "any"}
          onChange={() => onChange({ surgery: "any" })}
        />
        {NAMED_RANGES.map((r) => (
          <Choice
            key={r.value}
            name="patient-surgery"
            label={r.label}
            checked={view.surgery === r.value}
            onChange={() => onChange({ surgery: r.value })}
          />
        ))}
        <Choice
          name="patient-surgery"
          label="Pick a month"
          checked={picked}
          onChange={() => onChange({ surgery: currentMonth() })}
        />
      </Group>
      {picked && (
        <div className="flex gap-2">
          <select
            aria-label="Month"
            value={month}
            onChange={(e) => setMonth(year, e.target.value)}
            className={select}
          >
            {MONTH_NAMES.map((name, i) => (
              <option key={name} value={String(i + 1).padStart(2, "0")}>
                {name}
              </option>
            ))}
          </select>
          <select
            aria-label="Year"
            value={year}
            onChange={(e) => setMonth(e.target.value, month)}
            className={`${select} flex-none basis-28`}
          >
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </div>
      )}

      <Group legend="Show">
        {PAGE_SIZES.map((n) => (
          <Choice
            key={n}
            name="patient-show"
            label={String(n)}
            checked={view.show === n}
            onChange={() => onChange({ show: n })}
          />
        ))}
      </Group>

      <button type="button" onClick={onClose} className={`${secondary} min-h-11 w-full`}>
        Done
      </button>
    </div>
  );
}

function Group({
  legend,
  hint,
  cols = "grid-cols-3",
  children,
}: {
  legend: string;
  hint?: string;
  cols?: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{legend}</legend>
      <div className={`grid gap-2 ${cols}`}>{children}</div>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </fieldset>
  );
}

/** One option of a radio group, as a pill at least 44 px tall. The native radio stays in the page
 *  (for keyboard and screen readers); the check mark means it is not shown by color alone. */
function Choice({
  name,
  label,
  checked,
  onChange,
}: {
  name: string;
  label: string;
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <label className="group relative cursor-pointer">
      <input
        type="radio"
        name={name}
        checked={checked}
        onChange={onChange}
        className="peer sr-only"
      />
      <span className="flex min-h-11 items-center justify-center gap-1 rounded-md border border-border-strong bg-surface px-3 text-sm text-ink hover:bg-surface-subtle peer-checked:border-link peer-checked:bg-primary-tint peer-checked:hover:bg-primary-tint peer-checked:font-medium peer-focus-visible:ring-2 peer-focus-visible:ring-focus">
        <span aria-hidden="true" className="hidden group-has-checked:inline">
          ✓
        </span>
        {label}
      </span>
    </label>
  );
}
