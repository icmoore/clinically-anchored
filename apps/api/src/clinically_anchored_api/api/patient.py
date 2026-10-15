"""The patient's own screen state: one endpoint every patient page starts from."""

from fastapi import APIRouter, Depends

from clinically_anchored_api.api.episodes import iso
from clinically_anchored_api.core import categories
from clinically_anchored_api.core.clinic_info import office_phone
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.security import (
    PatientLink,
    require_patient_link_even_if_closed,
)
from clinically_anchored_api.schemas import PatientStateOut

router = APIRouter(tags=["patient"])


@router.get("/episode", response_model=PatientStateOut)
def patient_state(
    link: PatientLink = Depends(require_patient_link_even_if_closed),
) -> PatientStateOut:
    """What the patient may do right now, and when the next message window opens. This is the
    one patient route that still answers once the episode has closed, and then it says only
    "closed" and the office phone: the link no longer opens anything else."""
    supabase = get_supabase()
    phone = office_phone(supabase, link.clinic_id)
    state = link.state
    if state.closed:
        return PatientStateOut(phase=state.phase.value, office_phone=phone)
    schedule_tz = _timezone(supabase, link.episode["schedule_version"])
    return PatientStateOut(
        phase=state.phase.value,
        office_phone=phone,
        check_in_done=link.episode["checkin_completed_at"] is not None,
        categories=list(categories.OFFERED),
        post_op_day=state.post_op_day,
        interval_no=state.interval_no,
        intervals_total=state.intervals_total,
        interval_closes_at=iso(state.interval_closes_at),
        next_opens_at=iso(state.next_opens_at),
        episode_closes_at=iso(state.episode_closes_at),
        timezone=schedule_tz,
        can_send_scheduled=state.can_send_scheduled,
        can_reply=state.can_reply,
    )


def _timezone(supabase, version: int) -> str:
    rows = supabase.table("message_schedules").select("timezone").eq("version", version).execute()
    return rows.data[0]["timezone"]
