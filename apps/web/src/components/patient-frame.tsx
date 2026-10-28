"use client";

import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { PATIENT_TEXT } from "@/lib/patient-strings";

/** Set by the signed-in patient's wrapper (components/patient-gate.tsx): what "Sign out" does.
 *  Screens reached by a legacy link are not inside it, so they show no button. */
export const SignOutContext = createContext<(() => void) | null>(null);

/** The patient's "Sign out", wherever a patient is signed in; nothing otherwise. */
export function SignOutButton() {
  const signOut = useContext(SignOutContext);
  if (!signOut) return null;
  return (
    <button
      type="button"
      onClick={signOut}
      className="shrink-0 rounded-md border border-primary px-3 py-1.5 text-sm text-primary hover:bg-primary-tint"
    >
      {PATIENT_TEXT.signOut}
    </button>
  );
}

/** Urgent problems are handled outside the app, and every patient screen says so (the locked and
 *  closed ones most of all). The wording is a first draft: rewrite it here, it is shown everywhere. */
export function UrgentNotice({
  phone,
  first = false,
}: {
  phone?: string | null;
  /** Shown at the top of a screen (under the title) instead of at the bottom. Same words. */
  first?: boolean;
}) {
  return (
    <div
      role="note"
      className={`${first ? "mb-4" : "mt-8"} rounded-lg border border-flag-border bg-flag-bg p-4 text-sm text-flag-text`}
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

/** The page every patient screen sits in: a narrow column, a title, and the urgent notice (at the
 *  bottom, or first under the title with `noticeFirst`: the message screens put it first). */
export function PatientFrame({
  title,
  phone,
  noticeFirst = false,
  children,
}: {
  title: string;
  phone?: string | null;
  noticeFirst?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col px-4 py-6">
      <div className="mb-4 flex items-start justify-between gap-3">
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        <SignOutButton />
      </div>
      {noticeFirst && <UrgentNotice phone={phone} first />}
      <div className="flex-1">{children}</div>
      {!noticeFirst && <UrgentNotice phone={phone} />}
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
    <PatientFrame title="Your follow-up has ended" phone={phone} noticeFirst>
      <p className="text-ink">{PATIENT_TEXT.status.ended}</p>
    </PatientFrame>
  );
}
