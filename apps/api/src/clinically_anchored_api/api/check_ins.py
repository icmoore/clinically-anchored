import logging

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.rules import evaluate_red_flags
from clinically_anchored_api.core.security import PatientContext, require_patient
from clinically_anchored_api.schemas import (
    CheckInContext,
    CheckInCreate,
    CheckInOut,
    PatientCheckInOut,
    Procedure,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["check-ins"])


@router.get("/check-ins/context", response_model=CheckInContext)
def check_in_context(ctx: PatientContext = Depends(require_patient)) -> CheckInContext:
    """What the check-in page needs to render: the procedure the clinic chose for this
    episode (it picks the symptom set). The clinic comes from the signed-in patient, never the
    URL."""
    rows = (
        get_supabase()
        .table("procedures")
        .select("id, name, category")
        .eq("id", ctx.episode["procedure_id"])
        .eq("clinic_id", ctx.clinic_id)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=500, detail="This check-in is not set up correctly.")
    return CheckInContext(
        clinic_id=ctx.clinic_id,
        procedure=Procedure(**rows[0]),
        post_op_day=ctx.state.post_op_day,
        check_in_done=ctx.episode["checkin_completed_at"] is not None,
    )


@router.get("/check-ins/mine", response_model=PatientCheckInOut | None)
def my_check_in(ctx: PatientContext = Depends(require_patient)) -> PatientCheckInOut | None:
    """What the patient entered at their check-in, so they can look it up later (null until they
    have checked in). Only the answers and when; never the clinic's red flag or review state."""
    supabase = get_supabase()
    rows = (
        supabase.table("check_ins")
        .select("created_at, post_op_day, answers, procedure_id")
        .eq("episode_id", ctx.episode_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    row = rows[0]
    names = (
        supabase.table("procedures").select("name").eq("id", row["procedure_id"])
        .eq("clinic_id", ctx.clinic_id).execute().data
        if row["procedure_id"]
        else []
    )
    return PatientCheckInOut(
        submitted_at=row["created_at"],
        post_op_day=row["post_op_day"],
        procedure_name=names[0]["name"] if names else None,
        answers=row["answers"],
    )


@router.post("/check-ins", response_model=CheckInOut)
def submit_check_in(
    body: CheckInCreate, ctx: PatientContext = Depends(require_patient)
) -> CheckInOut:
    """The patient's one check-in for this episode. Completing it unlocks messaging. The
    post-op day is worked out from the surgery date, not typed by the patient. If it comes
    late, the intervals that already passed stay missed (they are recorded in the audit event)."""
    is_red_flag, _matched_rules = evaluate_red_flags(body.answers)
    supabase = get_supabase()

    try:
        out = (
            supabase.rpc(
                "complete_check_in",
                {
                    "p_clinic_id": ctx.clinic_id,
                    "p_episode_id": ctx.episode_id,
                    "p_post_op_day": ctx.state.post_op_day,
                    "p_answers": body.answers,
                    "p_is_red_flag": is_red_flag,
                },
            )
            .execute()
            .data
        )
    except Exception as exc:
        text = str(exc)
        if "check_in_already_done" in text:
            raise HTTPException(
                status_code=409, detail="You have already completed this check-in."
            ) from exc
        if "episode_closed" in text:
            raise HTTPException(status_code=410, detail="This care period has ended.") from exc
        raise

    row = out["check_in"]
    try:
        record_event(
            supabase,
            clinic_id=ctx.clinic_id,
            event_type="check_in.submitted",
            payload={
                "check_in_id": row["id"],
                "patient_id": ctx.patient_id,
                "episode_id": ctx.episode_id,
                "procedure_id": row["procedure_id"],
                "post_op_day": row["post_op_day"],
                "answers": row["answers"],
                "is_red_flag": row["is_red_flag"],
                "missed_intervals": list(ctx.state.missed),
            },
            metadata={
                "ref_type": "check_in",
                "ref_id": row["id"],
                "episode_id": ctx.episode_id,
                "actor": {"type": "patient", "id": ctx.patient_id},
            },
        )
    except AuditWriteError as exc:
        # The check-in row is already written; surface the gap loudly rather
        # than pretend the trail is complete.
        logger.error("check-in %s saved but audit write failed: %s", row["id"], exc)
        raise HTTPException(status_code=500, detail="Check-in saved but not audited.") from exc

    return CheckInOut(**row)
