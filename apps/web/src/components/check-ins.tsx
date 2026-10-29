"use client";

import { useState } from "react";
import { CheckInDetail, Episode, reviewCheckIn } from "@/lib/clinician-api";
import { dayLabel, zoneDate } from "@/lib/episode";
import { clock, timeAgo } from "@/lib/format";
import { buildSummary } from "@/lib/symptoms";
import { Disclosure } from "./disclosure";
import { StatusBadge } from "./status-badge";

/** Is this check-in the one for this care period? There is one per care period; the api names it.
 *  (An older api does not: then it is the one whose time the care period recorded.) */
function inEpisode(c: CheckInDetail, episode: Episode): boolean {
  if (c.episode_id) return c.episode_id === episode.id;
  return (
    episode.checkin_completed_at !== null &&
    new Date(c.created_at).getTime() === new Date(episode.checkin_completed_at).getTime()
  );
}

/** The check-in part of the Physician Summary: the check-in cards folded away behind a control.
 *  Inside it, above the cards: when the check-in was done, whether it was flagged and reviewed, and
 *  the red flags. The fold is closed on load, even with an unreviewed red flag; only the clinician,
 *  or a citation chip, opens it. With nothing to fold (no check-in yet, or still loading) those
 *  lines stand on their own under the block instead.
 *
 *  Check-ins that are not this care period's (an earlier one, or none) are not listed with its
 *  facts: they are kept at the bottom of the cards, so they stay reachable and citable. */
export function CheckInsBlock({
  clinicId,
  episode,
  checkIns,
  highlighted,
  onChanged,
}: {
  clinicId: string;
  episode: Episode;
  checkIns: CheckInDetail[] | null;
  highlighted: string | null; // the page element id to flash (a cited check-in)
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);

  const here = checkIns === null ? null : checkIns.filter((c) => inEpisode(c, episode));
  const elsewhere = checkIns === null ? [] : checkIns.filter((c) => !inEpisode(c, episode));
  const flagged = here === null ? null : here.filter((c) => c.is_red_flag);
  const day = (iso: string) => zoneDate(iso, episode.timezone);

  const shown = (here?.length ?? 0) + elsewhere.length;

  const facts = (
    <ul className="space-y-0.5 text-xs text-muted">
      <li>{latestCheckIn(episode, here)}</li>
      {flagged !== null && flagged.length === 0 && <li>No red flags.</li>}
      {flagged?.map((c) => (
        <li key={c.id} className="text-flag-text">
          Red flag: check-in {day(c.created_at)} &middot;{" "}
          {c.reviewed_at ? `reviewed ${day(c.reviewed_at)}` : "not reviewed yet"}
        </li>
      ))}
    </ul>
  );

  return (
    <div className="mt-3">
      {here !== null && here.length === 0 && (
        <p className="rounded-lg border border-dashed border-border-strong p-4 text-sm text-muted">
          No check-in yet. Once the patient is invited (see Patient account above) and signs in, the check-in comes first.
        </p>
      )}

      {shown > 0 && (
        <Disclosure
          summary={open ? "Hide check-ins" : `Show check-ins (${shown})`}
          open={open}
          className=""
          onOpenChange={setOpen}
        >
          <div className="mb-3">{facts}</div>
          <ul className="space-y-2">
            {here?.map((c) => (
              <CheckInCard
                key={c.id}
                checkIn={c}
                clinicId={clinicId}
                highlighted={highlighted === `checkin-${c.id}`}
                onChanged={onChanged}
              />
            ))}
          </ul>
          {elsewhere.length > 0 && (
            <>
              <p className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wide text-muted">
                Not in this care period ({elsewhere.length})
              </p>
              <ul className="space-y-2">
                {elsewhere.map((c) => (
                  <CheckInCard
                    key={c.id}
                    checkIn={c}
                    clinicId={clinicId}
                    highlighted={highlighted === `checkin-${c.id}`}
                    onChanged={onChanged}
                  />
                ))}
              </ul>
            </>
          )}
        </Disclosure>
      )}

      {shown === 0 && <div className="mt-3">{facts}</div>}
    </div>
  );
}

/** Completed date, whether it was flagged, whether it has been reviewed. */
function latestCheckIn(episode: Episode, here: CheckInDetail[] | null): string {
  if (!episode.checkin_completed_at) return "Check-in: not done yet.";
  const day = (iso: string) => zoneDate(iso, episode.timezone);
  const done = `Latest check-in: completed ${day(episode.checkin_completed_at)}`;
  const c = (here ?? [])[0];
  if (!c) return `${done}.`;
  return [
    done,
    c.is_red_flag ? "red flag" : "no red flag",
    c.reviewed_at ? `reviewed ${day(c.reviewed_at)}` : "not reviewed yet",
  ].join(" · ");
}

function CheckInCard({
  checkIn,
  clinicId,
  highlighted,
  onChanged,
}: {
  checkIn: CheckInDetail;
  clinicId: string;
  highlighted: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  // Patient/procedure/day are already shown by this card -- the summary
  // here is just the symptom detail, built from the same definitions the
  // check-in form itself used (apps/web's lib/symptoms.ts), so it can't
  // drift out of sync with what the patient was actually asked.
  const summary = buildSummary({ answers: checkIn.answers });
  const reviewed = checkIn.reviewed_at !== null;

  async function review() {
    setBusy(true);
    try {
      await reviewCheckIn(clinicId, checkIn.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      id={`checkin-${checkIn.id}`}
      className={`rounded-lg border p-4 transition-shadow ${
        checkIn.is_red_flag && !reviewed ? "border-flag-border bg-flag-bg" : "border-border bg-surface"
      } ${highlighted ? "ring-2 ring-focus" : ""}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">
          {clock(checkIn.created_at)}
          <span className="text-muted"> &middot; {timeAgo(checkIn.created_at)}</span>
        </p>
        <div className="flex gap-2 text-xs">
          {checkIn.is_red_flag && (
            <StatusBadge variant="flag">Red flag</StatusBadge>
          )}
          {reviewed && (
            <StatusBadge variant="clear">Reviewed</StatusBadge>
          )}
        </div>
      </div>
      <p className="mt-1 text-sm text-muted">
        {[checkIn.procedure_name, checkIn.post_op_day !== null && dayLabel(checkIn.post_op_day)]
          .filter(Boolean)
          .join(" · ") || "No procedure or day given"}
      </p>
      <p className="mt-2 whitespace-pre-wrap text-sm text-ink">{summary}</p>
      {!reviewed && (
        <button
          onClick={review}
          disabled={busy}
          className="mt-3 rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-3 py-1.5 text-sm font-medium text-on-primary disabled:opacity-60"
        >
          {busy ? "Saving..." : "Mark reviewed"}
        </button>
      )}
    </li>
  );
}
