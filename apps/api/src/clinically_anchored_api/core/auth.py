"""Clinician/delegate authentication for the api.

Clinicians sign in through Supabase Auth in apps/web and send their access
token here as `Authorization: Bearer <jwt>`. This service uses the service
role key, which bypasses RLS, so tenant scoping has to be enforced here:
verify the JWT with Supabase, then confirm the user is a member of the clinic
named in the route. Patients never come through this path -- they use the
signed link tokens in core/security.py, or sign in as a patient account
(core/patient_auth.py). The two kinds of Supabase Auth user never mix: a token whose user is
marked app_metadata.account_type = 'patient' is refused by every route in this module.
"""

from dataclasses import dataclass

from fastapi import Header, HTTPException

from clinically_anchored_api.core.db import get_supabase


@dataclass(frozen=True)
class ClinicMember:
    user_id: str
    clinic_id: str
    role: str  # owner | clinician | delegate


@dataclass(frozen=True)
class AuthedUser:
    user_id: str
    email: str | None
    account_type: str | None = None  # 'patient' for a patient account; staff have none


def _authenticate(authorization: str | None) -> AuthedUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    jwt = authorization[7:].strip()

    try:
        user_response = get_supabase().auth.get_user(jwt)
    except Exception as exc:  # supabase-py raises its own AuthError types
        raise HTTPException(status_code=401, detail="Invalid or expired session.") from exc
    user = getattr(user_response, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    metadata = getattr(user, "app_metadata", None) or {}
    if metadata.get("account_type") == "patient":
        raise HTTPException(status_code=403, detail="This is a patient account, not a staff one.")
    return AuthedUser(
        user_id=user.id,
        email=getattr(user, "email", None),
        account_type=metadata.get("account_type"),
    )


def require_user(authorization: str | None = Header(default=None)) -> AuthedUser:
    """FastAPI dependency: any signed-in Supabase user (no clinic scoping)."""
    return _authenticate(authorization)


def require_clinic_member(
    clinic_id: str, authorization: str | None = Header(default=None)
) -> ClinicMember:
    """FastAPI dependency: `clinic_id` is read from the route's path."""
    user = _authenticate(authorization)
    result = (
        get_supabase()
        .table("clinic_members")
        .select("role")
        .eq("clinic_id", clinic_id)
        .eq("user_id", user.user_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=403, detail="Not a member of this clinic.")
    return ClinicMember(user_id=user.user_id, clinic_id=clinic_id, role=result.data[0]["role"])
