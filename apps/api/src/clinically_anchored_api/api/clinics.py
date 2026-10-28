"""Clinic-scoped endpoints the clinician web app needs around patients:
who am I / which clinics, list and create patients (a new patient comes with their episode:
the surgery date and procedure are entered by the clinic). Episodes, discharge and the
patient's link are in api/episodes.py.

Link delivery stands in for SMS/email during trials: a clinician copies the link and sends
it to the patient themselves."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.api.episodes import check_new_episode, create_episode, episode_outs
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import (
    AuthedUser,
    ClinicMember,
    require_clinic_member,
    require_user,
)
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.episodes import COLUMNS as EPISODE_COLUMNS
from clinically_anchored_api.core.mailer import clean_email
from clinically_anchored_api.schemas import (
    ClinicOut,
    MeOut,
    PatientCreate,
    PatientOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["clinics"])


def _audit_or_500(supabase, member: ClinicMember, event_type: str, ref: dict, payload: dict):
    try:
        record_event(
            supabase,
            clinic_id=member.clinic_id,
            event_type=event_type,
            payload=payload,
            metadata={
                **ref,
                "actor": {"type": "member", "id": member.user_id, "role": member.role},
            },
        )
    except AuditWriteError as exc:
        logger.error("%s done but audit write failed: %s", event_type, exc)
        raise HTTPException(status_code=500, detail="Saved but not audited.") from exc


@router.get("/me", response_model=MeOut)
def me(user: AuthedUser = Depends(require_user)) -> MeOut:
    """The signed-in user's clinics, so the web app knows where to route them."""
    supabase = get_supabase()
    memberships = (
        supabase.table("clinic_members")
        .select("clinic_id, role")
        .eq("user_id", user.user_id)
        .execute()
        .data
    )
    clinics: list[ClinicOut] = []
    if memberships:
        roles = {m["clinic_id"]: m["role"] for m in memberships}
        rows = (
            supabase.table("clinics").select("id, name").in_("id", sorted(roles)).execute().data
        )
        clinics = [ClinicOut(id=r["id"], name=r["name"], role=roles[r["id"]]) for r in rows]
    return MeOut(user_id=user.user_id, email=user.email, clinics=clinics)


@router.get("/clinics/{clinic_id}/patients", response_model=list[PatientOut])
def list_patients(
    clinic_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> list[PatientOut]:
    """Patients with their latest episode and where it stands (computed, nothing is closed
    here: a naturally ended episode shows as closed and is settled when next touched)."""
    supabase = get_supabase()
    rows = (
        supabase.table("patients")
        .select("id, full_name, contact, email, created_at")
        .eq("clinic_id", clinic_id)
        .order("full_name")
        .execute()
        .data
    )
    latest: dict[str, dict] = {}
    for ep in (
        supabase.table("episodes").select(EPISODE_COLUMNS).eq("clinic_id", clinic_id)
        .order("created_at", desc=True).execute().data
    ):
        latest.setdefault(ep["patient_id"], ep)
    outs = episode_outs(supabase, list(latest.values()))
    return [
        PatientOut(**r, episode=outs[latest[r["id"]]["id"]] if r["id"] in latest else None)
        for r in rows
    ]


@router.post("/clinics/{clinic_id}/patients", response_model=PatientOut)
def create_patient(
    clinic_id: str, body: PatientCreate, member: ClinicMember = Depends(require_clinic_member)
) -> PatientOut:
    """Add a patient together with their episode (surgery date and procedure). If the episode
    can't be created the patient is removed again, so no patient is left without one."""
    supabase = get_supabase()
    email = clean_email(body.email)
    # Reject a bad procedure or surgery date (422) before anything is written.
    check_new_episode(supabase, clinic_id, body.procedure_id, body.surgery_date)
    result = (
        supabase.table("patients")
        .insert(
            {
                "clinic_id": clinic_id,
                "full_name": body.full_name.strip(),
                "contact": body.contact.strip() if body.contact else None,
                "email": email,
            }
        )
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Patient was not saved.")
    row = result.data[0]
    # Identity fields are hashed into the audit payload, never stored in the log.
    _audit_or_500(
        supabase,
        member,
        "patient.created",
        {"ref_type": "patient", "ref_id": row["id"]},
        {"patient_id": row["id"], "full_name": row["full_name"], "contact": row["contact"],
         "email": row["email"]},
    )
    try:
        episode = create_episode(
            supabase, member=member, patient_id=row["id"],
            procedure_id=body.procedure_id, surgery_date=body.surgery_date,
        )
    except HTTPException as exc:
        if exc.status_code != 500:  # no episode row was written: take the patient back out
            _remove_patient(supabase, row["id"])
        raise
    return PatientOut(**row, episode=episode_outs(supabase, [episode])[episode["id"]])


def _remove_patient(supabase, patient_id: str) -> None:
    try:
        supabase.table("patients").delete().eq("id", patient_id).execute()
    except Exception:
        logger.exception("could not remove patient %s after its episode failed", patient_id)
