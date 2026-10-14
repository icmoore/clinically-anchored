import type { ReactNode } from "react";

/** Urgent problems are handled outside the app, and every patient screen says so (the locked and
 *  closed ones most of all). The wording is a first draft: rewrite it here, it is shown everywhere. */
export function UrgentNotice({ phone }: { phone?: string | null }) {
  return (
    <div
      role="note"
      className="mt-8 rounded-lg border border-flag-border bg-flag-bg p-4 text-sm text-flag-text"
    >
      <p className="font-medium">Is this urgent?</p>
      <p className="mt-1">
        This app is not for emergencies. For an urgent problem, call the office
        {phone ? (
          <>
            {" "}
            at{" "}
            <a href={`tel:${phone.replace(/[^\d+]/g, "")}`} className="font-medium underline">
              {phone}
            </a>
          </>
        ) : null}{" "}
        or go to the nearest emergency department.
      </p>
    </div>
  );
}

/** The page every patient screen sits in: a narrow column, a title, and the urgent notice. */
export function PatientFrame({
  title,
  phone,
  children,
}: {
  title: string;
  phone?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col px-4 py-6">
      <h1 className="mb-4 text-xl font-semibold text-ink">{title}</h1>
      <div className="flex-1">{children}</div>
      <UrgentNotice phone={phone} />
    </div>
  );
}

/** The link was never valid, was replaced ("reset") by the clinic, or has expired. */
export function InvalidLinkScreen({ phone }: { phone?: string | null }) {
  return (
    <PatientFrame title="This link doesn't work" phone={phone}>
      <p className="text-ink">
        This link is not valid any more. The care team may have sent you a newer one. If you
        can&apos;t find it, please call the office.
      </p>
    </PatientFrame>
  );
}

/** The post-op period is over (the last message window ended, or the care team discharged you). */
export function ClosedScreen({ phone }: { phone?: string | null }) {
  return (
    <PatientFrame title="Your follow-up has ended" phone={phone}>
      <p className="text-ink">
        Your post-op follow-up with this app is over, so this link no longer opens your messages or
        check-in.
      </p>
      <p className="mt-3 text-sm text-muted">
        If you have a question, please call the office.
      </p>
    </PatientFrame>
  );
}
