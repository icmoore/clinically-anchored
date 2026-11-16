"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { clinicLabels } from "@/lib/clinic-choice";
import { supabase } from "@/lib/supabase";
import type { ReadyClinician } from "@/lib/use-clinician";

/** The clinician header. The clinic being worked in is always named here; a user who belongs to
 *  more than one clinic gets a switcher in its place, so nobody replies to a patient or discharges
 *  one in the wrong clinic without it being on screen. */
export function AppHeader({ clinician }: { clinician: ReadyClinician }) {
  const router = useRouter();
  const { clinic, clinics, email, switchClinic } = clinician;
  const labels = clinicLabels(clinics);
  async function signOut() {
    await supabase.auth.signOut();
    router.replace("/login");
  }
  return (
    <header className="border-b border-border bg-surface">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          {clinics.length > 1 ? (
            <label className="block">
              <span className="sr-only">Clinic</span>
              <select
                value={clinic.id}
                onChange={(e) => {
                  if (e.target.value !== clinic.id) switchClinic(e.target.value);
                }}
                className="max-w-full truncate rounded-md border border-border-strong bg-surface py-1 pl-2 pr-7 text-sm font-semibold text-ink"
              >
                {clinics.map((c) => (
                  <option key={c.id} value={c.id}>
                    {labels[c.id]}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <Link href="/dashboard" className="block truncate text-sm font-semibold text-ink">
              {clinic.name}
            </Link>
          )}
          <Link href="/dashboard" className="block truncate text-xs text-muted">
            Clinically Anchored{clinics.length > 1 ? ` · ${clinics.length} clinics` : ""}
          </Link>
        </div>
        <div className="flex items-center gap-3">
          {clinic.is_demo && clinic.role === "owner" && (
            <Link href="/dashboard/demo-inbox" className="text-sm text-link underline hover:text-link-hover">
              Demo inbox
            </Link>
          )}
          <AccountMenu email={email} onSignOut={signOut} />
        </div>
      </div>
    </header>
  );
}

/** The clinician's account menu: who is signed in, Change password, Sign out. Closes on Escape, on
 *  a click elsewhere and when a choice is made. */
function AccountMenu({ email, onSignOut }: { email: string | null; onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const item = "block w-full px-3 py-2 text-left text-sm text-ink hover:bg-primary-tint";
  return (
    <div ref={box} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="rounded-md border border-link px-3 py-1.5 text-sm text-link hover:border-link-hover hover:text-link-hover hover:bg-primary-tint"
      >
        Account
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-10 mt-1 w-56 rounded-md border border-border bg-surface py-1 shadow-md"
        >
          {email && <p className="truncate px-3 py-2 text-xs text-muted">{email}</p>}
          <Link
            role="menuitem"
            href="/dashboard/account/password"
            onClick={() => setOpen(false)}
            className={item}
          >
            Change password
          </Link>
          <button role="menuitem" type="button" onClick={onSignOut} className={item}>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
