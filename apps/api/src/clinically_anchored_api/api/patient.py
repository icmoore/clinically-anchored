"""The patient's own screen state: one endpoint every patient page starts from."""

from fastapi import APIRouter, Depends

from clinically_anchored_api.api.episodes import iso
from clinically_anchored_api.core import categories
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.schedule import MissedReason, WindowState, WindowStatus
from clinically_anchored_api.core.security import (
    PatientLink,
    require_patient_link_even_if_closed,
)
from clinically_anchored_api.schemas import PatientStateOut, PatientWindowOut

router = APIRouter(tags=["patient"])


def _office_phone(supabase, clinic_id: str) -> str | None:
    rows = supabase.table("clinics").select("office_phone").eq("id", clinic_id).execute().data
    return rows[0]["office_phone"] if rows else None


@router.get("/episode", response_model=PatientStateOut)
def patient_state(
    link: PatientLink = Depends(require_patient_link_even_if_closed),
) -> PatientStateOut:
    """What the patient may do right now, and when the next message window opens. This is the
    one patient route that still answers once the episode has closed, and then it says only
    "closed" and the office phone: the link no longer opens anything else."""
    supabase = get_supabase()
    phone = _office_phone(supabase, link.clinic_id)
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
        windows=patient_windows(state.windows),
    )


def patient_windows(windows: tuple[WindowState, ...]) -> list[PatientWindowOut]:
    """The clinic's window states, reduced to what a patient is shown. A window the patient could
    not use (it ended before their check-in) is "before_check_in", never "missed"; one that ended
    unused is "passed". A window running by the clock before the check-in is "upcoming": they can't
    use it yet. A window cut short by a discharge can't appear (a closed episode returns none)."""
    out = []
    for w in windows:
        if w.status is WindowStatus.USED:
            status = "used"
        elif w.status is WindowStatus.OPEN:
            status = "open"
        elif w.status is WindowStatus.MISSED:
            status = (
                "before_check_in" if w.missed_reason is MissedReason.BEFORE_CHECK_IN else "passed"
            )
        elif w.status in (WindowStatus.UPCOMING, WindowStatus.LOCKED):
            status = "upcoming"
        else:
            continue
        out.append(
            PatientWindowOut(
                number=w.number, status=status, opens_at=iso(w.opens_at), closes_at=iso(w.closes_at)
            )
        )
    return out


def _timezone(supabase, version: int) -> str:
    rows = supabase.table("message_schedules").select("timezone").eq("version", version).execute()
    return rows.data[0]["timezone"]
