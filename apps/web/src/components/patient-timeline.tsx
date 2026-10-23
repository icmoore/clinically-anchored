"use client";

import { useState } from "react";
import { PatientWindow, PatientWindowStatus } from "@/lib/api";
import { plainDay, plainTime, shortDay } from "@/lib/format";
import { PATIENT_TEXT } from "@/lib/patient-strings";

const T = PATIENT_TEXT.timeline;

// A glyph as well as a look for every state, so none depends on colour. Nothing is red or a cross:
// a period that ended unused is just a quiet grey dot.
const MARK: Record<PatientWindowStatus, { glyph: string; look: string }> = {
  used: { glyph: "✓", look: "border-clear-border bg-clear-bg text-clear-text" },
  open: {
    glyph: "●",
    look: "border-primary bg-primary-tint text-primary ring-2 ring-primary",
  },
  upcoming: { glyph: "○", look: "border-border-strong bg-surface text-muted" },
  passed: { glyph: "·", look: "border-transparent bg-surface-subtle text-muted" },
  before_check_in: { glyph: "·", look: "border-transparent bg-surface-subtle text-muted" },
};

/** The patient's nine message periods in a row. Wide enough, each shows its opening date under
 *  it; on a phone it is a compact row of circles with only the next opening day written out, and
 *  a tap on a circle says the dates and what happened in it. */
export function PatientTimeline({
  windows,
  timeZone,
  nextOpensAt,
  extra = false,
}: {
  windows: PatientWindow[];
  timeZone: string | null;
  nextOpensAt: string | null;
  /** These are the periods of extra follow-up the care team agreed to, not the first nine. */
  extra?: boolean;
}) {
  const [picked, setPicked] = useState<number | null>(null);
  if (windows.length === 0) return null;

  const describe = (w: PatientWindow) =>
    [
      extra ? PATIENT_TEXT.extension.extraPeriod(w.number, windows.length) : T.period(w.number, windows.length),
      T.state[w.status],
      T.opens(plainTime(w.opens_at, timeZone)),
      T.closes(plainTime(w.closes_at, timeZone)),
    ].join(" ");
  const chosen = windows.find((w) => w.number === picked);

  return (
    <div className="mt-3">
      <p className="text-xs font-medium text-muted">
        {extra ? PATIENT_TEXT.extension.extraHeading : T.heading}
      </p>
      <ol
        className="mt-2 grid gap-1"
        style={{ gridTemplateColumns: `repeat(${windows.length}, minmax(0, 1fr))` }}
      >
        {windows.map((w) => {
          const m = MARK[w.status];
          return (
            <li key={w.number} className="flex flex-col items-center gap-1">
              <button
                type="button"
                onClick={() => setPicked((cur) => (cur === w.number ? null : w.number))}
                aria-label={describe(w)}
                aria-pressed={picked === w.number}
                title={describe(w)}
                className={`flex h-8 w-8 items-center justify-center rounded-full border text-sm font-medium ${m.look}`}
              >
                <span aria-hidden="true">{m.glyph}</span>
              </button>
              <span aria-hidden="true" className="text-[10px] leading-none text-muted">
                {w.number}
              </span>
              <span
                aria-hidden="true"
                className="hidden text-[10px] leading-tight text-muted min-[420px]:block"
              >
                {shortDay(w.opens_at, timeZone)}
              </span>
            </li>
          );
        })}
      </ol>

      <p aria-live="polite" className="mt-2 min-h-5 text-xs text-ink">
        {chosen
          ? describe(chosen)
          : nextOpensAt && (
              <span className="min-[420px]:hidden">{T.nextOpens(plainDay(nextOpensAt, timeZone))}</span>
            )}
      </p>
      <ul aria-hidden="true" className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-muted">
        {T.legend.map((l) => (
          <li key={l.text}>
            {l.mark} {l.text}
          </li>
        ))}
      </ul>
      <p aria-hidden="true" className="mt-1 text-[11px] text-muted min-[420px]:hidden">
        {T.tapHint}
      </p>
      <p className="mt-2 text-xs text-muted">{T.onePerPeriod}</p>
    </div>
  );
}
