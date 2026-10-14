"use client";

import { useEffect, useState } from "react";
import { ApiError, Procedure } from "@/lib/api";
import {
  Episode,
  IssuedLink,
  correctEpisode,
  dischargeEpisode,
  issueLink,
  listProcedures,
  resetLink,
} from "@/lib/clinician-api";
import { dayLabel, episodeSummary, plainDate } from "@/lib/episode";
import { clock } from "@/lib/format";
import { StatusBadge } from "./status-badge";

// Matches the api: only owners and clinicians may discharge.
const DISCHARGE_ROLES = ["owner", "clinician"];

const secondary =
  "rounded-md border border-primary text-primary enabled:hover:bg-primary-tint px-3 py-2 text-sm font-medium disabled:opacity-60";
const primary =
  "rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-3 py-2 text-sm font-medium text-white disabled:opacity-60";
const danger =
  "rounded-md border border-flag-border bg-surface px-3 py-2 text-sm font-medium text-flag-text enabled:hover:bg-flag-bg disabled:opacity-60";

/** The patient's care period: what the clinic entered, where the schedule stands, the one link
 *  the patient uses, and the two things only the clinic can do (fix a typo before the check-in,
 *  discharge). What is open or used right now is computed by the api. */
export function EpisodeCard({
  clinicId,
  role,
  episode,
  onChanged,
}: {
  clinicId: string;
  role: string;
  episode: Episode | null;
  onChanged: () => void;
}) {
  if (episode === null) {
    return (
      <section className="rounded-lg border border-dashed border-border-strong p-4 text-sm text-muted">
        This patient has no care period yet.
      </section>
    );
  }
  const { label, detail, tone } = episodeSummary(episode);
  const s = episode.state;
  const closed = s.phase === "closed";

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">Care period</h2>
        {tone === "quiet" ? (
          <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-xs font-medium text-muted">
            {label}
          </span>
        ) : (
          <StatusBadge variant={tone}>{label}</StatusBadge>
        )}
      </div>
      <p className="mt-2 text-sm text-ink">
        {[episode.procedure_name, `surgery ${plainDate(episode.surgery_date)}`, !closed && dayLabel(s.post_op_day)]
          .filter(Boolean)
          .join(" · ")}
      </p>
      <p className="mt-1 text-sm text-muted">{detail}</p>
      <ul className="mt-2 space-y-0.5 text-xs text-muted">
        <li>
          Check-in:{" "}
          {episode.checkin_completed_at ? `done ${clock(episode.checkin_completed_at)}` : "not done yet"}
        </li>
        {s.missed.length > 0 && (
          <li>
            Windows that passed without a message: {s.missed.join(", ")} (a window not used is not
            carried over)
          </li>
        )}
        {!closed && s.reply_available && (
          <li>The patient can send one reply to your latest message (it doesn&apos;t use a window).</li>
        )}
        <li>Schedule version {episode.schedule_version}</li>
      </ul>

      {!closed && <LinkControls clinicId={clinicId} episodeId={episode.id} />}
      {s.phase === "locked_checkin" && (
        <CorrectForm clinicId={clinicId} episode={episode} onChanged={onChanged} />
      )}
      {!closed && DISCHARGE_ROLES.includes(role) && (
        <Discharge clinicId={clinicId} episodeId={episode.id} onChanged={onChanged} />
      )}
    </section>
  );
}

