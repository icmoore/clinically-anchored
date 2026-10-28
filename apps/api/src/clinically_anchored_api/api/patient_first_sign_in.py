"""The patient's first sign-in, after the code: confirm the surgery date, then agree.

  GET  /patient/account    where the account stands (any status), for the screens to route by
  POST /patient/identity   the surgery date; it must match the latest care period
  POST /patient/consent    agree to messaging and AI-assisted replies; with the date done, the
                           account becomes active

An invited patient reaches only these two POSTs; every other patient route needs an active
account (core/patient_auth.py). The identity check is the same one the fallback link uses: three
wrong dates lock the account, and the patient is told to call the office.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from clinically_anchored_api.core import clock, consent_wording, patient_auth
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.patient_auth import PatientSession
from clinically_anchored_api.schemas import ConsentAcceptIn, IdentityIn, PatientAccountOut

router = APIRouter(tags=["patient first sign-in"])


def _live_consents(supabase, account: dict) -> set[str]:
    rows = (
        supabase.table("consents")
        .select("consent_type")
        .eq("clinic_id", account["clinic_id"])
        .eq("patient_id", account["patient_id"])
        .eq("consent_text_version", consent_wording.CURRENT_VERSION)
        .is_("revoked_at", "null")
        .execute()
        .data
    )
    return {r["consent_type"] for r in rows}


def _account_out(supabase, account: dict) -> PatientAccountOut:
    clinic = (
        supabase.table("clinics").select("name, office_phone").eq("id", account["clinic_id"])
        .execute().data[0]
    )
    maximum = get_settings().patient_identity_max_attempts
    needs_consent = not set(consent_wording.REQUIRED) <= _live_consents(supabase, account)
    return PatientAccountOut(
        status=account["status"],
        needs_identity=account["identity_verified_at"] is None,
        needs_consent=needs_consent,
        identity_attempts_left=max(0, maximum - account["identity_failed_attempts"]),
        consent_version=consent_wording.CURRENT_VERSION,
        required_consents=list(consent_wording.REQUIRED),
        clinic_name=clinic["name"],
        office_phone=clinic["office_phone"],
    )


def activate_if_ready(supabase, account: dict) -> dict:
    """Make the account active once the surgery date AND every required consent are done. Audit
    first, then change; returns the account as it now stands."""
    account = patient_auth.account_by_id(supabase, account["id"])
    if (
        account["status"] != "invited"
        or account["identity_verified_at"] is None
        or not set(consent_wording.REQUIRED) <= _live_consents(supabase, account)
    ):
        return account
    patient_auth.audit_first(
        supabase,
        clinic_id=account["clinic_id"],
        event_type="patient_account.activated",
        ref_type="patient_account",
        ref_id=account["id"],
        actor=patient_auth.patient_actor(account["patient_id"]),
        payload={"account_id": account["id"], "patient_id": account["patient_id"]},
        patient_id=account["patient_id"],
    )
    supabase.table("patient_accounts").update(
        {"status": "active", "activated_at": clock.now().isoformat()}
    ).eq("id", account["id"]).eq("status", "invited").execute()
    return patient_auth.account_by_id(supabase, account["id"])


@router.get("/patient/account", response_model=PatientAccountOut)
def my_account(
    session: PatientSession = Depends(patient_auth.require_patient_account),
) -> PatientAccountOut:
    return _account_out(get_supabase(), session.account)


@router.post("/patient/identity", response_model=PatientAccountOut)
def confirm_surgery_date(
    body: IdentityIn, session: PatientSession = Depends(patient_auth.require_first_sign_in)
):
    supabase = get_supabase()
    account = session.account
    if account["identity_verified_at"] is None:
        outcome = patient_auth.check_identity(
            supabase, account, body.surgery_date,
            actor=patient_auth.patient_actor(account["patient_id"]),
        )
        if outcome["outcome"] == "wrong":
            return JSONResponse(
                status_code=422,
                content={"detail": patient_auth.WRONG_DATE,
                         "attempts_left": outcome["attempts_left"]},
            )
        if outcome["outcome"] != "verified":
            return JSONResponse(
                status_code=403, content={"detail": patient_auth.LOCKED, "locked": True}
            )
    account = activate_if_ready(supabase, account)
    return _account_out(supabase, account)


@router.post("/patient/consent", response_model=PatientAccountOut)
def accept_consents(
    body: ConsentAcceptIn, session: PatientSession = Depends(patient_auth.require_first_sign_in)
):
    supabase = get_supabase()
    account = session.account
    if account["identity_verified_at"] is None:
        raise HTTPException(status_code=409, detail="Please confirm your surgery date first.")
    if body.consent_text_version != consent_wording.CURRENT_VERSION:
        raise HTTPException(
            status_code=409, detail="The wording changed. Please reload the page and read it again."
        )
    if sorted(set(body.consents)) != sorted(consent_wording.REQUIRED):
        raise HTTPException(status_code=422, detail="Please agree to both to continue.")

    clinic_id, patient_id = account["clinic_id"], account["patient_id"]
    have = _live_consents(supabase, account)
    for consent_type in consent_wording.REQUIRED:
        if consent_type in have:
            continue
        # Audit first, with the row's id and time decided up front; if the insert then fails the
        # event is left dangling (reconcile: informational), never a consent without an event.
        consent_id, granted_at = str(uuid.uuid4()), clock.now().isoformat()
        patient_auth.audit_first(
            supabase,
            clinic_id=clinic_id,
            event_type="consent.granted",
            ref_type="consent",
            ref_id=consent_id,
            actor=patient_auth.patient_actor(patient_id),
            payload={
                "consent_id": consent_id,
                "patient_id": patient_id,
                "consent_type": consent_type,
                "consent_text_version": consent_wording.CURRENT_VERSION,
                "granted_at": granted_at,
            },
            patient_id=patient_id,
        )
        try:
            supabase.table("consents").insert(
                {
                    "id": consent_id,
                    "clinic_id": clinic_id,
                    "patient_id": patient_id,
                    "consent_type": consent_type,
                    "consent_text_version": consent_wording.CURRENT_VERSION,
                    "granted_at": granted_at,
                }
            ).execute()
        except Exception:
            # A double-click: the other request's identical grant won the unique index.
            if consent_type not in _live_consents(supabase, account):
                raise
    account = activate_if_ready(supabase, account)
    return _account_out(supabase, account)
