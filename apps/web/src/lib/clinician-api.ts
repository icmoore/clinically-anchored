// Calls to apps/api that need a signed-in clinician: the Supabase access token
// is sent as a bearer token and the api checks clinic membership.
import { Procedure, request } from "./api";
import { supabase } from "./supabase";

export interface Clinic {
  id: string;
  name: string;
  role: string;
  is_demo?: boolean; // a demo clinic sends no real email; its owners have a demo inbox
}
export interface Me {
  user_id: string;
  email: string | null;
  clinics: Clinic[];
}
export interface QueueItem {
  patient_id: string;
  patient_name: string | null;
  has_red_flag: boolean;
  unreviewed_check_ins: number;
  unread_messages: number;
  latest_check_in_id: string | null;
  latest_post_op_day: number | null; // today's, from the surgery date (negative before it)
  unread_categories: string[]; // category keys of the unread scheduled messages
  last_activity_at: string;
  extension_requested?: boolean; // a follow-up extension request is waiting for a decision
}

/** Where an episode stands now, computed by the api from the schedule and the clock. Times are
 *  UTC instants: show them in the episode's `timezone`. */
export type EpisodePhase = "locked_checkin" | "waiting" | "open" | "used" | "closed";
/** One message window on the timeline. `missed_reason` is set only for a missed window. */
export type WindowStatus = "used" | "missed" | "open" | "locked" | "upcoming" | "cut_short";
export type MissedReason = "closed_before_check_in" | "no_message";
export interface WindowState {
  number: number;
  opens_at: string;
  closes_at: string;
  status: WindowStatus;
  missed_reason: MissedReason | null;
  extension_no?: number; // 0 / absent: the main care period; n: the n-th follow-up extension
}
export interface EpisodeState {
  phase: EpisodePhase;
  post_op_day: number;
  intervals_total: number;
  interval_no: number | null;
  interval_opens_at: string | null;
  interval_closes_at: string | null;
  next_opens_at: string | null;
  episode_closes_at: string;
  closed_at: string | null;
  close_reason: "discharged" | "schedule_complete" | null;
  missed: number[]; // intervals that ended without a message
  reply_available: boolean; // the patient may send one reply to the clinic's latest message
  can_send_scheduled: boolean;
  can_reply: boolean;
  windows?: WindowState[]; // every window in order; absent only from an older api
}
/** A patient's request for more follow-up, after the care period ended by schedule. */
export interface ExtensionRequest {
  id: string;
  episode_id: string; // the ended episode it is about
  status: "pending" | "accepted" | "declined";
  reason: string | null; // the patient's words
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_message: string | null; // the clinician's words, if they wrote any
  extension_episode_id: string | null;
}
/** Follow-up extensions around an episode. `number` 0 = the main care period, n = the n-th
 *  extension; `earlier_windows` are the windows of the episodes this one continues. */
export interface EpisodeExtension {
  number: number;
  extends_episode_id: string | null;
  started_at: string | null;
  earlier_windows: WindowState[];
  request: ExtensionRequest | null; // the request about THIS episode
  request_by: string | null; // last moment the patient may still ask
  can_request: boolean;
}
/** One post-op period: the surgery date and procedure the clinic entered, and the schedule. */
export interface Episode {
  id: string;
  patient_id: string;
  procedure_id: string;
  procedure_name: string | null;
  surgery_date: string; // YYYY-MM-DD
  schedule_version: number;
  timezone: string;
  checkin_completed_at: string | null;
  closed_at: string | null;
  close_reason: "discharged" | "schedule_complete" | null;
  created_at: string;
  state: EpisodeState;
  extension?: EpisodeExtension; // absent from an older api
}
export interface Patient {
  id: string;
  full_name: string;
  contact: string | null;
  email: string | null; // where the sign-in invite and codes go
  // The sign-in account's state; absent from an older api. Only "locked" is shown (a tag).
  account_status?: "none" | "invited" | "active" | "locked" | "off" | null;
  created_at: string;
  episode: Episode | null; // the latest
}
export interface CheckInDetail {
  id: string;
  patient_id: string;
  episode_id: string | null; // the care period it belongs to; null from an older api
  patient_name: string | null;
  procedure_name: string | null;
  post_op_day: number | null;
  answers: Record<string, unknown>;
  is_red_flag: boolean;
  created_at: string;
  reviewed_at: string | null;
}
export interface Message {
  id: string;
  episode_id: string;
  sender: "clinician" | "patient" | "system";
  body: string;
  // extension_request / extension_decision are thread entries about a follow-up extension, not
  // messages: they use no window and grant no reply.
  origin: "scheduled" | "reply" | "clinician" | "extension_request" | "extension_decision";
  extension_no?: number; // n > 0: it used a window of the n-th follow-up extension
  interval_no: number | null; // the message window a scheduled message used
  category: string | null; // a key; see lib/categories.ts for the label
  reply_to_message_id: string | null;
  read_at: string | null;
  created_at: string;
}

