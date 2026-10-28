// Thin fetch wrapper for the api service. This is a hand-written stand-in
// for the OpenAPI-generated client mentioned in the repo README -- codegen
// isn't wired up yet, but nothing here should be called with anything
// other than the api's actual response shapes.

import { getPatientAccessToken } from "./patient-session";
import type { CheckInAnswers, ProcedureCategory } from "./symptoms";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  /** The whole error body, for the few answers that carry more than a message
   *  (`attempts_left` after a wrong surgery date, `locked`). */
  body: Record<string, unknown>;
  constructor(status: number, message: string, body: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : null;
    throw new ApiError(response.status, detail ?? `Request failed (${response.status})`, body);
  }
  return response.json() as Promise<T>;
}

/** A call as the signed-in patient (lib/patient-session.ts), sent as a bearer token. The retired
 *  per-episode links (`?token=`) are gone: the api ignores a token in the address. */
async function patientRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const access = await getPatientAccessToken();
  return request<T>(path, {
    ...init,
    headers: { ...init?.headers, ...(access ? { Authorization: `Bearer ${access}` } : {}) },
  });
}

export interface Procedure {
  id: string;
  name: string;
  category: ProcedureCategory;
}

/** What the check-in page renders from: the procedure the clinic chose for this episode. */
export interface CheckInContext {
  clinic_id: string;
  procedure: Procedure;
  post_op_day: number; // negative before the surgery date
  check_in_done: boolean;
}

export interface CheckInOut {
  id: string;
  clinic_id: string;
  patient_id: string;
  episode_id: string;
  procedure_id: string | null;
  post_op_day: number | null;
  answers: CheckInAnswers;
  is_red_flag: boolean;
  created_at: string;
}

export function getCheckInContext(): Promise<CheckInContext> {
  return patientRequest(`/check-ins/context`);
}

export function submitCheckIn(body: { answers: CheckInAnswers }): Promise<CheckInOut> {
  return patientRequest(`/check-ins`, { method: "POST", body: JSON.stringify(body) });
}

// --- The signed-in patient's care period ------------------------------------------------

export type PatientPhase = "locked_checkin" | "waiting" | "open" | "used" | "closed";

/** One message period as the patient sees it. `before_check_in` is a period that ended before they
 *  did their check-in; `passed` ended unused. Neither is ever shown as a failure. */
export type PatientWindowStatus = "used" | "open" | "upcoming" | "passed" | "before_check_in";
export interface PatientWindow {
  number: number;
  status: PatientWindowStatus;
  opens_at: string;
  closes_at: string;
}

/** Where the patient stands right now, worked out by the api. A closed episode answers
 *  only `phase` and `office_phone`. Times are UTC instants; show them in `timezone`. */
export interface PatientState {
  phase: PatientPhase;
  office_phone: string | null;
  check_in_done: boolean;
  categories: string[]; // keys the patient may pick from, in order
  post_op_day: number | null; // negative before the surgery date
  interval_no: number | null;
  intervals_total: number | null;
  interval_closes_at: string | null;
  next_opens_at: string | null;
  episode_closes_at: string | null;
  timezone: string | null;
  can_send_scheduled: boolean;
  can_reply: boolean; // the care team's latest message has no reply yet
  windows?: PatientWindow[]; // every period in order; empty once closed, absent from an older api
  ended_at?: string | null; // set while a closed episode is in its limited "ended" mode
  extension?: PatientExtension | null; // follow-up extensions; absent from an older api
}

/** Where the patient stands on asking for more follow-up (api/extensions.py). `eligible`: they may
 *  ask until `request_by`. `pending`: asked, not decided. `declined`: final. `accepted`: an
 *  extension is running (`extension_no` says which). */
export interface PatientExtension {
  standing: "eligible" | "pending" | "declined" | "accepted";
  request_by: string | null;
  reason_max: number;
  requested_at: string | null;
  decision_message: string | null; // the clinician's own words, if they wrote any
  extension_no: number;
}

export interface PatientMessage {
  id: string;
  sender: "clinician" | "patient" | "system";
  body: string;
  origin: "scheduled" | "reply" | "clinician" | "extension_request" | "extension_decision";
  interval_no: number | null;
  category: string | null;
  created_at: string;
}

/** What the patient entered at their check-in, read back to them (null until they have). */
export interface MyCheckIn {
  submitted_at: string;
  post_op_day: number | null;
  procedure_name: string | null;
  answers: CheckInAnswers;
}
export const getMyCheckIn = () => patientRequest<MyCheckIn | null>(`/check-ins/mine`);

export const requestExtension = (reason: string) =>
  patientRequest<PatientExtension>(`/extension-request`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });

export const getPatientState = () => patientRequest<PatientState>(`/episode`);
export const getPatientThread = () => patientRequest<PatientMessage[]>(`/messages`);
export const sendPatientMessage = (
  body: { body: string; kind: "scheduled" | "reply"; category?: string },
) => patientRequest<PatientMessage>(`/messages`, { method: "POST", body: JSON.stringify(body) });

// --- Patient accounts: sign in with an email and a code (api/patient_auth.py) --------------------

/** A session the api issued after a good code or fallback link; give it to savePatientSession. */
export interface IssuedPatientSession {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  expires_at: number | null;
}

/** Asks for a code. The answer is the same for every address: it never says whether the email
 *  has an account. */
export const requestSignInCode = (email: string) =>
  request<{ ok: true }>("/patient-auth/request-code", {
    method: "POST",
    body: JSON.stringify({ email }),
  });

export const verifySignInCode = (email: string, code: string) =>
  request<IssuedPatientSession>("/patient-auth/verify", {
    method: "POST",
    body: JSON.stringify({ email, code }),
  });

/** The single-use link the clinic gives a patient with no email. It asks for the surgery date
 *  every time. A wrong date comes back as an ApiError with `body.attempts_left`; the account being
 *  locked as one with `body.locked`. */
export const redeemFallbackLink = (token: string, surgeryDate: string) =>
  request<IssuedPatientSession>("/patient-auth/link", {
    method: "POST",
    body: JSON.stringify({ token, surgery_date: surgeryDate }),
  });

/** Where the signed-in patient's account stands: what is left of the first sign-in. */
export interface PatientAccount {
  status: "invited" | "active" | "locked" | "off";
  needs_identity: boolean; // still to enter the surgery date
  needs_consent: boolean; // still to agree
  identity_attempts_left: number;
  consent_version: string; // the wording version the api expects back
  required_consents: string[];
  clinic_name: string;
  office_phone: string | null;
}

export const getPatientAccount = () => patientRequest<PatientAccount>("/patient/account");
export const confirmSurgeryDate = (surgeryDate: string) =>
  patientRequest<PatientAccount>("/patient/identity", {
    method: "POST",
    body: JSON.stringify({ surgery_date: surgeryDate }),
  });
export const acceptConsents = (consentTextVersion: string, consents: string[]) =>
  patientRequest<PatientAccount>("/patient/consent", {
    method: "POST",
    body: JSON.stringify({ consent_text_version: consentTextVersion, consents }),
  });
