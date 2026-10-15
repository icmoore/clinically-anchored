"use client";

import { useEffect, useState } from "react";
import { ApiError, Procedure } from "@/lib/api";
import { Episode, correctEpisode, dischargeEpisode, listProcedures } from "@/lib/clinician-api";
import { danger, primary, secondary } from "./buttons";

// Matches the api: only owners and clinicians may discharge.
export const DISCHARGE_ROLES = ["owner", "clinician"];

/** Only before the patient's check-in: after it every window has been counted from the date. */
export function CorrectForm({
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

export function Discharge({
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
