"""Message endpoints, clinician side and patient side.

Every clinician route is scoped by clinic through `require_clinic_member`, and every
patient/message id is re-checked against that clinic: the service role bypasses RLS, so a
clinic_id in the URL is only trustworthy once we've confirmed the rows we touch actually
belong to it.

Writes go through database functions (migration 9) that lock the episode, so a message can't
slip into an episode that was just discharged, and the database is what refuses a second
message in an interval. WHICH interval is open comes from core/schedule.py."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.core import categories, episodes
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.schedule import Phase
from clinically_anchored_api.core.security import PatientLink, require_patient_link
from clinically_anchored_api.core.touchpoints import log_message_sent
from clinically_anchored_api.schemas import (
    ClinicianMessageCreate,
    MessageOut,
    PatientMessageCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["messages"])

_COLUMNS = (
    "id, clinic_id, patient_id, episode_id, sender, body, origin, interval_no, category, "
    "reply_to_message_id, read_at, created_at"
)


def _require_patient_in_clinic(supabase, clinic_id: str, patient_id: str) -> None:
    result = (
        supabase.table("patients")
        .select("id")
        .eq("id", patient_id)
        .eq("clinic_id", clinic_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Patient not found.")


def audit_message_sent(supabase, *, clinic_id: str, row: dict, actor: dict) -> None:
    """Record `message.sent` for a stored message row. Raises AuditWriteError.

    The text and the category are in the hashed payload. The readable metadata carries
    ids, the origin, the interval and the category KEY (a menu choice, not content)."""
    record_event(
        supabase,
        clinic_id=clinic_id,
        event_type="message.sent",
        payload={
            "message_id": row["id"],
            "patient_id": row["patient_id"],
            "episode_id": row["episode_id"],
            "sender": row["sender"],
            "origin": row["origin"],
            "interval_no": row["interval_no"],
            "category": row["category"],
            "reply_to_message_id": row["reply_to_message_id"],
            "body": row["body"],
        },
        metadata={
            "ref_type": "message",
            "ref_id": row["id"],
            "actor": actor,
            "episode_id": row["episode_id"],
            "origin": row["origin"],
            "interval_no": row["interval_no"],
            "category": row["category"],
        },
    )


def _audit_or_500(supabase, *, clinic_id: str, row: dict, actor: dict) -> None:
    try:
        audit_message_sent(supabase, clinic_id=clinic_id, row=row, actor=actor)
    except AuditWriteError as exc:
        logger.error("message %s saved but audit write failed: %s", row["id"], exc)
        raise HTTPException(status_code=500, detail="Message saved but not audited.") from exc


@router.get(
    "/clinics/{clinic_id}/patients/{patient_id}/messages", response_model=list[MessageOut]
)
def list_thread(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> list[MessageOut]:
    """Every message with this patient, across episodes, oldest first."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    result = (
        supabase.table("messages")
        .select(_COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("created_at")
        .execute()
    )
    return [MessageOut(**row) for row in result.data]


