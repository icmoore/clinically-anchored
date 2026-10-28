"use client";

import Link from "next/link";
import { useCallback } from "react";
import { AppHeader } from "@/components/app-header";
import { ClinicianProblem } from "@/components/clinician-problem";
import { DemoEmail, listDemoInbox } from "@/lib/clinician-api";
import { clock } from "@/lib/format";
import { useClinician } from "@/lib/use-clinician";
import { usePolling } from "@/lib/use-polling";

const REFRESH_MS = 5000;

const KIND: Record<DemoEmail["template"], string> = {
  invite: "Invite",
  sign_in_code: "Sign-in code",
};

/** The emails a demo clinic did not send, newest first, so a demo can show the whole sign-in without
 *  a real mailbox. Only an owner of a demo clinic gets here (the api refuses everyone else). */
export default function DemoInboxPage() {
  const clinician = useClinician();
  if (clinician.status === "loading") return <p className="p-6 text-muted">Loading...</p>;
  if (clinician.status === "error") return <ClinicianProblem problem={clinician} />;
  const { clinic } = clinician;
  return (
    <>
      <AppHeader clinician={clinician} />
      <main className="mx-auto max-w-3xl space-y-4 px-4 py-6">
        <div>
          <Link href="/dashboard" className="text-sm text-muted hover:underline">
            &larr; Dashboard
          </Link>
          <h1 className="mt-1 text-xl font-semibold text-ink">Demo inbox</h1>
        </div>
        {clinic.is_demo && clinic.role === "owner" ? (
          <Inbox clinicId={clinic.id} />
        ) : (
          <p className="rounded-lg border border-dashed border-border-strong p-4 text-muted">
            The demo inbox is only for owners of a demo clinic.
          </p>
        )}
      </main>
    </>
  );
}

function Inbox({ clinicId }: { clinicId: string }) {
  const fetchInbox = useCallback(() => listDemoInbox(clinicId), [clinicId]);
  const { data, failed } = usePolling(fetchInbox, REFRESH_MS);
  return (
    <>
      <p
        role="note"
        className="rounded-lg border border-watch-border bg-watch-bg p-3 text-sm font-medium text-watch-text"
      >
        Demo clinic: emails are not sent.
      </p>
      {failed && <p className="text-sm text-flag-text">Couldn&apos;t refresh. Retrying...</p>}
      {data === null ? (
        <p className="text-muted">Loading...</p>
      ) : data.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border-strong p-4 text-muted">
          No emails yet. An invite or a sign-in code for a patient of this clinic will show up here.
        </p>
      ) : (
        <ul className="space-y-3">
          {data.map((mail) => (
            <li key={mail.id} className="rounded-lg border border-border bg-surface p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <p className="text-sm text-muted">
                  To <span className="break-all font-medium text-ink">{mail.recipient}</span>
                </p>
                <p className="text-xs text-muted">
                  {KIND[mail.template]} &middot; {clock(mail.created_at)}
                </p>
              </div>
              <p className="mt-2 font-medium text-ink">{mail.subject}</p>
              <pre className="mt-2 whitespace-pre-wrap break-words rounded-md bg-surface-subtle p-3 font-sans text-sm text-ink">
                {mail.text}
              </pre>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
