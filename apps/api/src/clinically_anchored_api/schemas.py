"""Pydantic request/response models. Kept separate from the FastAPI routers
so the shapes are easy to scan in one place -- this file is effectively the
contract apps/web's generated OpenAPI client is built from."""

from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Procedure(BaseModel):
    id: str
    name: str
    # Routes the check-in form to the right symptom set and sub-questions;
    # see supabase/migrations/00000000000007_postop_checkin_v1.sql and
    # apps/web's lib/symptoms.ts for what each category actually asks.
    category: Literal["abdominal_general", "abdominal_bowel_resection", "anorectal", "other"]


class CheckInCreate(BaseModel):
    # The procedure is the clinic's choice (on the episode) and the post-op day is worked out
    # from the surgery date, so neither is asked of the patient any more.
    # Structured answers from the branching check-in form (decision 13).
    # Kept as a loose dict rather than a strict Pydantic model -- the shape
    # varies by procedure category and is still a DRAFT pending Sarah's
    # clinical review, so validation lives in the frontend form (lib/
    # symptoms.ts) and in core/rules.py, not here. Documented shape, all
    # keys optional and procedure-category-dependent:
    #
    #   symptoms: list[str]              -- which checkboxes were ticked
    #   surgery_type: "open" | "laparoscopic"           (abdominal only)
    #   wound_closure: "staples" | "absorbing_sutures" | "unsure"  (abdominal only)
    #   pain: {scale: int, trend, character, medications: list[str],
    #          medication_frequency: dict[str, str]}
    #   bleeding: {trend, timing, description, dressing_change_frequency,
    #              started_prescribed_medication: bool}   (anorectal only)
    #   fever: {took_temperature: bool, temperature_c: float, chills: bool}
    #   discharge: {trend, character, dressing_change_frequency}
    #   dizziness: {trend, orthostatic: bool, since}
    #   fatigue: {trend, interfering_with_activities: bool}
    #   constipation: {days_since_bm: int, passing_gas: bool, distension: bool}
    #   diarrhea: {frequency_per_day: int, blood_in_stool: bool, duration}
    #   nausea_vomiting: {trend, keeping_fluids_down: bool, vomiting_episodes_today: int}
    #   no_bowel_movement: {days: int, distension: bool, nausea_vomiting: bool}
    #       -- bowel-resection procedures only
    #   distension: {trend, associated_pain: bool}  -- bowel-resection procedures only
    #   concerns_text: str                -- patient's own words, capped client-side
    #
    # No derived "summary" field is stored -- apps/web's lib/symptoms.ts
    # buildSummary() renders the narrative view from this structured data
    # plus the already-resolved patient/procedure names, both on the
    # patient's pre-submit review and on the clinician's check-in card.
    answers: dict = Field(default_factory=dict)


