"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { AppHeader } from "@/components/app-header";
import { ClinicianProblem } from "@/components/clinician-problem";
import { StatusBadge } from "@/components/status-badge";
import { ApiError } from "@/lib/api";
import {
  Patient,
  QueueItem,
  createPatient,
  getQueue,
  listPatients,
  listProcedures,
} from "@/lib/clinician-api";
import { Procedure } from "@/lib/api";
import { categoryLabel } from "@/lib/categories";
import { dayLabel, episodeSummary } from "@/lib/episode";
import { timeAgo } from "@/lib/format";
import { useClinician } from "@/lib/use-clinician";
import { usePolling } from "@/lib/use-polling";

const REFRESH_MS = 15000;

export default function DashboardPage() {
  const clinician = useClinician();
  if (clinician.status === "loading") return <p className="p-6 text-muted">Loading...</p>;
  if (clinician.status === "error") return <ClinicianProblem problem={clinician} />;
  return (
    <>
      <AppHeader clinician={clinician} />
      <Dashboard clinicId={clinician.clinic.id} />
    </>
  );
}

function Dashboard({ clinicId }: { clinicId: string }) {
  const fetchAll = useCallback(
    async () => {
      const [queue, patients] = await Promise.all([getQueue(clinicId), listPatients(clinicId)]);
      return { queue, patients };
    },
    [clinicId],
  );
  const { data, failed } = usePolling(fetchAll, REFRESH_MS);
  const queue: QueueItem[] | null = data?.queue ?? null;
  const patients: Patient[] | null = data?.patients ?? null;
  const error = failed ? "Couldn't refresh. Retrying..." : null;

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-6">
      {error && <p className="text-sm text-flag-text">{error}</p>}

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
          Needs attention
        </h2>
        {queue === null ? (
          <p className="text-muted">Loading...</p>
        ) : queue.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border-strong p-4 text-muted">
            Nothing waiting. New check-ins and patient messages will show up here.
          </p>
        ) : (
          <ul className="space-y-2">
            {queue.map((item) => (
              <li key={item.patient_id}>
                <Link
                  href={`/dashboard/patients/${item.patient_id}`}
                  className={`block rounded-lg border p-4 hover:bg-surface-subtle ${
                    item.has_red_flag ? "border-flag-border bg-flag-bg" : "border-border bg-surface"
                  }`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <p className="font-medium text-ink">{item.patient_name ?? "Unknown"}</p>
                    <span className="shrink-0 text-xs text-muted">
                      {timeAgo(item.last_activity_at)}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs">
                    {item.has_red_flag && (
                      <StatusBadge variant="flag">Red flag</StatusBadge>
                    )}
                    {item.extension_requested && (
                      <StatusBadge variant="watch">Extension requested</StatusBadge>
                    )}
                    {item.unreviewed_check_ins > 0 && (
                      <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-muted">
                        {item.unreviewed_check_ins} check-in
                        {item.unreviewed_check_ins > 1 ? "s" : ""} to review
                      </span>
                    )}
                    {item.unread_messages > 0 && (
                      <StatusBadge variant="info">
                        {item.unread_messages} unread message
                        {item.unread_messages > 1 ? "s" : ""}
                      </StatusBadge>
                    )}
                    {item.unread_categories.map((key) => (
                      <span
                        key={key}
                        className="rounded-full border border-border bg-surface px-2 py-0.5 text-ink"
                      >
                        {categoryLabel(key)}
                      </span>
                    ))}
                    {item.latest_post_op_day !== null && (
                      <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-muted">
                        {dayLabel(item.latest_post_op_day)}
                      </span>
                    )}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
          All patients
        </h2>
        <AddPatientForm clinicId={clinicId} />
        {patients === null ? null : patients.length === 0 ? (
          <p className="mt-3 text-muted">No patients yet. Add one above.</p>
        ) : (
          <ul className="mt-3 divide-y divide-border rounded-lg border border-border bg-surface">
            {patients.map((p) => (
              <li key={p.id}>
                <Link
                  href={`/dashboard/patients/${p.id}`}
                  className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-subtle"
                >
                  <span className="min-w-0">
                    <span
                      className={`block font-medium ${
                        p.episode?.state.phase === "closed" ? "text-muted" : "text-ink"
                      }`}
                    >
                      {p.full_name}
                    </span>
                    {(p.contact || p.email) && (
                      <span className="block truncate text-sm text-muted">{p.contact ?? p.email}</span>
                    )}
                  </span>
                  <EpisodeTag patient={p} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
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

function AddPatientForm({ clinicId }: { clinicId: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [email, setEmail] = useState("");
  const [surgeryDate, setSurgeryDate] = useState("");
  const [procedureId, setProcedureId] = useState("");
  const [procedures, setProcedures] = useState<Procedure[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProcedures(clinicId)
      .then(setProcedures)
      .catch(() => setProcedures([]));
  }, [clinicId]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const patient = await createPatient(clinicId, {
        full_name: name,
        contact: contact || null,
        email: email.trim() || null,
        surgery_date: surgeryDate,
        procedure_id: procedureId,
      });
      router.push(`/dashboard/patients/${patient.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add this patient.");
      setBusy(false);
    }
  }

  const field = "min-w-0 rounded-md border border-border-strong bg-surface px-3 py-2 text-base";
  return (
    <form onSubmit={handleSubmit} className="grid gap-2 sm:grid-cols-2">
      <input
        required
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Patient name (use a fake name for now)"
        aria-label="Patient name"
        className={field}
      />
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="Email for the sign-in invite (optional)"
        aria-label="Patient email"
        className={field}
      />
      <input
        value={contact}
        onChange={(e) => setContact(e.target.value)}
        placeholder="Phone (optional)"
        aria-label="Phone"
        className={`${field} sm:col-span-2`}
      />
      <label className="flex flex-col gap-1 text-xs text-muted">
        Surgery date
        <input
          required
          type="date"
          value={surgeryDate}
          onChange={(e) => setSurgeryDate(e.target.value)}
          className={`${field} text-ink`}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted">
        Procedure
        <select
          required
          value={procedureId}
          onChange={(e) => setProcedureId(e.target.value)}
          className={`${field} text-ink`}
        >
          <option value="" disabled>
            {procedures === null ? "Loading..." : "Select one"}
          </option>
          {procedures?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        disabled={busy || !name.trim() || !surgeryDate || !procedureId}
        className="rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-base font-medium text-white disabled:opacity-60 sm:col-span-2"
      >
        Add patient
      </button>
      {error && <p className="text-sm text-flag-text sm:col-span-2">{error}</p>}
    </form>
  );
}
