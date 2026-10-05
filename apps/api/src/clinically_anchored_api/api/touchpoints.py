"""Patient contact/time log: list and manually log calls, visits and emails.

Message touchpoints are never created here -- they are written automatically
when a clinician message is sent (see core/touchpoints.py). Any clinic member
may log: recording that a call happened sends nothing, so this is not limited
to DECIDER_ROLES. Scoped by clinic like every clinician route; the patient id is
re-checked against the clinic because the service role bypasses RLS."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.api.messages import _require_patient_in_clinic
from clinically_anchored_api.core.audit import AuditWriteError
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.touchpoints import COLUMNS, audit_touchpoint, insert_touchpoint
from clinically_anchored_api.schemas import TouchpointCreate, TouchpointOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["touchpoints"])


@router.get(
    "/clinics/{clinic_id}/patients/{patient_id}/touchpoints",
    response_model=list[TouchpointOut],
)
def list_touchpoints(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> list[TouchpointOut]:
    """The patient's contact log, most recent contact first."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    result = (
        supabase.table("touchpoints")
        .select(COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("occurred_at", desc=True)
        .execute()
    )
    return [TouchpointOut(**row) for row in result.data]


@router.post(
    "/clinics/{clinic_id}/patients/{patient_id}/touchpoints", response_model=TouchpointOut
)
def log_touchpoint(
    clinic_id: str,
    patient_id: str,
    body: TouchpointCreate,
    member: ClinicMember = Depends(require_clinic_member),
) -> TouchpointOut:
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    occurred_at = body.occurred_at or datetime.now(UTC)
    try:
        row = insert_touchpoint(
            supabase,
            {
                "clinic_id": clinic_id,
                "patient_id": patient_id,
                "kind": body.kind,
                "source": "manual",
                "occurred_at": occurred_at.isoformat(),
                "duration_minutes": body.duration_minutes,
                "note": body.note,
                "logged_by": member.user_id,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="Touchpoint was not saved.") from exc
    try:
        audit_touchpoint(
            supabase,
            row=row,
            actor={"type": "member", "id": member.user_id, "role": member.role},
        )
    except AuditWriteError as exc:
        logger.error("touchpoint %s saved but audit write failed: %s", row["id"], exc)
        raise HTTPException(status_code=500, detail="Touchpoint saved but not audited.") from exc
    return TouchpointOut(**row)
