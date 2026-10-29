"""The clinic's side of a patient's account: invite, resend, turn off, re-invite, a fallback link.

  GET  .../patients/{id}/account               where the account stands            any member
  POST .../account/invite                      first invite (needs the patient's email)  any member
  POST .../account/resend                      send the invite again               any member
  POST .../account/turn-off                    end access and every session        owner, clinician
  POST .../account/reinvite                    after turn-off or a lock            owner, clinician
  POST .../account/fallback-link               a single-use link, shown once       owner, clinician
  PUT  .../patients/{id}/email                 set the address invites go to       any member

Re-inviting and the fallback link are limited to owner and clinician, like turning access off: all
three hand out or restore a way in, which a delegate does not decide.

Every change is audited BEFORE it is made (core/patient_auth.audit_first); the events carry ids
only. Changing the email of an ACTIVE account is refused: turn access off, change it, re-invite.
Changing it while the patient is still only invited is allowed; "Resend" then updates the address
at Supabase Auth and mails the new one.
"""

import logging
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.api.messages import _require_patient_in_clinic
from clinically_anchored_api.core import clock, gotrue, mailer, patient_auth
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.email_templates import INVITE
from clinically_anchored_api.core.episodes import member_actor
from clinically_anchored_api.schemas import (
    AccountOut,
    FallbackLinkInfo,
    FallbackLinkOut,
    InviteEmailOut,
    PatientEmailIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient accounts"])

ADMIN_ROLES = ("owner", "clinician")
BASE = "/clinics/{clinic_id}/patients/{patient_id}"


def _require_admin(member: ClinicMember) -> None:
    if member.role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only an owner or clinician can do this.")


def _patient(supabase, clinic_id: str, patient_id: str) -> dict:
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    return (
        supabase.table("patients").select("id, full_name, email").eq("clinic_id", clinic_id)
        .eq("id", patient_id).execute().data[0]
    )


def _out(supabase, patient: dict, account: dict | None) -> AccountOut:
    if account is None:
        return AccountOut(status="none", email=patient["email"])
    invite = (
        supabase.table("email_outbox").select("status, created_at")
        .eq("clinic_id", account["clinic_id"]).eq("patient_id", account["patient_id"])
        .eq("template", INVITE).order("created_at", desc=True).limit(1).execute().data
    )
    link = (
        supabase.table("patient_sign_in_links")
        .select("issued_at, expires_at, used_at, revoked_at").eq("account_id", account["id"])
        .order("issued_at", desc=True).limit(1).execute().data
    )
    return AccountOut(
        status=account["status"],
        email=patient["email"],
        invited_at=account["invited_at"],
        activated_at=account["activated_at"],
        turned_off_at=account["turned_off_at"],
        locked_at=account["locked_at"],
        last_sign_in_at=account["last_sign_in_at"],
        last_invite_email=(
            InviteEmailOut(status=invite[0]["status"], at=invite[0]["created_at"])
            if invite else None
        ),
        fallback_link=FallbackLinkInfo(**link[0]) if link else None,
    )


def _actor(member: ClinicMember) -> dict:
    return member_actor(member)


def _audit(supabase, member: ClinicMember, event_type: str, account_id: str, patient_id: str,
           payload: dict | None = None, *, ref_type: str = "patient_account",
           ref_id: str | None = None) -> None:
    patient_auth.audit_first(
        supabase,
        clinic_id=member.clinic_id,
        event_type=event_type,
        ref_type=ref_type,
        ref_id=ref_id or account_id,
        actor=_actor(member),
        payload={"account_id": account_id, "patient_id": patient_id, **(payload or {})},
        patient_id=patient_id,
    )


def _find_user(supabase, email: str) -> str | None:
    found = supabase.rpc("find_auth_user_by_email", {"p_email": email}).execute().data
    return found["id"] if found else None


def _check_email_free(supabase, email: str, patient_id: str, account: dict | None) -> None:
    """409 (with a plain reason) if `email` cannot become this patient's sign-in: Supabase Auth
    allows one user per address, so an address already held by a staff login, or by another
    patient's account, cannot be used -- and finding out at Resend would be an unexplained 500.

    A patient with NO account yet may take an address whose only holder is a turned-off patient
    (the sign-in is reused). One who already has an account is changing it, so any other holder
    blocks it."""
    holder = _find_user(supabase, email)
    if holder is None or (account is not None and str(holder) == str(account["user_id"])):
        return
    if supabase.table("clinic_members").select("id").eq("user_id", holder).execute().data:
        raise HTTPException(status_code=409, detail="That email belongs to a staff account.")
    others = supabase.table("patient_accounts").select("patient_id, status").eq(
        "user_id", holder
    ).execute().data
    other_patient = [o for o in others if str(o["patient_id"]) != str(patient_id)]
    if other_patient and (account is not None or any(o["status"] != "off" for o in other_patient)):
        raise HTTPException(
            status_code=409,
            detail="That email already belongs to another patient's account "
            "(even one whose access is turned off). Use a different email.",
        )
    if account is not None:
        raise HTTPException(
            status_code=409,
            detail="That email is already used by another sign-in. Use a different email.",
        )


def _ensure_user(supabase, patient: dict, account: dict | None) -> str:
    """The Auth user behind this patient's account: made if new, brought in line with the patient's
    email, marked as a patient and un-banned. Refuses an address another login already holds."""
    email = patient["email"]
    if account is not None:
        user_id = account["user_id"]
        if email:
            _check_email_free(supabase, email, patient["id"], account)
        return gotrue.ensure_patient_user(email or gotrue.user_email(user_id), user_id=user_id)
    if email is None:
        return gotrue.ensure_patient_user(gotrue.placeholder_email())
    _check_email_free(supabase, email, patient["id"], None)
    return gotrue.ensure_patient_user(email, user_id=_find_user(supabase, email))


def _conflict(exc: Exception) -> HTTPException:
    text = str(exc)
    if "patient_accounts_one_live_per_user" in text or "patient_accounts_patient_id_key" in text:
        return HTTPException(
            status_code=409,
            detail="That email belongs to another patient account, or this patient has one.",
        )
    if "patient_account_user_is_staff" in text:
        return HTTPException(status_code=409, detail="That email belongs to a staff account.")
    raise exc


def _send_invite(supabase, member: ClinicMember, patient: dict) -> None:
    if patient["email"]:
        mailer.send_email(
            supabase, clinic_id=member.clinic_id, patient_id=patient["id"],
            to=patient["email"], template=INVITE,
        )


def _create_account(supabase, member: ClinicMember, patient: dict, *, audit_payload: dict) -> dict:
    account_id = str(uuid.uuid4())
    _audit(supabase, member, "patient_account.invited", account_id, patient["id"], audit_payload)
    user_id = _ensure_user(supabase, patient, None)
    try:
        rows = supabase.table("patient_accounts").insert(
            {
                "id": account_id,
                "clinic_id": member.clinic_id,
                "patient_id": patient["id"],
                "user_id": user_id,
                "status": "invited",
                "invited_at": clock.now().isoformat(),
                "invited_by": member.user_id,
            }
        ).execute().data
    except Exception as exc:
        raise _conflict(exc) from exc
    return rows[0]


@router.get(BASE + "/account", response_model=AccountOut)
def get_account(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> AccountOut:
    supabase = get_supabase()
    patient = _patient(supabase, clinic_id, patient_id)
    return _out(supabase, patient, patient_auth.account_for_patient(supabase, patient_id))


@router.put(BASE + "/email", response_model=AccountOut)
def set_email(
    clinic_id: str,
    patient_id: str,
    body: PatientEmailIn,
    member: ClinicMember = Depends(require_clinic_member),
) -> AccountOut:
    supabase = get_supabase()
    patient = _patient(supabase, clinic_id, patient_id)
    email = mailer.clean_email(body.email)
    account = patient_auth.account_for_patient(supabase, patient_id)
    if email and email != patient["email"]:
        _check_email_free(supabase, email, patient_id, account)
    if account is not None and account["status"] == "active" and email != patient["email"]:
        raise HTTPException(
            status_code=409,
            detail="Turn off this patient's access before changing the email, then re-invite.",
        )
    if email != patient["email"]:
        patient_auth.audit_first(
            supabase, clinic_id=clinic_id, event_type="patient.email_changed",
            ref_type="patient", ref_id=patient_id, actor=_actor(member),
            payload={"patient_id": patient_id, "from": patient["email"], "to": email},
            patient_id=patient_id,
        )
        try:
            supabase.table("patients").update({"email": email}).eq("clinic_id", clinic_id).eq(
                "id", patient_id
            ).execute()
        except Exception as exc:
            if "patient_email_locked" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail="Turn off this patient's access before changing the email.",
                ) from exc
            raise
        patient = {**patient, "email": email}
    return _out(supabase, patient, account)


@router.post(BASE + "/account/invite", response_model=AccountOut)
def invite(
    clinic_id: str, patient_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> AccountOut:
    supabase = get_supabase()
    patient = _patient(supabase, clinic_id, patient_id)
    existing = patient_auth.account_for_patient(supabase, patient_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail=_exists_message(existing["status"]))
    if not patient["email"]:
        raise HTTPException(status_code=422, detail="Add the patient's email first.")
    account = _create_account(supabase, member, patient, audit_payload={"email": patient["email"]})
    _send_invite(supabase, member, patient)
    return _out(supabase, patient, account)


def _exists_message(status: str) -> str:
    return {
        "invited": "This patient is already invited. Use Resend.",
        "active": "This patient already has an active account.",
    }.get(status, "This patient already has an account. Use Re-invite.")


@router.post(BASE + "/account/resend", response_model=AccountOut)
def resend(
    clinic_id: str, patient_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> AccountOut:
    supabase = get_supabase()
    patient = _patient(supabase, clinic_id, patient_id)
    account = patient_auth.account_for_patient(supabase, patient_id)
    if account is None or account["status"] != "invited":
        raise HTTPException(status_code=409, detail="Only a patient who is invited can be re-sent.")
    if not patient["email"]:
        raise HTTPException(status_code=422, detail="Add the patient's email first.")
    _check_email_free(supabase, patient["email"], patient_id, account)  # before anything is audited
    _audit(supabase, member, "patient_account.invited", account["id"], patient_id,
           {"email": patient["email"], "resend": True})
    _ensure_user(supabase, patient, account)
    supabase.table("patient_accounts").update({"invited_at": clock.now().isoformat()}).eq(
        "id", account["id"]
    ).eq("status", "invited").execute()
    _send_invite(supabase, member, patient)
    return _out(supabase, patient, patient_auth.account_by_id(supabase, account["id"]))


def _revoke_unused_links(supabase, account_id: str) -> None:
    supabase.table("patient_sign_in_links").update({"revoked_at": clock.now().isoformat()}).eq(
        "account_id", account_id
    ).is_("used_at", "null").is_("revoked_at", "null").execute()


@router.post(BASE + "/account/reinvite", response_model=AccountOut)
def reinvite(
    clinic_id: str, patient_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> AccountOut:
    _require_admin(member)
    supabase = get_supabase()
    patient = _patient(supabase, clinic_id, patient_id)
    account = patient_auth.account_for_patient(supabase, patient_id)
    if account is None or account["status"] not in ("locked", "off"):
        raise HTTPException(
            status_code=409,
            detail="Only an account that is locked or turned off can be re-invited.",
        )
    if patient["email"]:
        _check_email_free(supabase, patient["email"], patient_id, account)
    _audit(supabase, member, "patient_account.invited", account["id"], patient_id,
           {"email": patient["email"], "reinvite": True, "was": account["status"]})
    _ensure_user(supabase, patient, account)
    rows = supabase.table("patient_accounts").update(
        {
            "status": "invited",
            "invited_at": clock.now().isoformat(),
            "invited_by": member.user_id,
            "identity_verified_at": None,
            "identity_failed_attempts": 0,
            "code_sent_at": None,
            "code_failed_attempts": 0,
        }
    ).eq("id", account["id"]).eq("status", account["status"]).execute().data
    if not rows:
        raise HTTPException(status_code=409, detail="The account changed. Please reload.")
    _revoke_unused_links(supabase, account["id"])
    # They must sign in again: a locked patient's old sessions end with the lock.
    supabase.rpc("end_patient_sessions", {"p_user_id": account["user_id"]}).execute()
    _send_invite(supabase, member, patient)
    return _out(supabase, patient, rows[0])


@router.post(BASE + "/account/turn-off", response_model=AccountOut)
def turn_off(
    clinic_id: str, patient_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> AccountOut:
    _require_admin(member)
    supabase = get_supabase()
    patient = _patient(supabase, clinic_id, patient_id)
    account = patient_auth.account_for_patient(supabase, patient_id)
    if account is None:
        raise HTTPException(status_code=409, detail="This patient has no account.")
    if account["status"] != "off":
        _audit(supabase, member, "patient_account.access_turned_off", account["id"], patient_id,
               {"was": account["status"]})
        rows = supabase.table("patient_accounts").update(
            {
                "status": "off",
                "turned_off_at": clock.now().isoformat(),
                "turned_off_by": member.user_id,
            }
        ).eq("id", account["id"]).eq("status", account["status"]).execute().data
        if not rows:
            raise HTTPException(status_code=409, detail="The account changed. Please reload.")
        account = rows[0]
        _revoke_unused_links(supabase, account["id"])
    # From here the api already refuses the patient (the status is read on every request). Now end
    # what Supabase holds. Safe to repeat: turning off an account that is off only does this part.
    try:
        gotrue.ban(account["user_id"])
        supabase.rpc("end_patient_sessions", {"p_user_id": account["user_id"]}).execute()
    except Exception as exc:
        logger.error("access off for account %s but ending sessions failed (%s)",
                     account["id"], type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Access is turned off, but their sign-ins could not be ended. Please try again.",
        ) from exc
    return _out(supabase, patient, account)


@router.post(BASE + "/account/fallback-link", response_model=FallbackLinkOut)
def fallback_link(
    clinic_id: str, patient_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> FallbackLinkOut:
    """A single-use sign-in link for a patient with no email (or no phone to read it on). It is
    shown ONCE: only a hash of its secret is kept. A newer link replaces any unused older one."""
    _require_admin(member)
    settings = get_settings()
    base = settings.web_base_url or (
        "http://localhost:3000" if settings.environment == "development" else ""
    )
    if not base:
        raise HTTPException(status_code=503, detail="WEB_BASE_URL is not configured.")
    supabase = get_supabase()
    patient = _patient(supabase, clinic_id, patient_id)
    account = patient_auth.account_for_patient(supabase, patient_id)
    if account is None:
        account = _create_account(supabase, member, patient, audit_payload={"delivery": "link"})
    elif account["status"] not in patient_auth.LIVE:
        raise HTTPException(status_code=409, detail="Re-invite this patient first.")

    secret = secrets.token_urlsafe(32)
    link_id = str(uuid.uuid4())
    issued = clock.now()
    expires = issued + timedelta(hours=settings.patient_fallback_link_ttl_hours)
    _audit(supabase, member, "patient_account.fallback_link_issued", account["id"], patient_id,
           {"link_id": link_id, "expires_at": expires.isoformat()},
           ref_type="patient_sign_in_link", ref_id=link_id)
    _revoke_unused_links(supabase, account["id"])
    supabase.table("patient_sign_in_links").insert(
        {
            "id": link_id,
            "clinic_id": clinic_id,
            "account_id": account["id"],
            "token_hash": patient_auth.hash_link_token(secret),
            "issued_by": member.user_id,
            "issued_at": issued.isoformat(),
            "expires_at": expires.isoformat(),
        }
    ).execute()
    return FallbackLinkOut(
        url=f"{base.rstrip('/')}/sign-in/link#{secret}", expires_at=expires.isoformat()
    )
