"""Episodes, clinician side: create (with the patient), read, correct, discharge, and the
patient's link.

An episode is one patient's post-op period: a surgery date and procedure entered by the
clinic, and a schedule version. Everything about which interval is open is computed
(core/schedule.py); the routes here only change the stored facts, and audit each change:

  episode.created / episode.corrected / episode.closed / episode.link_rotated, link.issued

Discharging is a clinical decision, so it is limited to owner and clinician (a delegate
can create and correct episodes and hand out links)."""

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.api.messages import _require_patient_in_clinic
from clinically_anchored_api.core import clock, episodes
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.schedule import EpisodeState, Schedule, episode_end
from clinically_anchored_api.core.security import create_episode_token
from clinically_anchored_api.schemas import (
    EpisodeCorrect,
    EpisodeOut,
    EpisodeStateOut,
    LinkOut,
    NewEpisode,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["episodes"])

DISCHARGE_ROLES = frozenset({"owner", "clinician"})
LINK_PATH = "/care"


# --- shaping -------------------------------------------------------------------------------


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def state_out(state: EpisodeState) -> EpisodeStateOut:
    return EpisodeStateOut(
        phase=state.phase.value,
        post_op_day=state.post_op_day,
        intervals_total=state.intervals_total,
        interval_no=state.interval_no,
        interval_opens_at=iso(state.interval_opens_at),
        interval_closes_at=iso(state.interval_closes_at),
        next_opens_at=iso(state.next_opens_at),
        episode_closes_at=iso(state.episode_closes_at),
        closed_at=iso(state.closed_at),
        close_reason=state.close_reason,
        missed=list(state.missed),
        reply_available=state.reply_available,
        can_send_scheduled=state.can_send_scheduled,
        can_reply=state.can_reply,
    )


def episode_outs(
    supabase, rows: list[dict], now: datetime | None = None
) -> dict[str, EpisodeOut]:
    """EpisodeOut per episode id, with each state computed at `now` (batched). Reads only."""
    if not rows:
        return {}
    now = now or clock.now()
    schedules = episodes.schedules_for(supabase, {r["schedule_version"] for r in rows})
    states = episodes.states_for(supabase, rows, now, schedules)
    return {
        r["id"]: EpisodeOut(
            id=r["id"],
            patient_id=r["patient_id"],
            procedure_id=r["procedure_id"],
            surgery_date=str(r["surgery_date"]),
            schedule_version=r["schedule_version"],
            timezone=schedules[r["schedule_version"]].timezone,
            checkin_completed_at=r["checkin_completed_at"],
            closed_at=r["closed_at"],
            close_reason=r["close_reason"],
            created_at=r["created_at"],
            state=state_out(states[r["id"]]),
        )
        for r in rows
    }


def audit_or_500(supabase, event_type: str, episode: dict, member: ClinicMember, payload=None):
    try:
        episodes.audit_episode(
            supabase,
            event_type=event_type,
            episode=episode,
            actor=episodes.member_actor(member),
            payload=payload,
        )
    except AuditWriteError as exc:
        logger.error("%s done but audit write failed: %s", event_type, exc)
        raise HTTPException(status_code=500, detail="Saved but not audited.") from exc


