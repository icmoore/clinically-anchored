// Thin fetch wrapper for the api service. This is a hand-written stand-in
// for the OpenAPI-generated client mentioned in the repo README -- codegen
// isn't wired up yet, but nothing here should be called with anything
// other than the api's actual response shapes.

import type { CheckInAnswers, ProcedureCategory } from "./symptoms";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
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

const q = (token: string) => `?token=${encodeURIComponent(token)}`;

export function getCheckInContext(token: string): Promise<CheckInContext> {
  return request(`/check-ins/context${q(token)}`);
}

export function submitCheckIn(token: string, body: { answers: CheckInAnswers }): Promise<CheckInOut> {
  return request(`/check-ins${q(token)}`, { method: "POST", body: JSON.stringify(body) });
}

// --- The patient's episode link (one link for the whole post-op period) -------------------

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
export const getMyCheckIn = (token: string) => request<MyCheckIn | null>(`/check-ins/mine${q(token)}`);

export const requestExtension = (token: string, reason: string) =>
  request<PatientExtension>(`/extension-request${q(token)}`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });

export const getPatientState = (token: string) => request<PatientState>(`/episode${q(token)}`);
export const getPatientThread = (token: string) => request<PatientMessage[]>(`/messages${q(token)}`);
export const sendPatientMessage = (
  token: string,
  body: { body: string; kind: "scheduled" | "reply"; category?: string },
) => request<PatientMessage>(`/messages${q(token)}`, { method: "POST", body: JSON.stringify(body) });
