"""Patient sign-in: an email, a 6-digit code, and a session that lasts weeks. No passwords.

Three public routes (no credential yet, so no `Authorization`):

  POST /patient-auth/request-code   an email in, "ok" out, whoever it belongs to
  POST /patient-auth/verify         an email and a code in, a session out
  POST /patient-auth/link           a single-use link and the surgery date in, a session out

Nothing here may tell a stranger whether an email is registered. So: the answer to a code request
is the same for every address and comes back before any work is done (the work -- find the
account, make the code, send the mail -- runs after the response); every failure to enter a code
is the same 401 and wording; and the limits that stop abuse (a code per minute, five per hour)
swallow the request quietly instead of answering differently. The only different answers are
per-client-address (429), which say nothing about any email.

A code lives in Supabase Auth, which makes it (admin `generate_link`: returns the code, sends no
mail) and later checks it. This module counts: five tries per code and ten minutes, in the
database, before Supabase is asked at all. Codes are mailed by core/mailer.py; Supabase never
sends mail (api/auth_hooks.py).
"""

import logging
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from clinically_anchored_api.core import clock, gotrue, mailer, patient_auth
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.email_templates import SIGN_IN_CODE
from clinically_anchored_api.schemas import (
    LinkRedeemIn,
    PatientSessionOut,
    RequestCodeIn,
    VerifyCodeIn,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient sign-in"])

# Six digits is what the dashboard should be set to (Email OTP Length), but Supabase decides the
# length, so a mis-set dashboard must not turn every real code into a refusal.
CODE = re.compile(r"^\d{6,10}$")
CODE_FAILED = "That code did not work. Check it, or ask for a new one."
LINK_FAILED = "This link does not work any more. Please ask your surgeon's office for a new one."


def _session_out(session: gotrue.AuthSession) -> PatientSessionOut:
    return PatientSessionOut(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
        expires_at=session.expires_at,
    )


def _require_configured() -> None:
    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_anon_key):
        raise HTTPException(status_code=503, detail="Sign-in is not available right now.")


# --- 1. ask for a code ------------------------------------------------------------------------


