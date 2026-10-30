"""Clinic-scoped endpoints the clinician web app needs around patients:
who am I / which clinics, list (paged), read and create patients (a new patient comes with
their episode: the surgery date and procedure are entered by the clinic). Episodes and discharge
are in api/episodes.py.

Link delivery stands in for SMS/email during trials: a clinician copies the link and sends
it to the patient themselves."""

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from clinically_anchored_api.api.episodes import check_new_episode, create_episode, episode_outs
from clinically_anchored_api.core import clock
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import (
    AuthedUser,
    ClinicMember,
    require_clinic_member,
    require_user,
)
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.episodes import latest_episode
from clinically_anchored_api.core.mailer import clean_email
from clinically_anchored_api.core.patient_list import (
    MAX_PAGE_SIZE,
    SurgeryRange,
    month_bounds,
    resolve_range,
)
from clinically_anchored_api.schemas import (
    ClinicOut,
    MeOut,
    PatientCreate,
    PatientOut,
    PatientPage,
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
            supabase.table("clinics")
            .select("id, name, is_demo")
            .in_("id", sorted(roles))
            .execute()
            .data
        )
        clinics = [
            ClinicOut(
                id=r["id"], name=r["name"], role=roles[r["id"]], is_demo=bool(r.get("is_demo"))
            )
            for r in rows
        ]
    return MeOut(user_id=user.user_id, email=user.email, clinics=clinics)


SURGERY_MONTH = r"^(19|2\d)\d{2}-(0[1-9]|1[0-2])$"  # YYYY-MM, 1900-2999


def _patient_outs(supabase, rows: list[dict], now) -> list[PatientOut]:
    """PatientOut for each row ({patient columns, account_status, episode row or null}), with
    episode state computed for just these rows (batched), at `now`."""
    outs = episode_outs(supabase, [r["episode"] for r in rows if r["episode"]], now)
    return [
        PatientOut(
            **{k: v for k, v in r.items() if k != "episode"},
            episode=outs[r["episode"]["id"]] if r["episode"] else None,
        )
        for r in rows
    ]


def _surgery_dates(
    now, named: SurgeryRange | None, month: str | None, start: date | None, end: date | None
) -> tuple[date | None, date | None]:
    """The inclusive surgery-date bounds the request means. One way to say it, not several."""
    if sum([named is not None, month is not None, start is not None or end is not None]) > 1:
        raise HTTPException(
            status_code=422,
            detail="Give one of surgery_range, surgery_month, or surgery_from/surgery_to.",
        )
    if named is not None:
        return resolve_range(named, now)
    if month is not None:
        return month_bounds(int(month[:4]), int(month[5:]))
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="surgery_from is after surgery_to.")
    return start, end


@router.get("/clinics/{clinic_id}/patients", response_model=PatientPage)
def list_patients(
    clinic_id: str,
    status: Literal["active", "ended", "all"] = "active",
    surgery_range: SurgeryRange | None = None,
    surgery_month: str | None = Query(default=None, pattern=SURGERY_MONTH),
    surgery_from: date | None = None,
    surgery_to: date | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=5, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    _member: ClinicMember = Depends(require_clinic_member),
) -> PatientPage:
    """One page of the clinic's patients, newest surgery first then name (patients with no care
    period last). The database filters, searches, sorts, counts and pages; episode state is
    computed only for the patients on the page (a naturally ended episode shows as closed and is
    settled when next touched). `status` is about the patient's LATEST care period: active =
    still in follow-up, ended = discharged or run out; only `all` includes patients with none.
    Surgery dates are inclusive and, for the named ranges, resolved in the clinic's time zone."""
    now = clock.now()
    start, end = _surgery_dates(now, surgery_range, surgery_month, surgery_from, surgery_to)
    supabase = get_supabase()
    page = (
        supabase.rpc(
            "list_patients_page",
            {
                "p_clinic_id": clinic_id,
                "p_now": now.isoformat(),
                "p_status": status,
                "p_surgery_from": start.isoformat() if start else None,
                "p_surgery_to": end.isoformat() if end else None,
                "p_q": (q or "").strip() or None,
                "p_limit": limit,
                "p_offset": offset,
            },
        )
        .execute()
        .data
    )
    return PatientPage(
        items=_patient_outs(supabase, page["items"], now),
        total=page["total"],
        limit=limit,
        offset=offset,
        surgery_from=start.isoformat() if start else None,
        surgery_to=end.isoformat() if end else None,
    )


@router.get("/clinics/{clinic_id}/patients/{patient_id}", response_model=PatientOut)
def get_patient(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> PatientOut:
    """One patient of this clinic, in the same shape as a list row (the patient page's header)."""
    supabase = get_supabase()
    rows = (
        supabase.table("patients")
        .select("id, full_name, contact, email, created_at")
        .eq("clinic_id", clinic_id)
        .eq("id", patient_id)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Patient not found.")
    accounts = (
        supabase.table("patient_accounts").select("status")
        .eq("clinic_id", clinic_id).eq("patient_id", patient_id).execute().data
    )
    episode = latest_episode(supabase, clinic_id, patient_id)
    return _patient_outs(
        supabase,
        [{**rows[0], "account_status": accounts[0]["status"] if accounts else "none",
          "episode": episode}],
        clock.now(),
    )[0]


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
