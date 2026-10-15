"use client";

import {
  BriefSource,
  CheckInDetail,
  Episode,
  Message,
  Touchpoint,
  WindowState,
} from "@/lib/clinician-api";
import { dayLabel, episodeSummary, plainDate, zoneDate } from "@/lib/episode";
import { inZone } from "@/lib/format";
import { CorrectForm, DISCHARGE_ROLES, Discharge } from "./episode-actions";
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
  onJump,
  onChanged,
}: {
  clinicId: string;
  role: string;
  episode: Episode | null;
  patientId: string;
  checkIns: CheckInDetail[] | null;
  thread: Message[] | null;
  touchpoints: Touchpoint[] | null;
  onJump: (source: BriefSource) => void;
  onChanged: () => void;
}) {
  if (episode === null) return null;
  const { label, detail, tone } = episodeSummary(episode);
  const s = episode.state;
  const closed = s.phase === "closed";
  const windows = s.windows ?? [];
  const flagged = checkIns === null ? null : checkIns.filter((c) => c.is_red_flag && inEpisode(c, episode));
  const day = (iso: string) => zoneDate(iso, episode.timezone);

  return (
    <Section title="Physician Summary">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink">
          {[episode.procedure_name, `surgery ${plainDate(episode.surgery_date)}`, !closed && dayLabel(s.post_op_day)]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {tone === "quiet" ? (
          <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-xs font-medium text-muted">
            {label}
          </span>
        ) : (
          <StatusBadge variant={tone}>{label}</StatusBadge>
        )}
      </div>
      <p className="mt-1 text-sm text-muted">{detail}</p>

      {windows.length > 0 && <WindowTimeline windows={windows} timeZone={episode.timezone} />}

      <ul className="mt-3 space-y-0.5 text-xs text-muted">
        <li>{latestCheckIn(episode, checkIns)}</li>
        {flagged !== null && flagged.length === 0 && <li>No red flags.</li>}
        {flagged?.map((c) => (
          <li key={c.id} className="text-flag-text">
            Red flag: check-in {day(c.created_at)} &middot;{" "}
            {c.reviewed_at ? `reviewed ${day(c.reviewed_at)}` : "not reviewed yet"}
          </li>
        ))}
        {!closed && <li>The care period ends {zoneDate(s.episode_closes_at, episode.timezone)}.</li>}
        {!closed && s.reply_available && (
          <li>The patient can send one reply to your latest message (it doesn&apos;t use a window).</li>
        )}
      </ul>

      {s.phase === "locked_checkin" && <CorrectForm clinicId={clinicId} episode={episode} onChanged={onChanged} />}

      <div className="mt-4 border-t border-border pt-4">
        <PatientBriefSection
          clinicId={clinicId}
          patientId={patientId}
          timeZone={episode.timezone}
          thread={thread}
          checkIns={checkIns}
          touchpoints={touchpoints}
          onJump={onJump}
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

/** Is this check-in the one for this care period? There is one per care period; the api names it.
 *  (An older api does not: then it is the one whose time the care period recorded.) */
function inEpisode(c: CheckInDetail, episode: Episode): boolean {
  if (c.episode_id) return c.episode_id === episode.id;
  return (
    episode.checkin_completed_at !== null &&
    new Date(c.created_at).getTime() === new Date(episode.checkin_completed_at).getTime()
  );
}

/** Completed date, whether it was flagged, whether it has been reviewed. */
function latestCheckIn(episode: Episode, checkIns: CheckInDetail[] | null): string {
  if (!episode.checkin_completed_at) return "Check-in: not done yet.";
  const day = (iso: string) => zoneDate(iso, episode.timezone);
  const done = `Latest check-in: completed ${day(episode.checkin_completed_at)}`;
  const c = (checkIns ?? []).find((x) => inEpisode(x, episode));
  if (!c) return `${done}.`;
  return [
    done,
    c.is_red_flag ? "red flag" : "no red flag",
    c.reviewed_at ? `reviewed ${day(c.reviewed_at)}` : "not reviewed yet",
  ].join(" · ");
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

function WindowTimeline({ windows, timeZone }: { windows: WindowState[]; timeZone: string }) {
  const by = (reason: keyof typeof WHY) =>
    windows.filter((w) => w.status === "missed" && w.missed_reason === reason).map((w) => w.number);
  const beforeCheckIn = by("closed_before_check_in");
  const noMessage = by("no_message");
  const plural = (n: number[]) => (n.length === 1 ? `window ${n[0]}` : `windows ${n.join(", ")}`);

  return (
    <div className="mt-4">
      <ol className="grid grid-cols-9 gap-1" aria-label="Message windows">
        {windows.map((w) => {
          const m = MARKER[w.status];
          const why = w.missed_reason ? `, ${WHY[w.missed_reason]}` : "";
          const hover = `Window ${w.number}: ${m.word}${why}. Opens ${inZone(w.opens_at, timeZone)}.`;
          return (
            <li key={w.number} title={hover} aria-label={hover} className="flex flex-col items-center gap-1">
              <span className={`flex h-8 w-8 items-center justify-center rounded-full border text-xs font-medium ${m.box}`}>
                <span aria-hidden="true">{w.status === "open" || w.status === "locked" ? w.number : m.glyph}</span>
              </span>
              <span aria-hidden="true" className="text-[10px] leading-none text-muted">
                {w.number}
              </span>
              <span aria-hidden="true" className="text-[10px] leading-none text-muted">
                {zoneDate(w.opens_at, timeZone)}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="mt-2 text-[11px] text-muted">
        {"✓"} message received &middot; {"✕"} missed &middot; numbered circle: open now &middot;{" "}
        {"○"} upcoming
        {windows.some((w) => w.status === "locked") &&
          " · dashed numbered circle: open by the clock, waiting for the check-in"}
        . Dates are when each window opens.
      </p>
      {(beforeCheckIn.length > 0 || noMessage.length > 0) && (
        <ul className="mt-1 space-y-0.5 text-xs text-muted">
          {beforeCheckIn.length > 0 && (
            <li>
              Missed, closed before the check-in: {plural(beforeCheckIn)}.
            </li>
          )}
          {noMessage.length > 0 && <li>Missed, no message sent: {plural(noMessage)}.</li>}
          <li>A window that is not used is not carried over.</li>
        </ul>
      )}
    </div>
  );
}
