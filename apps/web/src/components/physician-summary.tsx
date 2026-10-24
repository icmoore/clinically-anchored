"use client";

import {
  CheckInDetail,
  Episode,
  Message,
  Touchpoint,
  WindowState,
} from "@/lib/clinician-api";
import { dayLabel, episodeSummary, plainDate, zoneDate } from "@/lib/episode";
import { inZone } from "@/lib/format";
import { CareProviderSummarySection } from "./care-provider-summary";
import { CheckInsBlock } from "./check-ins";
import { CorrectForm, DISCHARGE_ROLES, Discharge } from "./episode-actions";
import { ExtensionRequestCard } from "./extension-request";
import { PatientBriefSection } from "./patient-brief";
import { Section } from "./section";
import { StatusBadge } from "./status-badge";

/** Where the care period stands, for the physician. The facts at the top are worked out from the
 *  schedule, the clock and the record: nothing there is written by AI, so none of it carries the AI
 *  badge. Below a line, set visibly apart, is the AI patient summary (on request only). Last come
 *  the two things only the clinic can do to a care period: fix a typo before the check-in
 *  (with the facts it changes), and discharge. */
export function PhysicianSummarySection({
  clinicId,
  role,
  episode,
  patientId,
  checkIns,
  thread,
  touchpoints,
  highlighted,
  onChanged,
}: {
  clinicId: string;
  role: string;
  episode: Episode | null;
  patientId: string;
  checkIns: CheckInDetail[] | null;
  thread: Message[] | null;
  touchpoints: Touchpoint[] | null;
  highlighted: string | null; // the page element id to flash (a cited check-in)
  onChanged: () => void;
}) {
  if (episode === null) return null;
  const { label, detail, tone } = episodeSummary(episode);
  const s = episode.state;
  const closed = s.phase === "closed";
  // The nine windows, then the six of each follow-up extension, as rows of their own.
  const windows = [...(episode.extension?.earlier_windows ?? []), ...(s.windows ?? [])];
  const request = episode.extension?.request ?? null;

  return (
    <Section title="Physician Summary">
      <div className="flex flex-wrap">
        {/* A neutral badge, not a StatusBadge: the procedure is not a status, and the status colors
            keep their meaning. rounded-2xl rather than rounded-full so it wraps cleanly on a phone. */}
        <p className="rounded-2xl border border-border-strong bg-surface-subtle px-3 py-1 text-base font-semibold text-ink">
          {[episode.procedure_name, `surgery ${plainDate(episode.surgery_date)}`, !closed && dayLabel(s.post_op_day)]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>

      <CheckInsBlock
        clinicId={clinicId}
        episode={episode}
        checkIns={checkIns}
        highlighted={highlighted}
        onChanged={onChanged}
      />

      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <p className="text-sm text-muted">{detail}</p>
        {tone === "quiet" ? (
          <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-xs font-medium text-muted">
            {label}
          </span>
        ) : (
          <StatusBadge variant={tone}>{label}</StatusBadge>
        )}
      </div>

      {request && (
        <ExtensionRequestCard
          clinicId={clinicId}
          role={role}
          episode={episode}
          request={request}
          onChanged={onChanged}
        />
      )}

      {windows.length > 0 && <WindowTimeline windows={windows} timeZone={episode.timezone} surgeryDate={episode.surgery_date} />}

      {!closed && s.reply_available && (
        <p className="mt-3 text-xs text-muted">
          The patient can send one reply to your latest message (it doesn&apos;t use a window).
        </p>
      )}

      {s.phase === "locked_checkin" && <CorrectForm clinicId={clinicId} episode={episode} onChanged={onChanged} />}

      <div className="mt-4 border-t border-border pt-4">
        <PatientBriefSection
          clinicId={clinicId}
          patientId={patientId}
          timeZone={episode.timezone}
          thread={thread}
          checkIns={checkIns}
          touchpoints={touchpoints}
        />
      </div>

      <div className="mt-4 border-t border-border pt-4">
        <CareProviderSummarySection
          clinicId={clinicId}
          patientId={patientId}
          timeZone={episode.timezone}
          thread={thread}
          checkIns={checkIns}
          touchpoints={touchpoints}
        />
      </div>

      {!closed && DISCHARGE_ROLES.includes(role) && (
        <Discharge clinicId={clinicId} episodeId={episode.id} onChanged={onChanged} />
      )}

      <p className="mt-4 text-[11px] text-muted">
        The facts above are worked out from the schedule and the record, not written by AI. Schedule
        version {episode.schedule_version}.
      </p>
    </Section>
  );
}

// Glyphs as well as colours, so a window's state is never shown by colour alone.
const MARKER: Record<WindowState["status"], { glyph: string; box: string; word: string }> = {
  used: { glyph: "✓", box: "border-clear-border bg-clear-bg text-clear-text", word: "message received" },
  missed: { glyph: "✕", box: "border-border-strong bg-surface-subtle text-muted", word: "missed" },
  open: { glyph: "●", box: "border-info-border bg-info-bg text-info-text ring-2 ring-info-border", word: "open now" },
  locked: { glyph: "●", box: "border-dashed border-watch-border bg-watch-bg text-watch-text", word: "open, waiting for the check-in" },
  upcoming: { glyph: "○", box: "border-dashed border-border-strong bg-surface text-muted", word: "upcoming" },
  cut_short: { glyph: "–", box: "border-dashed border-border bg-surface text-muted", word: "not reached (care period closed)" },
};

const WHY: Record<NonNullable<WindowState["missed_reason"]>, string> = {
  closed_before_check_in: "closed before the check-in",
  no_message: "no message sent",
};

/** The calendar day (YYYY-MM-DD) an instant falls on in the clinic's time zone. */
function localDay(iso: string, timeZone: string): string {
  return new Date(iso).toLocaleDateString("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}
const dayNumber = (day: string) => Date.parse(`${day}T00:00:00Z`) / 86_400_000;

/** Whole days from the previous window's opening day to this one's; for window 1, from the day of
 *  surgery. Counted in calendar days in the clinic's zone, so a daylight-saving change never makes
 *  it 6 or 8. Worked out from the schedule's own times, never typed in. */
function daysSincePrevious(windows: WindowState[], i: number, surgeryDate: string, timeZone: string): number {
  const before = i === 0 ? surgeryDate : localDay(windows[i - 1].opens_at, timeZone);
  return dayNumber(localDay(windows[i].opens_at, timeZone)) - dayNumber(before);
}

/** The windows grouped by care period: the first nine, then one group per follow-up extension. */
function groupWindows(windows: WindowState[]): WindowState[][] {
  const groups: WindowState[][] = [];
  for (const w of windows) {
    const n = w.extension_no ?? 0;
    if (!groups[n]) groups[n] = [];
    groups[n].push(w);
  }
  return groups.filter(Boolean);
}
const daysLabel = (n: number) => `${n} day${n === 1 ? "" : "s"}`;

function WindowTimeline({
  windows,
  timeZone,
  surgeryDate,
}: {
  windows: WindowState[];
  timeZone: string;
  surgeryDate: string;
}) {
  const groups = groupWindows(windows);
  const extended = groups.length > 1;
  return (
    <div className="mt-4 space-y-4">
      {groups.map((group, g) => (
        <div key={g}>
          {extended && (
            <p className="mb-1 text-xs font-medium text-ink">
              {g === 0 ? "Care period" : groups.length > 2 ? `Extension ${g}` : "Extension"}
              {g > 0 && (
                <span className="font-normal text-muted">
                  {" "}
                  &middot; {group.length} windows, from when the clinic accepted
                </span>
              )}
            </p>
          )}
          <WindowRow
            windows={group}
            extensionNo={g}
            timeZone={timeZone}
            surgeryDate={surgeryDate}
          />
        </div>
      ))}
      <p className="text-[11px] text-muted">
        {"✓"} message received &middot; {"✕"} missed &middot; {"●"} open now &middot; {"○"} upcoming
        {windows.some((w) => w.status === "locked") &&
          " · dashed ●: open by the clock, waiting for the check-in"}
        . Under each circle: days since the previous window opened (window 1: since surgery
        {extended && "; the first of an extension: since it was accepted"}), then the date it opens.
      </p>
    </div>
  );
}

function WindowRow({
  windows,
  extensionNo,
  timeZone,
  surgeryDate,
}: {
  windows: WindowState[];
  extensionNo: number;
  timeZone: string;
  surgeryDate: string;
}) {
  const noun = extensionNo > 0 ? "Extension window" : "Window";
  return (
    <ol
      className="grid gap-1"
      style={{ gridTemplateColumns: `repeat(${windows.length}, minmax(0, 1fr))` }}
      aria-label={extensionNo > 0 ? "Follow-up extension message windows" : "Message windows"}
    >
      {windows.map((w, i) => {
        const m = MARKER[w.status];
        const why = w.missed_reason ? `, ${WHY[w.missed_reason]}` : "";
        // The first window of an extension opens at the acceptance: no "days since" to show.
        const accepted = extensionNo > 0 && i === 0;
        const days = accepted
          ? "accepted"
          : daysLabel(daysSincePrevious(windows, i, surgeryDate, timeZone));
        const since = accepted
          ? "when the clinic accepted the request"
          : `${days} after ${i === 0 ? "surgery" : "the previous window"}`;
        const hover = `${noun} ${w.number}: ${m.word}${why}. Opens ${inZone(w.opens_at, timeZone)}, ${since}.`;
        return (
          <li key={w.number} title={hover} aria-label={hover} className="flex flex-col items-center gap-1">
            <span aria-hidden="true" className="text-xs font-medium leading-none text-ink">
              {w.number}
            </span>
            <span
              className={`flex h-8 w-8 items-center justify-center rounded-full border text-xs font-medium ${m.box} ${
                extensionNo > 0 ? "border-dotted" : ""
              }`}
            >
              <span aria-hidden="true">{m.glyph}</span>
            </span>
            <span aria-hidden="true" className="text-[10px] leading-none text-ink">
              {days}
            </span>
            <span aria-hidden="true" className="text-[10px] leading-none text-muted">
              {zoneDate(w.opens_at, timeZone)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
