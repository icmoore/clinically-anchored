import { createClient } from "@supabase/supabase-js";
import { useEffect, useState } from "react";

// The patient's own sign-in, kept apart from the clinician's. It is a second Supabase client with
// its own storage key, so signing in as a patient never replaces a clinician's session in the same
// browser (and signing out of one never signs out the other). Like lib/supabase.ts it is used only
// to hold a session: all data goes through apps/api, with this access token as a bearer token.
//
// The sign-in itself (email, code) is done by the api (api/patient_auth.py), which hands back a
// session; `savePatientSession` stores it here, and supabase-js then keeps it fresh.
export const PATIENT_STORAGE_KEY = "ca-patient-auth";

const patientAuth = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL || "http://localhost:54321",
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "missing-anon-key",
  {
    auth: {
      storageKey: PATIENT_STORAGE_KEY,
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
    },
  },
).auth;

export interface IssuedSession {
  access_token: string;
  refresh_token: string;
}

/** The patient's access token, or null when nobody is signed in on this device. */
export async function getPatientAccessToken(): Promise<string | null> {
  const { data } = await patientAuth.getSession();
  return data.session?.access_token ?? null;
}

export async function savePatientSession(session: IssuedSession): Promise<void> {
  const { error } = await patientAuth.setSession(session);
  if (error) throw error;
}

/** Ends this device's patient session (the api deletes it too). Never touches a clinician's. */
export async function signOutPatient(): Promise<void> {
  try {
    await patientAuth.signOut({ scope: "local" });
  } catch {
    // The device is signed out locally either way.
  }
}

export type PatientSignIn = "checking" | "signed-in" | "signed-out";

/** Whether this device has a patient session. "checking" until storage has been read. */
export function usePatientSignIn(): PatientSignIn {
  const [state, setState] = useState<PatientSignIn>("checking");
  useEffect(() => {
    let cancelled = false;
    getPatientAccessToken()
      .then((token) => !cancelled && setState(token ? "signed-in" : "signed-out"))
      .catch(() => !cancelled && setState("signed-out"));
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}