@router.post("/patient-auth/request-code", status_code=202)
def request_code(body: RequestCodeIn, request: Request, background: BackgroundTasks) -> dict:
    """Always the same answer. If the email belongs to an invited or active patient a code is
    emailed to it; otherwise nothing happens. Either way the caller cannot tell."""
    patient_auth.limit_by_ip(request, "request-code")
    email = mailer.normalize_address(body.email)
    if not mailer.is_valid_address(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    background.add_task(send_sign_in_code, email)
    return {"ok": True}


def send_sign_in_code(email: str) -> None:
    """Runs after the response. Never raises: a problem is logged and the patient asks again."""
    try:
        _send_sign_in_code(email)
    except Exception as exc:  # noqa: BLE001 -- the caller has already been answered
        logger.error("sign-in code not sent (%s)", type(exc).__name__)


def _send_sign_in_code(email: str) -> None:
    supabase = get_supabase()
    settings = get_settings()
    account = patient_auth.live_account_for_email(supabase, email)
    if account is None:
        return
    now = clock.now()

    last_code = patient_auth.parse_ts(account["code_sent_at"])
    if last_code and now - last_code < timedelta(seconds=settings.patient_code_cooldown_seconds):
        return
    recent = (
        supabase.table("email_outbox")
        .select("created_at")
        .eq("recipient", email)
        .eq("template", SIGN_IN_CODE)
        .order("created_at", desc=True)
        .limit(settings.patient_codes_per_hour)
        .execute()
        .data
    )
    if len(recent) >= settings.patient_codes_per_hour and (
        now - patient_auth.parse_ts(recent[-1]["created_at"]) < timedelta(hours=1)
    ):
        return

    outbox_id = str(uuid.uuid4())
    patient_auth.audit_first(
        supabase,
        clinic_id=account["clinic_id"],
        event_type="patient_account.sign_in_code_sent",
        ref_type="email",
        ref_id=outbox_id,
        actor=patient_auth.system_actor(),
        payload={"account_id": account["id"], "patient_id": account["patient_id"],
                 "email_id": outbox_id},
        patient_id=account["patient_id"],
    )
    code = gotrue.generate_code(gotrue.user_email(account["user_id"]))
    supabase.rpc("issue_patient_code", {"p_account_id": account["id"]}).execute()
    mailer.send_email(
        supabase,
        clinic_id=account["clinic_id"],
        patient_id=account["patient_id"],
        to=email,
        template=SIGN_IN_CODE,
        code=code,
        outbox_id=outbox_id,
    )


# --- 2. enter the code ------------------------------------------------------------------------


def _code_failed() -> HTTPException:
    return HTTPException(status_code=401, detail=CODE_FAILED)


@router.post("/patient-auth/verify", response_model=PatientSessionOut)
def verify_code(body: VerifyCodeIn, request: Request) -> PatientSessionOut:
    patient_auth.limit_by_ip(request, "verify")
    _require_configured()
    email = mailer.normalize_address(body.email)
    code = body.code.strip()
    if not mailer.is_valid_address(email) or not CODE.match(code):
        raise _code_failed()

    supabase = get_supabase()
    settings = get_settings()
    account = patient_auth.live_account_for_email(supabase, email)
    if account is None:
        raise _code_failed()
    # The try is counted before the code is checked, so parallel guesses cannot outrun the limit.
    claim = supabase.rpc(
        "claim_patient_code_attempt",
        {
            "p_account_id": account["id"],
            "p_max": settings.patient_code_max_attempts,
            "p_ttl_seconds": settings.patient_code_ttl_seconds,
        },
    ).execute().data
    if claim["outcome"] != "ok":
        raise _code_failed()
    try:
        session = gotrue.verify_code(gotrue.user_email(account["user_id"]), code)
    except gotrue.CodeRejected as exc:
        raise _code_failed() from exc
    if session.user_id != account["user_id"]:
        raise _code_failed()
    supabase.rpc("finish_patient_code", {"p_account_id": account["id"]}).execute()
    return _session_out(session)


# --- 3. the single-use fallback link ------------------------------------------------------------


@router.post("/patient-auth/link", response_model=PatientSessionOut)
def redeem_link(body: LinkRedeemIn, request: Request):
    """The clinic gave a patient with no email a link. It works once, for 24 hours, and ASKS FOR
    THE SURGERY DATE every time (not only the first): a link that was forwarded or read over a
    shoulder is not enough by itself. A wrong date does not use the link up; three wrong dates lock
    the account (the clinic re-invites)."""
    patient_auth.limit_by_ip(request, "link")
    _require_configured()
    supabase = get_supabase()
    token_hash = patient_auth.hash_link_token(body.token.strip())

    rows = (
        supabase.table("patient_sign_in_links")
        .select("id, clinic_id, account_id, expires_at, used_at, revoked_at")
        .eq("token_hash", token_hash)
        .execute()
        .data
    )
    link = rows[0] if rows else None
    if (
        link is None
        or link["used_at"] is not None
        or link["revoked_at"] is not None
        or patient_auth.parse_ts(link["expires_at"]) <= clock.now()
    ):
        raise HTTPException(status_code=401, detail=LINK_FAILED)
    account = patient_auth.account_by_id(supabase, link["account_id"])
    if account is None or account["status"] not in patient_auth.LIVE:
        raise HTTPException(status_code=401, detail=LINK_FAILED)

    outcome = patient_auth.check_identity(
        supabase, account, body.surgery_date,
        actor=patient_auth.patient_actor(account["patient_id"]),
    )
    if outcome["outcome"] == "wrong":
        return JSONResponse(
            status_code=422,
            content={"detail": patient_auth.WRONG_DATE, "attempts_left": outcome["attempts_left"]},
        )
    if outcome["outcome"] != "verified":
        return JSONResponse(
            status_code=403, content={"detail": patient_auth.LOCKED, "locked": True}
        )

    # Verified. Audit, then use the link up -- once: if someone else used it a moment ago the
    # atomic step finds nothing and this request gets no session.
    patient_auth.audit_first(
        supabase,
        clinic_id=link["clinic_id"],
        event_type="patient_account.fallback_link_used",
        ref_type="patient_sign_in_link",
        ref_id=link["id"],
        actor=patient_auth.patient_actor(account["patient_id"]),
        payload={"link_id": link["id"], "account_id": account["id"]},
        patient_id=account["patient_id"],
    )
    used = supabase.rpc("consume_patient_link", {"p_token_hash": token_hash}).execute().data
    if not used:
        raise HTTPException(status_code=401, detail=LINK_FAILED)
    try:
        session = gotrue.mint_session(gotrue.user_email(account["user_id"]))
    except gotrue.NotConfigured as exc:
        raise HTTPException(status_code=503, detail="Sign-in is not available right now.") from exc
    except gotrue.CodeRejected as exc:
        logger.error("fallback link used but no session could be made (account %s)", account["id"])
        raise HTTPException(
            status_code=502, detail="Could not sign you in. Please try again."
        ) from exc
    supabase.rpc("finish_patient_code", {"p_account_id": account["id"]}).execute()
    return _session_out(session)