export type DraftStatus = "pending" | "approved" | "edited" | "rejected";
/** An AI-written reply to a patient message. Nothing is sent until a clinician decides. */
export interface Draft {
  id: string;
  patient_id: string;
  source_message_id: string; // the patient message this answers
  draft_text: string; // exactly what the model wrote; never changes
  model_id: string;
  prompt_version: string;
  status: DraftStatus;
  final_text: string | null; // what was actually sent (approved / edited)
  sent_message_id: string | null;
  created_at: string;
  decided_at: string | null;
}
export interface DraftDecision {
  draft: Draft;
  message: Message | null; // the message that was sent; null when rejected
}
export interface SummaryLine {
  text: string;
  citations: string[]; // ids of messages in this patient's thread; verified by the api
}
export interface Summary {
  summary_id: string;
  patient_id: string;
  generated_at: string;
  model_id: string;
  prompt_version: string;
  lines: SummaryLine[];
  covers_messages: number;
  truncated: boolean; // older messages were left out
}

async function authed<T>(path: string, init?: RequestInit): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return request<T>(path, {
    ...init,
    headers: { ...init?.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
}

const post = <T>(path: string, body?: unknown) =>
  authed<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const getMe = (signal?: AbortSignal) => authed<Me>("/me", { signal });
export const getQueue = (clinic: string) => authed<QueueItem[]>(`/clinics/${clinic}/queue`);
export const listPatients = (clinic: string) => authed<Patient[]>(`/clinics/${clinic}/patients`);
export const createPatient = (
  clinic: string,
  body: {
    full_name: string;
    contact: string | null;
    email: string | null;
    surgery_date: string;
    procedure_id: string;
  },
) => post<Patient>(`/clinics/${clinic}/patients`, body);
export const listProcedures = (clinic: string) => request<Procedure[]>(`/clinics/${clinic}/procedures`);

// Episodes: the surgery date and procedure are the clinic's; discharge is owner/clinician-only at
// the api.
export const getEpisode = (clinic: string, patient: string) =>
  authed<Episode>(`/clinics/${clinic}/patients/${patient}/episode`);
export const correctEpisode = (
  clinic: string,
  episode: string,
  body: { surgery_date?: string; procedure_id?: string },
) => authed<Episode>(`/clinics/${clinic}/episodes/${episode}`, { method: "PATCH", body: JSON.stringify(body) });
export const dischargeEpisode = (clinic: string, episode: string) =>
  post<Episode>(`/clinics/${clinic}/episodes/${episode}/close`);
// Follow-up extensions: owner/clinician only at the api. The message to the patient is optional.
export const acceptExtension = (clinic: string, id: string, message: string | null) =>
  post<ExtensionRequest>(`/clinics/${clinic}/extension-requests/${id}/accept`, { message });
export const declineExtension = (clinic: string, id: string, message: string | null) =>
  post<ExtensionRequest>(`/clinics/${clinic}/extension-requests/${id}/decline`, { message });
export const listCheckIns = (clinic: string, patient: string) =>
  authed<CheckInDetail[]>(`/clinics/${clinic}/check-ins?patient_id=${patient}&limit=50`);
export const reviewCheckIn = (clinic: string, id: string) =>
  post<CheckInDetail>(`/clinics/${clinic}/check-ins/${id}/review`);
export const listThread = (clinic: string, patient: string) =>
  authed<Message[]>(`/clinics/${clinic}/patients/${patient}/messages`);
export const sendMessage = (clinic: string, patient: string, body: string) =>
  post<Message>(`/clinics/${clinic}/patients/${patient}/messages`, { body });
export const markRead = (clinic: string, id: string) =>
  post<Message>(`/clinics/${clinic}/messages/${id}/read`);

// AI drafts: generate stores a pending draft and sends nothing; only approve / edit
// send a message (reject sends nothing). Only owners and clinicians may decide.
export const listDrafts = (clinic: string, patient: string) =>
  authed<Draft[]>(`/clinics/${clinic}/patients/${patient}/drafts`);
export const generateDraft = (clinic: string, patient: string) =>
  post<Draft>(`/clinics/${clinic}/patients/${patient}/drafts`);
export const approveDraft = (clinic: string, id: string) =>
  post<DraftDecision>(`/clinics/${clinic}/drafts/${id}/approve`);
export const editDraft = (clinic: string, id: string, finalText: string) =>
  post<DraftDecision>(`/clinics/${clinic}/drafts/${id}/edit`, { final_text: finalText });
export const rejectDraft = (clinic: string, id: string) =>
  post<DraftDecision>(`/clinics/${clinic}/drafts/${id}/reject`);

// On-demand summary: each call to generateSummary calls the model (and costs a call from the daily
// cap). The api saves what it generates; getLatestSummary reads the newest saved one back for free
// (null if there is none), so opening a patient shows the last summary without asking again.
export const generateSummary = (clinic: string, patient: string) =>
  post<Summary>(`/clinics/${clinic}/patients/${patient}/summaries`);
export const getLatestSummary = (clinic: string, patient: string) =>
  authed<Summary | null>(`/clinics/${clinic}/patients/${patient}/summaries/latest`);

// AI patient summary ("brief"): three labelled bullets, each citing the messages (M), check-ins (C)
// and calls/visits (T) it came from. Like the interaction summary, generateBrief calls the model and
// the api saves what it generates; getLatestBrief reads the newest saved one back for free (null if
// none), with counts of what has arrived since.
export type BriefSourceKind = "message" | "check_in" | "touchpoint";
export interface BriefSource {
  kind: BriefSourceKind;
  id: string; // verified by the api to be this clinic's record for this patient
}
export interface BriefBullet {
  label: "The picture now" | "Trends" | "Loose ends";
  text: string;
  sources: BriefSource[];
}
export interface BriefCounts {
  messages: number;
  check_ins: number;
  touchpoints: number; // calls, visits and emails
}
export interface PatientBrief {
  brief_id: string;
  patient_id: string;
  episode_id: string;
  generated_at: string;
  model_id: string;
  prompt_version: string;
  bullets: BriefBullet[];
  covers: BriefCounts; // what the model was shown
  truncated: boolean;
  new_since: BriefCounts; // added after it was generated
}
export const generateBrief = (clinic: string, patient: string) =>
  post<PatientBrief>(`/clinics/${clinic}/patients/${patient}/patient-brief`);
export const getLatestBrief = (clinic: string, patient: string) =>
  authed<PatientBrief | null>(`/clinics/${clinic}/patients/${patient}/patient-brief/latest`);

// AI care provider summary: one plain paragraph (no citations) the physician can pass on to the
// patient's other care provider. Same pattern as the AI patient summary: generateCareProviderSummary
// calls the model and the api saves what it generates; getLatestCareProviderSummary reads the newest
// saved one back for free (null if none), with counts of what has arrived since.
export interface CareProviderSummary {
  summary_id: string;
  patient_id: string;
  episode_id: string;
  generated_at: string;
  model_id: string;
  prompt_version: string;
  text: string;
  covers: BriefCounts; // what the model was shown
  truncated: boolean;
  new_since: BriefCounts; // added after it was generated
  // The physician's newest edit of `text`, if any. It is what is shown and copied; `text` stays
  // exactly what the AI wrote.
  edited_text: string | null;
  edited_at: string | null;
}
export const generateCareProviderSummary = (clinic: string, patient: string) =>
  post<CareProviderSummary>(`/clinics/${clinic}/patients/${patient}/care-provider-summary`);
// Saves the edit as a new record beside the AI's text (the api never overwrites the AI's text).
export const editCareProviderSummary = (
  clinic: string,
  patient: string,
  summaryId: string,
  text: string,
) =>
  post<CareProviderSummary>(
    `/clinics/${clinic}/patients/${patient}/care-provider-summary/${summaryId}/edit`,
    { text },
  );
export const getLatestCareProviderSummary = (clinic: string, patient: string) =>
  authed<CareProviderSummary | null>(
    `/clinics/${clinic}/patients/${patient}/care-provider-summary/latest`,
  );

// Contact/time log. Manual entries are calls, visits and emails; `message` rows are
// written by the api itself whenever a clinician message is sent (never by this UI).
export type TouchpointKind = "call" | "visit" | "email" | "message";
export type ManualTouchpointKind = Exclude<TouchpointKind, "message">;
export interface Touchpoint {
  id: string;
  patient_id: string;
  kind: TouchpointKind;
  source: "auto" | "manual";
  occurred_at: string;
  duration_minutes: number | null;
  note: string | null;
  logged_by: string | null; // null for auto-logged rows
  created_at: string;
}
export const listTouchpoints = (clinic: string, patient: string) =>
  authed<Touchpoint[]>(`/clinics/${clinic}/patients/${patient}/touchpoints`);
export const logTouchpoint = (
  clinic: string,
  patient: string,
  body: { kind: ManualTouchpointKind; duration_minutes?: number; note?: string },
) => post<Touchpoint>(`/clinics/${clinic}/patients/${patient}/touchpoints`, body);

// Patient accounts: the patient signs in with their email and a code (docs/api/patient-accounts.md).
// Any member can invite, resend and set the email; turning access off, re-inviting and a fallback
// link are owner/clinician only at the api.
export type AccountStatus = "none" | "invited" | "active" | "locked" | "off";
/** The outbox status of the latest invite email: sent, failed, demo (a demo clinic sends none),
 *  refused (a test address), queued. */
export interface InviteEmail {
  status: "sent" | "failed" | "demo" | "refused" | "queued";
  at: string;
}
export interface FallbackLinkInfo {
  issued_at: string;
  expires_at: string;
  used_at: string | null;
  revoked_at: string | null;
}
export interface Account {
  status: AccountStatus;
  email: string | null;
  invited_at: string | null;
  activated_at: string | null;
  turned_off_at: string | null;
  locked_at: string | null;
  last_sign_in_at: string | null;
  last_invite_email: InviteEmail | null;
  fallback_link: FallbackLinkInfo | null;
}
/** A single-use sign-in link. The url is shown once: only a hash of its secret is kept. */
export interface FallbackLink {
  url: string;
  expires_at: string;
}
const accountPath = (clinic: string, patient: string) =>
  `/clinics/${clinic}/patients/${patient}/account`;
export const getAccount = (clinic: string, patient: string) =>
  authed<Account>(accountPath(clinic, patient));
export const inviteAccount = (clinic: string, patient: string) =>
  post<Account>(`${accountPath(clinic, patient)}/invite`);
export const resendInvite = (clinic: string, patient: string) =>
  post<Account>(`${accountPath(clinic, patient)}/resend`);
export const turnOffAccess = (clinic: string, patient: string) =>
  post<Account>(`${accountPath(clinic, patient)}/turn-off`);
export const reinviteAccount = (clinic: string, patient: string) =>
  post<Account>(`${accountPath(clinic, patient)}/reinvite`);
export const createFallbackLink = (clinic: string, patient: string) =>
  post<FallbackLink>(`${accountPath(clinic, patient)}/fallback-link`);
export const setPatientEmail = (clinic: string, patient: string, email: string | null) =>
  authed<Account>(`/clinics/${clinic}/patients/${patient}/email`, {
    method: "PUT",
    body: JSON.stringify({ email }),
  });

/** An email a demo clinic did not send (owner-only demo inbox). */
export interface DemoEmail {
  id: string;
  recipient: string;
  template: "invite" | "sign_in_code";
  patient_id: string | null;
  subject: string;
  text: string;
  html: string;
  created_at: string;
}
export const listDemoInbox = (clinic: string) =>
  authed<DemoEmail[]>(`/clinics/${clinic}/demo-inbox`);
