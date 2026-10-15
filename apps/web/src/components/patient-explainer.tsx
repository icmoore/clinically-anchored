"use client";

import { useState, useSyncExternalStore } from "react";
import { PATIENT_TEXT } from "@/lib/patient-strings";

const T = PATIENT_TEXT.explainer;
// Per browser, not per link: it says nothing about the patient, and the key holds no token.
const KEY = "ca.patient.explainer-seen";

type Stored = "seen" | "new" | "unknown";

/** What this browser remembers. Storage can be blocked or throw: then it is simply "new". */
function readStored(): Stored {
  try {
    return window.localStorage.getItem(KEY) === "1" ? "seen" : "new";
  } catch {
    return "new";
  }
}
const subscribe = (notify: () => void) => {
  window.addEventListener("storage", notify);
  return () => window.removeEventListener("storage", notify);
};

/** The first time a patient opens their link: "How this works", three steps and a "Got it".
 *  Remembered in this browser only; if storage is blocked or fails, it simply shows again next
 *  time. A small link brings it back. */
export function PatientExplainer() {
  // "unknown" until the browser has been asked, so a returning patient never sees it flash.
  const stored = useSyncExternalStore<Stored>(subscribe, readStored, () => "unknown");
  const [dismissed, setDismissed] = useState(false);
  const [reopened, setReopened] = useState(false);
  const seen = dismissed || stored === "seen";

  function dismiss() {
    try {
      window.localStorage.setItem(KEY, "1");
    } catch {
      // Not remembered: it will show again next time.
    }
    setDismissed(true);
    setReopened(false);
  }

  if (stored === "unknown") return null;
  if (seen && !reopened) {
    return (
      <p className="mb-3 text-xs">
        <button type="button" onClick={() => setReopened(true)} className="text-muted underline">
          {T.reopen}
        </button>
      </p>
    );
  }
  return (
    <section
      aria-labelledby="how-this-works"
      className="mb-4 rounded-lg border border-info-border bg-info-bg p-4 text-info-text"
    >
      <h2 id="how-this-works" className="font-medium">
        {T.title}
      </h2>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
        {T.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <button
        type="button"
        onClick={dismiss}
        className="mt-3 rounded-md border border-info-border bg-surface px-4 py-2 text-sm font-medium text-ink"
      >
        {T.gotIt}
      </button>
    </section>
  );
}
