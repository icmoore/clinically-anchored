"use client";

import Link from "next/link";
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
 *  bottom, or first under the title with `noticeFirst`: the message screens put it first).
 *  `underNav`: the page already has the site's top bar above it, in a full-height column, so the
 *  frame fills what is left instead of a whole screen (which would push the notice off the bottom). */
export function PatientFrame({
  title,
  phone,
  noticeFirst = false,
  underNav = false,
  children,
}: {
  title: string;
  phone?: string | null;
  noticeFirst?: boolean;
  underNav?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={`mx-auto flex w-full max-w-md flex-col px-4 py-6 ${underNav ? "flex-1" : "min-h-screen"}`}
    >
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

/** One of the old per-episode patient links (`?token=`): they were replaced by sign-in with an
 *  email and a code, so it opens nothing. It says so, and points at the sign-in page for a patient
 *  who already has their invite. */
export function ReplacedLinkScreen() {
  const T = PATIENT_TEXT.replacedLink;
  return (
    <PatientFrame title={T.title}>
      <p className="text-ink">{T.body}</p>
      <Link href="/sign-in" className="mt-4 inline-block text-sm text-primary underline">
        {T.signIn}
      </Link>
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
