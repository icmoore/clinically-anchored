"use client";

import { useState } from "react";
import { StatusBadge } from "@/components/status-badge";

export const LOCKED_TIP =
  "Locked after 3 wrong surgery dates. Re-invite the patient from Patient account.";

/** "Account locked": shown for a patient whose sign-in the surgery-date check locked. Hover shows
 *  the reason (the title), and a tap or click shows it as a line under the tag, since a phone has
 *  no hover. `relative z-10` keeps it clickable above a row's whole-row link. */
export function LockedTag() {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative z-10 inline-flex flex-col items-start gap-1">
      <button
        type="button"
        title={LOCKED_TIP}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="cursor-pointer rounded-full"
      >
        <StatusBadge variant="watch">Account locked</StatusBadge>
      </button>
      {open && <span className="text-xs text-watch-text">{LOCKED_TIP}</span>}
    </span>
  );
}
