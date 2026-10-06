"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { AppHeader } from "@/components/app-header";
import { StatusBadge } from "@/components/status-badge";
import { ApiError } from "@/lib/api";
import { Patient, QueueItem, createPatient, getQueue, listPatients } from "@/lib/clinician-api";
import { timeAgo } from "@/lib/format";
import { useClinician } from "@/lib/use-clinician";
import { usePolling } from "@/lib/use-polling";

const REFRESH_MS = 15000;

export default function DashboardPage() {
  const clinician = useClinician();
  if (clinician.status === "loading") return <p className="p-6 text-muted">Loading...</p>;
  if (clinician.status === "error") return <p className="p-6 text-flag-text">{clinician.message}</p>;
  return (
    <>
      <AppHeader clinicName={clinician.clinic.name} email={clinician.email} />
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
                    {item.latest_post_op_day !== null && (
                      <span className="rounded-full border border-border bg-surface-subtle px-2 py-0.5 text-muted">
                        Post-op day {item.latest_post_op_day}
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
                  <span className="font-medium text-ink">{p.full_name}</span>
                  <span className="truncate text-sm text-muted">{p.contact}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function AddPatientForm({ clinicId }: { clinicId: string }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [contact, setContact] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const patient = await createPatient(clinicId, {
        full_name: name,
        contact: contact || null,
      });
      router.push(`/dashboard/patients/${patient.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add this patient.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 sm:flex-row">
      <input
        required
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Patient name (use a fake name for now)"
        className="min-w-0 flex-1 rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
      />
      <input
        value={contact}
        onChange={(e) => setContact(e.target.value)}
        placeholder="Phone or email (optional)"
        className="min-w-0 flex-1 rounded-md border border-border-strong bg-surface px-3 py-2 text-base"
      />
      <button
        type="submit"
        disabled={busy || !name.trim()}
        className="rounded-md bg-primary enabled:hover:bg-primary-hover enabled:active:bg-primary-active px-4 py-2 text-base font-medium text-white disabled:opacity-60"
      >
        Add patient
      </button>
      {error && <p className="text-sm text-flag-text sm:basis-full">{error}</p>}
    </form>
  );
}