@router.post(
    "/clinics/{clinic_id}/patients/{patient_id}/messages", response_model=MessageOut
)
def send_message(
    clinic_id: str,
    patient_id: str,
    body: ClinicianMessageCreate,
    member: ClinicMember = Depends(require_clinic_member),
) -> MessageOut:
    """Clinician/delegate -> patient, at any time while the patient's episode is open. Always
    stored with sender='clinician'; the caller can't choose the sender. Every clinician
    message lets the patient send one reply."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    episode, _state = episodes.open_episode_or_409(supabase, clinic_id, patient_id)
    try:
        row = (
            supabase.rpc(
                "send_clinician_message",
                {"p_clinic_id": clinic_id, "p_episode_id": episode["id"], "p_body": body.body},
            )
            .execute()
            .data
        )
    except Exception as exc:
        if "episode_closed" in str(exc):  # discharged between our check and the write
            raise HTTPException(
                status_code=409, detail="This patient has no open care period."
            ) from exc
        raise
    log_message_sent(supabase, clinic_id=clinic_id, message=row)
    _audit_or_500(supabase, clinic_id=clinic_id, row=row, actor=episodes.member_actor(member))
    return MessageOut(**row)


def _get_message(supabase, clinic_id: str, message_id: str) -> dict:
    found = (
        supabase.table("messages")
        .select(_COLUMNS)
        .eq("id", message_id)
        .eq("clinic_id", clinic_id)
        .execute()
        .data
    )
    if not found:
        raise HTTPException(status_code=404, detail="Message not found.")
    return found[0]


@router.post("/clinics/{clinic_id}/messages/{message_id}/read", response_model=MessageOut)
def mark_read(
    clinic_id: str, message_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> MessageOut:
    """Mark a patient's message as read by the clinic. Idempotent: only the
    first read sets read_at, and only that read is audited (`message.read`)."""
    supabase = get_supabase()
    row = _get_message(supabase, clinic_id, message_id)
    # An extension request is a thread entry the clinic answers by deciding it, not a message.
    if (
        row["sender"] != "patient"
        or row["origin"] == "extension_request"
        or row["read_at"] is not None
    ):
        return MessageOut(**row)

    # `is null` in the update makes concurrent readers race safely: only one
    # update matches, and only that caller writes the audit event.
    updated = (
        supabase.table("messages")
        .update({"read_at": datetime.now(UTC).isoformat()})
        .eq("id", message_id)
        .eq("clinic_id", clinic_id)
        .is_("read_at", "null")
        .execute()
        .data
    )
    if not updated:
        return MessageOut(**_get_message(supabase, clinic_id, message_id))  # lost the race
    row = updated[0]
    try:
        record_event(
            supabase,
            clinic_id=clinic_id,
            event_type="message.read",
            payload={
                "message_id": message_id,
                "patient_id": row["patient_id"],
                "read_by": member.user_id,
                "read_at": row["read_at"],
            },
            metadata={
                "ref_type": "message",
                "ref_id": message_id,
                "actor": episodes.member_actor(member),
            },
        )
    except AuditWriteError as exc:
        logger.error("message %s marked read but audit write failed: %s", message_id, exc)
        raise HTTPException(status_code=500, detail="Message marked read but not audited.") from exc
    return MessageOut(**row)


# --- Patient side: authenticated by the episode link, no account. -------------


@router.get("/messages", response_model=list[MessageOut])
def patient_list_thread(link: PatientLink = Depends(require_patient_link)) -> list[MessageOut]:
    result = (
        get_supabase()
        .table("messages")
        .select(_COLUMNS)
        .eq("episode_id", link.episode_id)
        .order("created_at")
        .execute()
    )
    return [MessageOut(**row) for row in result.data]


def _refuse_scheduled(link: PatientLink) -> None:
    """Why the patient can't send a scheduled message right now (403/409), or return."""
    phase = link.state.phase
    if phase is Phase.LOCKED_CHECKIN:
        raise HTTPException(
            status_code=403, detail="Messaging opens after you complete your check-in."
        )
    if phase is Phase.WAITING:
        raise HTTPException(status_code=409, detail="Your first message window has not opened yet.")
    if phase is Phase.USED:
        raise HTTPException(
            status_code=409, detail="You have already sent your message for this window."
        )


@router.post("/messages", response_model=MessageOut)
def patient_send_message(
    body: PatientMessageCreate, link: PatientLink = Depends(require_patient_link)
) -> MessageOut:
    """The patient writes to the clinic: either the one message for the interval that is open
    now (`kind` "scheduled", with a category), or the one reply to the care team's latest
    message (`kind` "reply"). The interval is never the client's choice: it is worked out here."""
    state = link.state
    if body.kind == "scheduled":
        _refuse_scheduled(link)
        if not categories.is_offered(body.category):
            raise HTTPException(status_code=422, detail="Pick what your message is about.")
        params = {"p_origin": "scheduled", "p_interval_no": state.interval_no,
                  "p_category": body.category}
    else:
        if not state.can_reply:
            raise HTTPException(
                status_code=409, detail="There is no message from your care team to reply to."
            )
        params = {"p_origin": "reply", "p_interval_no": None, "p_category": None}

    try:
        row = (
            get_supabase()
            .rpc(
                "send_patient_message",
                {"p_clinic_id": link.clinic_id, "p_episode_id": link.episode_id,
                 "p_body": body.body, **params},
            )
            .execute()
            .data
        )
    except Exception as exc:
        # The database has the last word: these are the races the checks above can lose.
        text = str(exc)
        for code, status, detail in (
            ("episode_closed", 410, "This care period has ended."),
            ("check_in_required", 403, "Messaging opens after you complete your check-in."),
            ("interval_already_used", 409, "You have already sent your message for this window."),
            ("no_reply_available", 409, "There is no message from your care team to reply to."),
            ("message_body_invalid", 422, "Your message is empty or too long."),
        ):
            if code in text:
                raise HTTPException(status_code=status, detail=detail) from exc
        raise
    _audit_or_500(
        get_supabase(), clinic_id=link.clinic_id, row=row,
        actor={"type": "patient", "id": link.patient_id},
    )
    return MessageOut(**row)