class CheckInOut(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    episode_id: str
    procedure_id: str | None
    post_op_day: int | None
    answers: dict
    is_red_flag: bool
    created_at: str


class DevLinkRequest(BaseModel):
    episode_id: str


class DevLinkResponse(BaseModel):
    token: str


class CheckInContext(BaseModel):
    """What the check-in page needs to render its form, resolved from the link alone:
    the procedure the clinic chose for this episode (it picks the symptom set)."""

    clinic_id: str
    procedure: Procedure
    post_op_day: int  # negative before the surgery date
    check_in_done: bool


class PatientCheckInOut(BaseModel):
    """The patient's own check-in, shown back to them: what they entered, nothing the clinic
    derived from it (no red flag, no review state)."""

    submitted_at: str
    post_op_day: int | None
    procedure_name: str | None
    answers: dict


MessageText = Field(min_length=1, max_length=5000)


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("body must not be blank")
    return value


class ClinicianMessageCreate(BaseModel):
    # Messages are free-form text from both sides (decision 5); the cap is a
    # sanity limit, not a product rule.
    body: str = MessageText

    check_body = field_validator("body")(_not_blank)


class PatientMessageCreate(BaseModel):
    body: str = MessageText
    # "scheduled": the message for the interval that is open now (needs a category).
    # "reply": the one reply to the care team's latest message (no category).
    kind: Literal["scheduled", "reply"] = "scheduled"
    category: str | None = Field(default=None, max_length=64)

    check_body = field_validator("body")(_not_blank)


class MessageOut(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    episode_id: str
    sender: str
    body: str
    origin: Literal["scheduled", "reply", "clinician"]
    interval_no: int | None
    category: str | None
    reply_to_message_id: str | None
    read_at: str | None
    created_at: str


class WindowStateOut(BaseModel):
    """One message window on the clinic's timeline. `missed_reason` is set only when the
    window was missed: it closed before the check-in was done, or the patient was able to write
    and did not."""

    number: int
    opens_at: str
    closes_at: str
    status: Literal["used", "missed", "open", "locked", "upcoming", "cut_short"]
    missed_reason: Literal["closed_before_check_in", "no_message"] | None = None


class EpisodeStateOut(BaseModel):
    """Where an episode stands now, computed by core/schedule.py. Times are UTC instants;
    the web app shows them in the schedule's timezone."""

    phase: Literal["closed", "locked_checkin", "waiting", "open", "used"]
    post_op_day: int
    intervals_total: int
    interval_no: int | None
    interval_opens_at: str | None
    interval_closes_at: str | None
    next_opens_at: str | None
    episode_closes_at: str
    closed_at: str | None
    close_reason: Literal["discharged", "schedule_complete"] | None
    missed: list[int]
    reply_available: bool
    can_send_scheduled: bool
    can_reply: bool
    # Added after the first release; older clients ignore it, and `missed` above is unchanged.
    windows: list[WindowStateOut] = []


class EpisodeOut(BaseModel):
    id: str
    patient_id: str
    procedure_id: str
    procedure_name: str | None
    surgery_date: str
    schedule_version: int
    timezone: str
    checkin_completed_at: str | None
    closed_at: str | None
    close_reason: Literal["discharged", "schedule_complete"] | None
    created_at: str
    state: EpisodeStateOut


class NewEpisode(BaseModel):
    """A new surgery for a patient who is already in the system."""

    surgery_date: date
    procedure_id: str


class EpisodeCorrect(BaseModel):
    surgery_date: date | None = None
    procedure_id: str | None = None


class PatientWindowOut(BaseModel):
    """One message window as the PATIENT sees it: a number, a plain state and the two times.
    Deliberately less than the clinic's view: no reason a window was missed beyond "before your
    check-in", nothing about the schedule version, the check-in's review or any red flag."""

    number: int
    # used: they wrote in it. open: they can write now. upcoming: not yet (also a window running
    # by the clock before their check-in, which they cannot use yet). passed: it ended unused.
    # before_check_in: it ended before they had done their check-in (never "missed").
    status: Literal["used", "open", "upcoming", "passed", "before_check_in"]
    opens_at: str
    closes_at: str


class PatientStateOut(BaseModel):
    """What the patient's own screen needs. A closed episode answers `closed` and the office
    phone, nothing else."""

    phase: Literal["closed", "locked_checkin", "waiting", "open", "used"]
    office_phone: str | None
    check_in_done: bool = False
    categories: list[str] = []
    post_op_day: int | None = None
    interval_no: int | None = None
    intervals_total: int | None = None
    interval_closes_at: str | None = None
    next_opens_at: str | None = None
    episode_closes_at: str | None = None
    timezone: str | None = None
    can_send_scheduled: bool = False
    can_reply: bool = False
    # Every message window in order (added later; an older client ignores it). Empty once closed.
    windows: list[PatientWindowOut] = []


class AuditVerifyOut(BaseModel):
    ok: bool
    count: int
    head_hash: str | None
    broken_at: int | None
    reason: str | None
    key_ids: list[str]  # signing keys that appear in this clinic's chain
    public_key: str  # base64 Ed25519 public key of the currently active signing key


class CheckInDetail(BaseModel):
    """A check-in as a clinician sees it: answers plus resolved names."""

    id: str
    patient_id: str
    episode_id: str | None = None  # the care period it belongs to (one check-in per episode)
    patient_name: str | None
    procedure_id: str | None
    procedure_name: str | None
    post_op_day: int | None
    answers: dict
    is_red_flag: bool
    created_at: str
    reviewed_at: str | None
    reviewed_by: str | None


class QueueItem(BaseModel):
    """One patient who needs attention: unreviewed check-ins and/or unread
    patient messages. Ordered by the endpoint, unreviewed red flags first."""

    patient_id: str
    patient_name: str | None
    has_red_flag: bool
    unreviewed_check_ins: int
    unread_messages: int
    latest_check_in_id: str | None
    latest_post_op_day: int | None
    unread_categories: list[str]  # category keys of the unread scheduled messages
    last_activity_at: str


class ClinicOut(BaseModel):
    id: str
    name: str
    role: str


class MeOut(BaseModel):
    user_id: str
    email: str | None
    clinics: list[ClinicOut]


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    # Phone or email the patient's link is sent to (free text for now).
    contact: str | None = Field(default=None, max_length=200)
    # Entered by the clinic: every interval is measured from it.
    surgery_date: date
    procedure_id: str

    @field_validator("surgery_date")
    @classmethod
    def _not_far_in_the_future(cls, value: date) -> date:
        if value > datetime.now(UTC).date() + timedelta(days=366):
            raise ValueError("surgery_date is more than a year away")
        return value


class PatientOut(BaseModel):
    id: str
    full_name: str
    contact: str | None
    created_at: str
    episode: EpisodeOut | None = None  # the latest episode


class LinkOut(BaseModel):
    url: str
    generation: int


# Consent types are slugs and wording versions are opaque labels: which ones
# exist, and what they say, is decided outside this service (see
# core/config.py `consent_types`). The shapes here only keep them well-formed.
CONSENT_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class ConsentGrant(BaseModel):
    consent_type: str = Field(pattern=CONSENT_TYPE_PATTERN)
    # Identifies the exact wording the patient was shown, e.g. "messaging-v1".
    consent_text_version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")


class ConsentOut(BaseModel):
    id: str
    patient_id: str
    consent_type: str
    consent_text_version: str
    granted_at: str
    revoked_at: str | None


class ConsentSummaryOut(BaseModel):
    """A patient's consent state: which types are currently granted, plus the
    full history (every grant and withdrawal), newest first."""

    patient_id: str
    active: list[str]
    records: list[ConsentOut]


class DraftCreate(BaseModel):
    # The patient message to answer; defaults to the patient's latest message.
    message_id: str | None = None


class DraftEdit(BaseModel):
    # What the clinician actually wants sent, in place of the model's draft.
    final_text: str = Field(min_length=1, max_length=5000)

    @field_validator("final_text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("final_text must not be blank")
        return value


class DraftOut(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    source_message_id: str
    draft_text: str  # exactly what the model wrote; never changes
    model_id: str
    prompt_version: str
    status: Literal["pending", "approved", "edited", "rejected"]
    final_text: str | None  # what was sent; null until approved/edited
    sent_message_id: str | None
    created_by: str
    created_at: str
    decided_by: str | None  # the approving / rejecting clinician
    decided_at: str | None


class DraftDecisionOut(BaseModel):
    draft: DraftOut
    message: MessageOut | None  # the message that was sent; null when rejected


class SummaryLineOut(BaseModel):
    text: str
    citations: list[str]  # ids of the messages this line comes from; all verified


class SummaryOut(BaseModel):
    """An AI summary of a patient's thread. Only ever returned with every citation
    verified against the thread; not stored (the audit trail holds its hash)."""

    summary_id: str  # ties this response to its `summary.generated` audit event
    patient_id: str
    generated_at: str
    model_id: str
    prompt_version: str
    lines: list[SummaryLineOut]
    covers_messages: int  # how many of the thread's messages the model was shown
    truncated: bool  # true if older messages were left out


class BriefSourceOut(BaseModel):
    kind: Literal["message", "check_in", "touchpoint"]
    id: str  # a record of this clinic's, for this patient; verified before it is shown


class BriefBulletOut(BaseModel):
    label: Literal["The picture now", "Trends", "Loose ends"]
    text: str
    sources: list[BriefSourceOut]


class BriefCountsOut(BaseModel):
    messages: int = 0
    check_ins: int = 0
    touchpoints: int = 0  # calls, visits and emails in the contact log


class PatientBriefOut(BaseModel):
    """The AI patient summary: exactly three labelled bullets, every source verified. Saved
    (migration 13) after its `patient_brief.generated` audit event, so a saved one has always been
    audited. `new_since` counts what has been added since it was generated."""

    brief_id: str  # ties this response to its audit event
    patient_id: str
    episode_id: str
    generated_at: str
    model_id: str
    prompt_version: str
    bullets: list[BriefBulletOut]
    covers: BriefCountsOut  # how much the model was shown
    truncated: bool  # older messages or contacts were left out
    new_since: BriefCountsOut


class CareProviderSummaryOut(BaseModel):
    """The AI care provider summary: one plain paragraph with no citations, checked for shape
    (core/provider_summary.py). Saved (migration 14) after its `care_provider_summary.generated`
    audit event, so a saved one has always been audited. `new_since` counts what has been added
    since it was generated."""

    summary_id: str  # ties this response to its audit event
    patient_id: str
    episode_id: str
    generated_at: str
    model_id: str
    prompt_version: str
    text: str
    covers: BriefCountsOut  # how much the model was shown
    truncated: bool  # older messages or contacts were left out
    new_since: BriefCountsOut


class TouchpointCreate(BaseModel):
    # Manual entries only: messages are logged automatically when one is sent, so
    # offering "message" here would invite counting the same contact twice.
    kind: Literal["call", "visit", "email"]
    # Defaults to now; a clinician can backdate (e.g. a call from this morning).
    occurred_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    note: str | None = Field(default=None, max_length=5000)

    @field_validator("occurred_at")
    @classmethod
    def _aware_and_not_future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        if value > datetime.now(UTC) + timedelta(minutes=5):  # small allowance for clock skew
            raise ValueError("occurred_at cannot be in the future")
        return value

    @field_validator("note")
    @classmethod
    def _blank_note_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class TouchpointOut(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    kind: Literal["call", "visit", "email", "message"]
    source: Literal["auto", "manual"]
    occurred_at: str
    duration_minutes: int | None
    note: str | None
    logged_by: str | None  # null for auto-logged rows
    created_at: str