function LinkControls({ clinicId, episodeId }: { clinicId: string; episodeId: string }) {
  const [link, setLink] = useState<IssuedLink | null>(null);
  const [busy, setBusy] = useState<"copy" | "reset" | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(kind: "copy" | "reset") {
    setBusy(kind);
    setError(null);
    setCopied(false);
    try {
      const issued = kind === "copy" ? await issueLink(clinicId, episodeId) : await resetLink(clinicId, episodeId);
      setLink(issued);
      setConfirmReset(false);
      try {
        await navigator.clipboard.writeText(issued.url);
        setCopied(true);
      } catch {
        // Clipboard can be blocked (e.g. non-HTTPS); the URL is shown below to copy by hand.
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create that link.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="text-sm font-medium text-ink">Patient link</p>
      <p className="mt-0.5 text-xs text-muted">
        One link for the whole care period: check-in, then messages. Text or email it to the patient
        yourself. It stops working when the care period ends, or if you reset it.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <button onClick={() => run("copy")} disabled={busy !== null} className={secondary}>
          Copy patient link
        </button>
        {!confirmReset && (
          <button onClick={() => setConfirmReset(true)} disabled={busy !== null} className={secondary}>
            Reset link...
          </button>
        )}
      </div>
      {confirmReset && (
        <div className="mt-2 rounded-md border border-watch-border bg-watch-bg p-3 text-sm text-watch-text">
          <p>The current link stops working at once. You will need to send the patient the new one.</p>
          <div className="mt-2 flex gap-2">
            <button onClick={() => run("reset")} disabled={busy !== null} className={primary}>
              {busy === "reset" ? "Resetting..." : "Reset and copy new link"}
            </button>
            <button onClick={() => setConfirmReset(false)} disabled={busy !== null} className={secondary}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-flag-text">{error}</p>}
      {link && (
        <div className="mt-2 space-y-1">
          <p className="text-xs text-muted">{copied ? "Copied. " : "Copy this link: "}</p>
          <input
            readOnly
            value={link.url}
            aria-label="Patient link"
            onFocus={(e) => e.currentTarget.select()}
            className="w-full rounded-md border border-border-strong bg-surface-subtle px-2 py-1.5 font-mono text-xs"
          />
        </div>
      )}
    </div>
  );
}

/** Only before the patient's check-in: after it every window has been counted from the date. */
function CorrectForm({
  clinicId,
  episode,
  onChanged,
}: {
  clinicId: string;
  episode: Episode;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(episode.surgery_date);
  const [procedureId, setProcedureId] = useState(episode.procedure_id);
  const [procedures, setProcedures] = useState<Procedure[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) listProcedures(clinicId).then(setProcedures).catch(() => setProcedures([]));
  }, [open, clinicId]);

  const changed = date !== episode.surgery_date || procedureId !== episode.procedure_id;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await correctEpisode(clinicId, episode.id, {
        ...(date !== episode.surgery_date ? { surgery_date: date } : {}),
        ...(procedureId !== episode.procedure_id ? { procedure_id: procedureId } : {}),
      });
      setOpen(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save that change.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <p className="mt-3 text-xs text-muted">
        Wrong surgery date or procedure?{" "}
        <button onClick={() => setOpen(true)} className="underline">
          Fix it
        </button>{" "}
        (possible until the patient has checked in).
      </p>
    );
  }
  const field = "rounded-md border border-border-strong bg-surface px-2 py-1.5 text-sm text-ink";
  return (
    <form onSubmit={save} className="mt-3 flex flex-wrap items-end gap-2 border-t border-border pt-3">
      <label className="flex flex-col gap-1 text-xs text-muted">
        Surgery date
        <input type="date" required value={date} onChange={(e) => setDate(e.target.value)} className={field} />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Procedure
        <select value={procedureId} onChange={(e) => setProcedureId(e.target.value)} className={field}>
          {procedures.length === 0 && <option value={episode.procedure_id}>{episode.procedure_name}</option>}
          {procedures.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" disabled={busy || !changed} className={primary}>
        {busy ? "Saving..." : "Save"}
      </button>
      <button type="button" onClick={() => setOpen(false)} disabled={busy} className={secondary}>
        Cancel
      </button>
      {error && <p className="basis-full text-sm text-flag-text">{error}</p>}
    </form>
  );
}

function Discharge({
  clinicId,
  episodeId,
  onChanged,
}: {
  clinicId: string;
  episodeId: string;
  onChanged: () => void;
}) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function discharge() {
    setBusy(true);
    setError(null);
    try {
      await dischargeEpisode(clinicId, episodeId);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't discharge this patient.");
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 border-t border-border pt-3">
      {!confirm ? (
        <button onClick={() => setConfirm(true)} className={danger}>
          Discharge patient...
        </button>
      ) : (
        <div className="rounded-md border border-flag-border bg-flag-bg p-3 text-sm text-flag-text">
          <p className="font-medium">Discharge this patient?</p>
          <p className="mt-1">
            Their link stops working at once and no more messages can be sent either way. This
            can&apos;t be undone.
          </p>
          <div className="mt-2 flex gap-2">
            <button onClick={discharge} disabled={busy} className={danger}>
              {busy ? "Discharging..." : "Yes, discharge"}
            </button>
            <button onClick={() => setConfirm(false)} disabled={busy} className={secondary}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-flag-text">{error}</p>}
    </div>
  );
}
