"""A clinician's own account: the audit record of a password change.

The password itself is changed in the browser, straight against Supabase Auth (re-sign-in with the
current password, then `updateUser`): this service never sees a password, old or new. What it does
is write the audit event, `clinician.password_changed`, because a browser can't write one. Since
the browser could also say so without having changed anything, the event is written only if
Supabase Auth itself shows the user was changed in the last couple of minutes.

One event per clinic the user belongs to, each in that clinic's chain: a password change is about
the person, and each clinic's log should show it. The payload holds the user id and nothing else;
there is no password material anywhere in it (or in the request, which has no body)."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.core import clock, gotrue, ratelimit
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import AuthedUser, require_user
from clinically_anchored_api.core.db import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(tags=["clinician-account"])

EVENT = "clinician.password_changed"
# How recent the Supabase Auth change must be for the claim to be believed.
FRESH_FOR = timedelta(minutes=2)
PER_MINUTE = 5


@router.post("/me/password-changed")
def password_changed(user: AuthedUser = Depends(require_user)) -> dict[str, int]:
    wait = ratelimit.hit(("password-changed", user.user_id), PER_MINUTE)
    if wait:
        raise HTTPException(
            status_code=429, detail="Too many requests.", headers={"Retry-After": str(wait)}
        )
    try:
        changed_at = gotrue.user_updated_at(user.user_id)
    except Exception as exc:
        logger.error("password_changed: could not read the user from Supabase Auth: %s", exc)
        raise HTTPException(status_code=503, detail="Couldn't confirm the change.") from exc
    if changed_at is None or clock.now() - changed_at > FRESH_FOR:
        raise HTTPException(status_code=409, detail="No recent password change found.")

    supabase = get_supabase()
    memberships = (
        supabase.table("clinic_members").select("clinic_id, role").eq("user_id", user.user_id)
        .execute().data
    )
    if not memberships:
        raise HTTPException(status_code=403, detail="Not a member of any clinic.")
    try:
        for m in memberships:
            record_event(
                supabase,
                clinic_id=m["clinic_id"],
                event_type=EVENT,
                payload={"user_id": user.user_id},
                metadata={
                    "ref_type": "user",
                    "ref_id": user.user_id,
                    "actor": {"type": "member", "id": user.user_id, "role": m["role"]},
                },
            )
    except AuditWriteError as exc:
        logger.error("%s done but audit write failed: %s", EVENT, exc)
        raise HTTPException(status_code=500, detail="Password changed but not audited.") from exc
    return {"audited_in_clinics": len(memberships)}
