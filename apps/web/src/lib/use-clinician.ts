"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { Clinic, getMe } from "./clinician-api";
import { chooseClinic, rememberClinic } from "./clinic-choice";
import { supabase } from "./supabase";

/** How long loading may take before the page stops saying "Loading..." and offers a retry. */
export const LOAD_TIMEOUT_MS = 15_000;
export const STALLED_MESSAGE = "Couldn't load. Check your connection.";

export type ReadyClinician = {
  status: "ready";
  email: string | null;
  clinic: Clinic; // the clinic being worked in
  clinics: Clinic[]; // every clinic the user belongs to
  /** Work in another clinic: remembers the choice and reloads the dashboard, so the queue is
   *  that clinic's and no patient from the old one stays open. */
  switchClinic: (clinicId: string) => void;
};

export type ClinicianState =
  | { status: "loading" }
  // `retry` is set when loading stalled: it starts over (see useClinician).
  | { status: "error"; message: string; retry?: () => void }
  | ReadyClinician;

/** Requires a signed-in clinician (else redirects to /login) and resolves the clinic to work in:
 *  the one remembered for this user in this browser if they still belong to it, else the first.
 *
 *  If that has not finished after LOAD_TIMEOUT_MS it stops waiting: the state becomes an error with
 *  `retry`. The stalled request is aborted, and whatever the stalled attempt still delivers later
 *  is ignored, so a retry never has a second attempt racing it. */
export function useClinician(): ClinicianState {
  const router = useRouter();
  const [state, setState] = useState<ClinicianState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    // Everything this attempt does is checked against `over`: true once it was cancelled (retry,
    // unmount) or timed out. The abort signal stops the request itself; `over` also covers the
    // session lookup, which cannot be aborted.
    let over = false;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      over = true;
      controller.abort();
      setState({ status: "error", message: STALLED_MESSAGE, retry });
    }, LOAD_TIMEOUT_MS);

    (async () => {
      const { data } = await supabase.auth.getSession();
      if (over) return;
      if (!data.session) {
        window.clearTimeout(timer);
        router.replace("/login");
        return;
      }
      try {
        const me = await getMe(controller.signal);
        if (over) return;
        window.clearTimeout(timer);
        if (me.clinics.length === 0) {
          setState({ status: "error", message: "Your account isn't linked to a clinic yet." });
        } else {
          setState({
            status: "ready",
            email: me.email,
            clinic: chooseClinic(me.clinics, me.user_id),
            clinics: me.clinics,
            switchClinic: (clinicId) => {
              rememberClinic(me.user_id, clinicId);
              // A full page load on purpose (hence not router.push): nothing from the previous
              // clinic (queue, open patient, half-typed message) can survive into the new one.
              window.location.assign(new URL("/dashboard", window.location.origin).href);
            },
          });
        }
      } catch (err) {
        if (over) return;
        window.clearTimeout(timer);
        if (err instanceof ApiError && err.status === 401) {
          await supabase.auth.signOut();
          router.replace("/login");
          return;
        }
        setState({ status: "error", message: "Couldn't reach the server. Try again shortly." });
      }
    })();

    return () => {
      over = true;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [router, attempt, retry]);

  return state;
}