def _load(supabase, clinic_id: str, episode_id: str) -> dict:
    row = episodes.get_episode(supabase, episode_id, clinic_id=clinic_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Episode not found.")
    return row


def _require_active_procedure(supabase, clinic_id: str, procedure_id: str) -> None:
    found = (
        supabase.table("procedures").select("id").eq("id", procedure_id)
        .eq("clinic_id", clinic_id).eq("active", True).execute().data
    )
    if not found:
        raise HTTPException(status_code=422, detail="Unknown procedure for this clinic.")


# --- creating ------------------------------------------------------------------------------


def check_new_episode(supabase, clinic_id: str, procedure_id: str, surgery_date: date) -> Schedule:
    """Validate what a new episode would be made from (422 if not) and return its schedule:
    the newest version. Nothing is written."""
    _require_active_procedure(supabase, clinic_id, procedure_id)
    schedule = episodes.latest_schedule(supabase)
    if episode_end(surgery_date, schedule) <= clock.now():
        raise HTTPException(
            status_code=422,
            detail="The message schedule for that surgery date has already ended.",
        )
    return schedule


def create_episode(
    supabase,
    *,
    member: ClinicMember,
    patient_id: str,
    procedure_id: str,
    surgery_date: date,
) -> dict:
    """Create the patient's episode on the newest schedule and audit it. The caller has
    already checked that the patient belongs to the clinic."""
    clinic_id = member.clinic_id
    schedule = check_new_episode(supabase, clinic_id, procedure_id, surgery_date)

    # A previous episode that ran out by itself but was never touched since is still "open"
    # to the database; close it first so a new surgery for the same patient is allowed.
    previous = episodes.latest_episode(supabase, clinic_id, patient_id)
    if previous is not None:
        episodes.current_state(supabase, previous)

    try:
        inserted = (
            supabase.table("episodes")
            .insert(
                {
                    "clinic_id": clinic_id,
                    "patient_id": patient_id,
                    "procedure_id": procedure_id,
                    "surgery_date": surgery_date.isoformat(),
                    "schedule_version": schedule.version,
                    "created_by": member.user_id,
                }
            )
            .execute()
            .data
        )
    except Exception as exc:
        if "episodes_one_open_per_patient" in str(exc):
            raise HTTPException(
                status_code=409, detail="This patient already has an open care period."
            ) from exc
        raise
    if not inserted:
        raise HTTPException(status_code=500, detail="Episode was not saved.")
    row = inserted[0]
    audit_or_500(
        supabase, "episode.created", row, member,
        {
            "procedure_id": procedure_id,
            "surgery_date": surgery_date.isoformat(),
            "schedule": schedule.snapshot(),
            "created_by": member.user_id,
        },
    )
    return row


@router.post("/clinics/{clinic_id}/patients/{patient_id}/episodes", response_model=EpisodeOut)
def new_episode_for_patient(
    clinic_id: str,
    patient_id: str,
    body: NewEpisode,
    member: ClinicMember = Depends(require_clinic_member),
) -> EpisodeOut:
    """A new surgery for a patient who is already in the system."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    row = create_episode(
        supabase, member=member, patient_id=patient_id,
        procedure_id=body.procedure_id, surgery_date=body.surgery_date,
    )
    return episode_outs(supabase, [row])[row["id"]]


# --- reading -------------------------------------------------------------------------------


@router.get("/clinics/{clinic_id}/patients/{patient_id}/episode", response_model=EpisodeOut)
def get_patient_episode(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> EpisodeOut:
    """The patient's latest episode and where it stands now."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    row = episodes.latest_episode(supabase, clinic_id, patient_id)
    if row is None:
        raise HTTPException(status_code=404, detail="This patient has no episode.")
    row, _state = episodes.current_state(supabase, row)
    return episode_outs(supabase, [row])[row["id"]]


# --- correcting and discharging ----------------------------------------------------------


@router.patch("/clinics/{clinic_id}/episodes/{episode_id}", response_model=EpisodeOut)
def correct_episode(
    clinic_id: str,
    episode_id: str,
    body: EpisodeCorrect,
    member: ClinicMember = Depends(require_clinic_member),
) -> EpisodeOut:
    """Fix a mistyped surgery date or procedure. Allowed only until the patient has done
    the check-in (the database refuses afterwards: every interval would move)."""
    if body.surgery_date is None and body.procedure_id is None:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    supabase = get_supabase()
    old = _load(supabase, clinic_id, episode_id)
    if old["closed_at"] is not None:
        raise HTTPException(status_code=409, detail="This care period has ended.")

    changes: dict = {}
    if body.surgery_date is not None:
        schedule = episodes.get_schedule(supabase, old["schedule_version"])
        if episode_end(body.surgery_date, schedule) <= clock.now():
            raise HTTPException(
                status_code=422,
                detail="The message schedule for that surgery date has already ended.",
            )
        changes["surgery_date"] = body.surgery_date.isoformat()
    if body.procedure_id is not None:
        _require_active_procedure(supabase, clinic_id, body.procedure_id)
        changes["procedure_id"] = body.procedure_id

    try:
        updated = (
            supabase.table("episodes").update(changes).eq("id", episode_id)
            .eq("clinic_id", clinic_id).is_("closed_at", "null").execute().data
        )
    except Exception as exc:
        text = str(exc)
        if "episodes_surgery_locked" in text:
            raise HTTPException(
                status_code=409,
                detail="The surgery date and procedure can't change after the patient has "
                "checked in. Close this care period and start a new one instead.",
            ) from exc
        if "episodes_closed" in text:
            raise HTTPException(status_code=409, detail="This care period has ended.") from exc
        raise
    if not updated:
        raise HTTPException(status_code=409, detail="This care period has ended.")
    row = updated[0]
    audit_or_500(
        supabase, "episode.corrected", row, member,
        {
            "from": {"surgery_date": str(old["surgery_date"]), "procedure_id": old["procedure_id"]},
            "to": {"surgery_date": str(row["surgery_date"]), "procedure_id": row["procedure_id"]},
        },
    )
    return episode_outs(supabase, [row])[row["id"]]


@router.post("/clinics/{clinic_id}/episodes/{episode_id}/close", response_model=EpisodeOut)
def discharge(
    clinic_id: str, episode_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> EpisodeOut:
    """Close the episode early: the patient's link stops working at once. There is no undo."""
    if member.role not in DISCHARGE_ROLES:
        raise HTTPException(status_code=403, detail="Only a clinician can discharge a patient.")
    supabase = get_supabase()
    episode = _load(supabase, clinic_id, episode_id)
    episode, state = episodes.current_state(supabase, episode)  # settles a naturally ended one
    if state.closed:
        raise HTTPException(status_code=409, detail="This care period has already ended.")
    try:
        row, changed = episodes.close(
            supabase, episode, reason="discharged", closed_by=member.user_id,
            closed_at=None, actor=episodes.member_actor(member),
        )
    except AuditWriteError as exc:
        logger.error("episode %s discharged but audit write failed: %s", episode_id, exc)
        raise HTTPException(status_code=500, detail="Saved but not audited.") from exc
    if not changed:  # someone else closed it between our check and the write
        raise HTTPException(status_code=409, detail="This care period has already ended.")
    return episode_outs(supabase, [row])[row["id"]]


# --- the patient's link ------------------------------------------------------------------------


def _link_url(token: str) -> str:
    settings = get_settings()
    base = settings.web_base_url or (
        "http://localhost:3000" if settings.environment == "development" else ""
    )
    if not base:
        raise HTTPException(status_code=503, detail="WEB_BASE_URL is not configured.")
    return f"{base.rstrip('/')}{LINK_PATH}?token={token}"


def _open_episode_row(supabase, clinic_id: str, episode_id: str) -> dict:
    episode = _load(supabase, clinic_id, episode_id)
    episode, state = episodes.current_state(supabase, episode)
    if state.closed:
        raise HTTPException(status_code=409, detail="This care period has ended.")
    return episode


@router.post("/clinics/{clinic_id}/episodes/{episode_id}/link", response_model=LinkOut)
def issue_link(
    clinic_id: str, episode_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> LinkOut:
    """The patient's link for the current generation: the same URL every time until the link
    is reset. Anyone holding it can use it until the episode closes, so hand it only to the
    patient. Every issuance is audited (without the token itself)."""
    supabase = get_supabase()
    episode = _open_episode_row(supabase, clinic_id, episode_id)
    url = _link_url(
        create_episode_token(episode_id=episode["id"], generation=episode["link_generation"])
    )
    try:
        record_event(
            supabase,
            clinic_id=clinic_id,
            event_type="link.issued",
            payload={"ref_type": "episode", "ref_id": episode["id"],
                     "generation": episode["link_generation"]},
            metadata={
                "patient_id": episode["patient_id"],
                "episode_id": episode["id"],
                "generation": episode["link_generation"],
                "actor": episodes.member_actor(member),
            },
        )
    except AuditWriteError as exc:
        logger.error("link.issued audit write failed: %s", exc)
        raise HTTPException(status_code=500, detail="Saved but not audited.") from exc
    return LinkOut(url=url, generation=episode["link_generation"])


@router.post("/clinics/{clinic_id}/episodes/{episode_id}/link/reset", response_model=LinkOut)
def reset_link(
    clinic_id: str, episode_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> LinkOut:
    """Revoke the current link (e.g. it was sent to the wrong number) and return a new one.
    The old URL stops working immediately."""
    supabase = get_supabase()
    episode = _open_episode_row(supabase, clinic_id, episode_id)
    updated = (
        supabase.table("episodes")
        .update({"link_generation": episode["link_generation"] + 1})
        .eq("id", episode_id).eq("clinic_id", clinic_id)
        .eq("link_generation", episode["link_generation"])  # two resets at once: one wins
        .is_("closed_at", "null")
        .execute()
        .data
    )
    if not updated:
        raise HTTPException(status_code=409, detail="The link changed; try again.")
    row = updated[0]
    audit_or_500(
        supabase, "episode.link_rotated", row, member,
        {"from_generation": episode["link_generation"], "to_generation": row["link_generation"]},
    )
    token = create_episode_token(episode_id=row["id"], generation=row["link_generation"])
    return LinkOut(url=_link_url(token), generation=row["link_generation"])
