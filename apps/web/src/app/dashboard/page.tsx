"use client";

import Link from "next/link";
import { Suspense, useCallback, useState } from "react";
import { PatientManagement } from "@/components/add-patient";
import { AppHeader } from "@/components/app-header";
import { ClinicianProblem } from "@/components/clinician-problem";
import { AllPatients } from "@/components/patient-list";
import { StatusBadge } from "@/components/status-badge";
import { getQueue } from "@/lib/clinician-api";
import { categoryLabel } from "@/lib/categories";
import { dayLabel } from "@/lib/episode";
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
  // "Needs attention" is never paged or filtered and refreshes every 15 s. The patient list below
  // it loads and refreshes on its own (components/patient-list.tsx); `listVersion` makes it
  // reload when a patient has just been added.
  const fetchQueue = useCallback(() => getQueue(clinicId), [clinicId]);
  const { data: queue, failed } = usePolling(fetchQueue, REFRESH_MS);
  const [listVersion, setListVersion] = useState(0);
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

      <Suspense fallback={<p className="text-muted">Loading...</p>}>
        <AllPatients clinicId={clinicId} version={listVersion} />
      </Suspense>

      <PatientManagement clinicId={clinicId} onAdded={() => setListVersion((v) => v + 1)} />
    </main>
  );
}
