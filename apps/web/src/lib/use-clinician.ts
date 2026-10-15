"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError } from "./api";
import { Clinic, getMe } from "./clinician-api";
import { chooseClinic, rememberClinic } from "./clinic-choice";
import { supabase } from "./supabase";

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
  | { status: "error"; message: string }
  | ReadyClinician;

/** Requires a signed-in clinician (else redirects to /login) and resolves the clinic to work in:
 *  the one remembered for this user in this browser if they still belong to it, else the first. */
export function useClinician(): ClinicianState {
  const router = useRouter();
  const [state, setState] = useState<ClinicianState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data } = await supabase.auth.getSession();
      if (!data.session) {
        router.replace("/login");
        return;
      }
      try {
        const me = await getMe();
        if (cancelled) return;
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
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          await supabase.auth.signOut();
          router.replace("/login");
          return;
        }
        setState({ status: "error", message: "Couldn't reach the server. Try again shortly." });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return state;
}
