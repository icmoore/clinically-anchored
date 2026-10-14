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
}

export interface PatientMessage {
  id: string;
  sender: "clinician" | "patient" | "system";
  body: string;
  origin: "scheduled" | "reply" | "clinician";
  interval_no: number | null;
  category: string | null;
  created_at: string;
}

export const getPatientState = (token: string) => request<PatientState>(`/episode${q(token)}`);
export const getPatientThread = (token: string) => request<PatientMessage[]>(`/messages${q(token)}`);
export const sendPatientMessage = (
  token: string,
  body: { body: string; kind: "scheduled" | "reply"; category?: string },
) => request<PatientMessage>(`/messages${q(token)}`, { method: "POST", body: JSON.stringify(body) });
