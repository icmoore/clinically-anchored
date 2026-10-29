"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, PatientAccount, getPatientAccount } from "@/lib/api";
import { signOutPatient, usePatientSignIn } from "@/lib/patient-session";
import {
  ConsentScreen,
  IdentityScreen,
  LockedScreen,
  TurnedOffScreen,
} from "./patient-first-sign-in";
import { SignOutContext } from "./patient-frame";

/** What a patient page may ask of the gate around it. */
export interface GateControls {
  /** Look at the account again: a request was refused (signed out elsewhere, access turned off,
   *  locked) and the gate decides where the patient belongs now. */
  recheck: () => void;
}

/** Wraps every page a signed-in patient uses. No session on this device: to /sign-in. A session:
 *  where the account stands decides what shows -- the surgery-date check, then consent, then the
 *  page itself (`children`); a locked or turned-off account gets a plain "call the office" screen.
 *  Every one of those screens has Sign out. */
export function SignedInPatient({ children }: { children: (gate: GateControls) => ReactNode }) {
  const router = useRouter();
  const signIn = usePatientSignIn();
  const [account, setAccount] = useState<PatientAccount | null>(null);
  const [problem, setProblem] = useState(false);
  const [nonce, setNonce] = useState(0);

  const leave = useCallback(async () => {
    await signOutPatient();
    router.replace("/sign-in");
  }, [router]);

  useEffect(() => {
    if (signIn === "signed-out") router.replace("/sign-in");
  }, [signIn, router]);

  useEffect(() => {
    if (signIn !== "signed-in") return;
    let cancelled = false;
    getPatientAccount()
      .then((a) => {
        if (cancelled) return;
        setAccount(a);
        setProblem(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Not signed in any more (ended, or older than the limit): back to the sign-in page.
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) void leave();
        else setProblem(true);
      });
    return () => {
      cancelled = true;
    };
  }, [signIn, nonce, leave]);

  const recheck = useCallback(() => setNonce((n) => n + 1), []);

  let body: ReactNode;
  if (signIn !== "signed-in" || (account === null && !problem)) {
    body = <p className="p-6 text-muted">Loading...</p>;
  } else if (account === null) {
    body = (
      <div className="p-6">
        <p role="alert" className="text-flag-text">
          Couldn&apos;t load. Check your connection.
        </p>
        <button
          onClick={recheck}
          className="mt-3 rounded-md border border-link bg-surface px-4 py-2 text-sm font-medium text-link hover:border-link-hover hover:text-link-hover hover:bg-primary-tint"
        >
          Try again
        </button>
      </div>
    );
  } else if (account.status === "locked") {
    body = <LockedScreen phone={account.office_phone} />;
  } else if (account.status === "off") {
    body = <TurnedOffScreen phone={account.office_phone} />;
  } else if (account.status === "invited" && account.needs_identity) {
    body = <IdentityScreen account={account} onAccount={setAccount} />;
  } else if (account.status === "invited") {
    body = <ConsentScreen account={account} onAccount={setAccount} />;
  } else {
    body = children({ recheck });
  }

  return <SignOutContext.Provider value={leave}>{body}</SignOutContext.Provider>;
}
