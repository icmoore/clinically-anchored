"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { PATIENT_TEXT } from "@/lib/patient-strings";

/** Set by the signed-in patient's wrapper (components/patient-gate.tsx): what "Sign out" does.
 *  Screens reached by a legacy link are not inside it, so they show no button. */
export const SignOutContext = createContext<(() => void) | null>(null);

/** Set by the signed-in patient's wrapper while the account is active: what "Stop using the app"
 *  does (record it, sign out). It throws if the api could not do it. Null: the menu is not shown. */
export const WithdrawContext = createContext<(() => Promise<void>) | null>(null);

const headerButton =
  "shrink-0 rounded-md border border-link px-3 py-1.5 text-sm text-link hover:border-link-hover hover:text-link-hover hover:bg-primary-tint";

/** The patient's "Sign out" (always visible, because a shared phone must be easy to sign out of),
 *  and, when the account is active, an "Account" menu holding "Stop using the app". Nothing when
 *  nobody is signed in. */
export function SignOutButton() {
  const signOut = useContext(SignOutContext);
  const withdraw = useContext(WithdrawContext);
  if (!signOut) return null;
  return (
    <div className="flex shrink-0 items-start gap-2">
      <button type="button" onClick={signOut} className={headerButton}>
        {PATIENT_TEXT.signOut}
      </button>
      {withdraw && <AccountMenu withdraw={withdraw} />}
    </div>
  );
}

/** "Account" menu with one item, "Stop using the app", which asks once more before it does it. */
function AccountMenu({ withdraw }: { withdraw: () => Promise<void> }) {
  const T = PATIENT_TEXT.accountMenu;
  const S = PATIENT_TEXT.stopUsing;
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  // Close the menu on a click outside it or on Escape.
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (confirming && !el.open) el.showModal();
    if (!confirming && el.open) el.close();
  }, [confirming]);

  async function confirm() {
    setBusy(true);
    setFailed(false);
    try {
      await withdraw();
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={headerButton}
      >
        {T.button}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-10 mt-1 w-56 rounded-md border border-border bg-surface p-1 shadow-md"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              setConfirming(true);
            }}
            className="block w-full rounded px-3 py-2 text-left text-sm text-ink hover:bg-surface-subtle"
          >
            {T.stopUsing}
          </button>
        </div>
      )}
      <dialog
        ref={dialog}
        onClose={() => !busy && setConfirming(false)}
        aria-labelledby="stop-using-title"
        className="m-auto w-[calc(100%-2rem)] max-w-md rounded-lg border border-border bg-surface p-5 text-ink backdrop:bg-black/40"
      >
        <h2 id="stop-using-title" className="text-lg font-semibold">
          {S.title}
        </h2>
        <p className="mt-2 text-sm">{S.body}</p>
        {failed && (
          <p role="alert" className="mt-3 text-sm text-flag-text">
            {S.failed}
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setConfirming(false)}
            disabled={busy}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-on-primary hover:bg-primary-hover disabled:opacity-60"
          >
            {S.cancel}
          </button>
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={busy}
            className="rounded-md border border-flag-border px-4 py-2 text-sm font-medium text-flag-text hover:bg-flag-bg disabled:opacity-60"
          >
            {busy ? S.working : S.confirm}
          </button>
        </div>
      </dialog>
    </div>
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
    <div className="mx-auto flex min-h-screen w-full max-w-md flex-col px-4 py-6">
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
      <Link href="/sign-in" className="mt-4 inline-block text-sm text-link underline hover:text-link-hover">
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
