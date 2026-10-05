"""Pydantic request/response models. Kept separate from the FastAPI routers
so the shapes are easy to scan in one place -- this file is effectively the
contract apps/web's generated OpenAPI client is built from."""

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
    procedure_id: str | None = None
    post_op_day: int | None = Field(default=None, ge=0)
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
    procedure_id: str | None
    post_op_day: int | None
    answers: dict
    is_red_flag: bool
    created_at: str


class DevCheckinLinkRequest(BaseModel):
    clinic_id: str
    patient_id: str
    scope: Literal["checkin", "messages"] = "checkin"


class DevCheckinLinkResponse(BaseModel):
    token: str
    scope: str


class CheckInContext(BaseModel):
    """What the check-in page needs to render its form, resolved from the
    token alone -- the patient's link never carries the clinic id directly."""

    clinic_id: str
    procedures: list[Procedure]


class MessageCreate(BaseModel):
    # Messages are free-form text from both sides (decision 5); the cap is a
    # sanity limit, not a product rule.
    body: str = Field(min_length=1, max_length=5000)


class MessageOut(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    sender: str
    body: str
    read_at: str | None
    created_at: str


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
    # Phone or email the check-in link is sent to (free text for now).
    contact: str | None = Field(default=None, max_length=200)


class PatientOut(BaseModel):
    id: str
    full_name: str
    contact: str | None
    created_at: str


class LinkOut(BaseModel):
    scope: Literal["checkin", "messages"]
    url: str
    expires_at: str


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
