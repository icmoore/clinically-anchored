"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
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
            <Link href="/dashboard/demo-inbox" className="text-sm text-primary underline">
              Demo inbox
            </Link>
          )}
          {email && <span className="hidden truncate text-xs text-muted sm:block">{email}</span>}
          <button
            onClick={async () => {
              await supabase.auth.signOut();
              router.replace("/login");
            }}
            className="rounded-md border border-primary px-3 py-1.5 text-sm text-primary hover:bg-primary-tint"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
