"""The patient's first sign-in, after the code: confirm the surgery date, then "Before you start".

  GET  /patient/account    where the account stands (any status), for the screens to route by
  POST /patient/identity   the surgery date; it must match the latest care period
  POST /patient/consent    "Before you start": accept the Terms, the Privacy Notice and both
                           consents, or decline; with the date done, an accept makes the account
                           active
  POST /patient/withdraw   "Stop using the app": a recorded withdrawal that ends the messaging
                           windows and the account's access

An invited patient reaches only the two POSTs before the last; every other patient route needs an
active account (core/patient_auth.py). The identity check is the same one the fallback link uses:
three wrong dates lock the account, and the patient is told to call the office.

"Before you start" is versioned (core/legal.py). Every answer is an append-only row in
`patient_acceptances` with the versions and the SHA-256 of the text shown, written after its audit
event like every other change. An ACTIVE patient whose latest acceptance does not match the current
versions is asked again: `needs_acceptance` on GET /patient/account is what the web app routes by.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from clinically_anchored_api.core import (
    clock,
    consent_wording,
    episodes,
    gotrue,
    legal,
    patient_auth,
)
from clinically_anchored_api.core.audit import AuditWriteError
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.patient_auth import PatientSession
from clinically_anchored_api.schemas import AcceptanceIn, IdentityIn, PatientAccountOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient first sign-in"])

RELOAD = "The wording changed. Please reload the page and read it again."
BOTH = "Please agree to both to continue."


def _account_out(supabase, account: dict) -> PatientAccountOut:
    clinic = (
        supabase.table("clinics").select("name, office_phone").eq("id", account["clinic_id"])
        .execute().data[0]
    )
    maximum = get_settings().patient_identity_max_attempts
    now = legal.current()
    return PatientAccountOut(
        status=account["status"],
        needs_identity=account["identity_verified_at"] is None,
        needs_acceptance=legal.needs_acceptance(
            supabase, account["clinic_id"], account["patient_id"]
        ),
        identity_attempts_left=max(0, maximum - account["identity_failed_attempts"]),
        terms_version=now.terms_version,
        privacy_version=now.privacy_version,
        consent_version=now.consent_version,
        required_consents=list(consent_wording.REQUIRED),
        clinic_name=clinic["name"],
        office_phone=clinic["office_phone"],
    )


def activate_if_ready(supabase, account: dict) -> dict:
    """Make the account active once the surgery date AND "Before you start" (the current versions)
    are done. Audit first, then change; returns the account as it now stands."""
    account = patient_auth.account_by_id(supabase, account["id"])
    if (
        account["status"] != "invited"
        or account["identity_verified_at"] is None
        or legal.needs_acceptance(supabase, account["clinic_id"], account["patient_id"])
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


# --- "Before you start" ---------------------------------------------------------------------


def _live_consents(supabase, account: dict) -> dict[str, str]:
    """consent type -> the wording version of its live grant (the newest, if there are several)."""
    rows = (
        supabase.table("consents")
        .select("consent_type, consent_text_version")
        .eq("clinic_id", account["clinic_id"])
        .eq("patient_id", account["patient_id"])
        .is_("revoked_at", "null")
        .execute()
        .data
    )
    return {r["consent_type"]: r["consent_text_version"] for r in rows}


def _acceptance_row(
    supabase, account: dict, episode: dict, action: str, shown: dict, *, event_type: str
) -> dict:
    """Audit, then insert, one append-only `patient_acceptances` row. `shown` holds the three
    versions and three hashes the row records. Returns the row."""
    acceptance_id, created_at = str(uuid.uuid4()), clock.now().isoformat()
    row = {
        "id": acceptance_id,
        "clinic_id": account["clinic_id"],
        "patient_id": account["patient_id"],
        "episode_id": episode["id"],
        "action": action,
        **shown,
    }
    patient_auth.audit_first(
        supabase,
        clinic_id=account["clinic_id"],
        event_type=event_type,
        ref_type="patient_acceptance",
        ref_id=acceptance_id,
        actor=patient_auth.patient_actor(account["patient_id"]),
        payload={
            "acceptance_id": acceptance_id,
            "patient_id": account["patient_id"],
            "episode_id": episode["id"],
            "action": action,
            "created_at": created_at,
            **shown,
        },
        patient_id=account["patient_id"],
    )
    # If the insert fails the event is left without a row (reconcile: informational), never a row
    # without its event.
    supabase.table("patient_acceptances").insert({**row, "created_at": created_at}).execute()
    return {**row, "created_at": created_at}


def _shown_now(now: legal.Current) -> dict:
    return {
        "terms_version": now.terms_version,
        "privacy_version": now.privacy_version,
        "consent_version": now.consent_version,
        "terms_sha256": now.terms_sha256,
        "privacy_sha256": now.privacy_sha256,
        "consent_sha256": now.consent_sha256,
    }


def _grant_consents(supabase, account: dict, now: legal.Current) -> None:
    """The two rows in `consents` that go with an acceptance (each sentence at its own version).
    Audit first; a double-click's duplicate is absorbed by the unique index."""
    clinic_id, patient_id = account["clinic_id"], account["patient_id"]
    have = _live_consents(supabase, account)
    for consent_type in consent_wording.REQUIRED:
        version = now.consent_items[consent_type]
        if have.get(consent_type) == version:
            continue
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
                "consent_text_version": version,
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
                    "consent_text_version": version,
                    "granted_at": granted_at,
                }
            ).execute()
        except Exception:
            # A double-click: the other request's identical grant won the unique index.
            if _live_consents(supabase, account).get(consent_type) != version:
                raise


@router.post("/patient/consent", response_model=PatientAccountOut)
def answer_before_you_start(
    body: AcceptanceIn, session: PatientSession = Depends(patient_auth.require_live_patient)
):
    """Accept (both consents ticked) or decline. A decline records the answer and changes nothing
    else: the patient is asked again at their next sign-in."""
    supabase = get_supabase()
    account = session.account
    if account["identity_verified_at"] is None:
        raise HTTPException(status_code=409, detail="Please confirm your surgery date first.")

    now = legal.current()
    shown = {
        "terms_version": body.terms_version,
        "privacy_version": body.privacy_version,
        "consent_version": body.consent_version,
        "consent_sha256": body.consent_sha256,
    }
    expected = {k: v for k, v in _shown_now(now).items() if k in shown}
    if shown != expected:
        raise HTTPException(status_code=409, detail=RELOAD)
    if body.action == legal.ACCEPTED and sorted(set(body.consents)) != sorted(
        consent_wording.REQUIRED
    ):
        raise HTTPException(status_code=422, detail=BOTH)

    clinic_id, patient_id = account["clinic_id"], account["patient_id"]
    episode = episodes.latest_episode(supabase, clinic_id, patient_id)
    if episode is None:
        raise HTTPException(status_code=409, detail="There is no care period for this account.")

    latest = legal.latest(supabase, clinic_id, patient_id)
    already = body.action == legal.ACCEPTED and legal.matches_current(latest)
    if not already:  # a double-click or a second tab adds nothing
        _acceptance_row(
            supabase, account, episode, body.action, _shown_now(now),
            event_type=f"acceptance.{body.action}",
        )
    if body.action == legal.ACCEPTED:
        _grant_consents(supabase, account, now)
        account = activate_if_ready(supabase, account)
    return _account_out(supabase, account)


# --- "Stop using the app" ---------------------------------------------------------------------


def _end_consents(supabase, account: dict) -> None:
    """End every live consent (audit first, then the update, per grant)."""
    clinic_id, patient_id = account["clinic_id"], account["patient_id"]
    live = (
        supabase.table("consents")
        .select("id, consent_type, consent_text_version, granted_at")
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .is_("revoked_at", "null")
        .execute()
        .data
    )
    for grant in live:
        revoked_at = clock.now().isoformat()
        patient_auth.audit_first(
            supabase,
            clinic_id=clinic_id,
            event_type="consent.revoked",
            ref_type="consent",
            ref_id=grant["id"],
            actor=patient_auth.patient_actor(patient_id),
            payload={
                "consent_id": grant["id"],
                "patient_id": patient_id,
                "consent_type": grant["consent_type"],
                "consent_text_version": grant["consent_text_version"],
                "granted_at": grant["granted_at"],
                "revoked_at": revoked_at,
            },
            patient_id=patient_id,
        )
        supabase.table("consents").update({"revoked_at": revoked_at}).eq("id", grant["id"]).eq(
            "clinic_id", clinic_id
        ).is_("revoked_at", "null").execute()


@router.post("/patient/withdraw", response_model=PatientAccountOut)
def stop_using_the_app(session: PatientSession = Depends(patient_auth.require_live_patient)):
    """The patient chose to stop. In this order (each step can be repeated if one fails, because
    the patient is not locked out until the last): record the withdrawal, end the consents, close
    the care period (which stops the messaging windows), then turn the account off and end every
    sign-in. The clinic can re-invite them later; they will be asked "Before you start" again."""
    supabase = get_supabase()
    account = session.account
    clinic_id, patient_id = account["clinic_id"], account["patient_id"]
    episode = episodes.latest_episode(supabase, clinic_id, patient_id)
    if episode is None:
        raise HTTPException(status_code=409, detail="There is no care period for this account.")

    # What the row records: the versions they last answered, else the current ones.
    previous = legal.latest(supabase, clinic_id, patient_id)
    keep = ("terms_version", "privacy_version", "consent_version",
            "terms_sha256", "privacy_sha256", "consent_sha256")
    shown = {k: previous[k] for k in keep} if previous else _shown_now(legal.current())
    _acceptance_row(
        supabase, account, episode, legal.WITHDRAWN, shown, event_type="acceptance.withdrawn"
    )
    _end_consents(supabase, account)

    try:
        _, _changed = episodes.close(
            supabase, episode, reason="withdrawn", closed_by=session.user.id, closed_at=None,
            actor=patient_auth.patient_actor(patient_id),
        )
    except AuditWriteError as exc:
        logger.error("episode %s closed (withdrawn) but audit write failed: %s", episode["id"], exc)
        raise HTTPException(
            status_code=500, detail="Could not record that, so please try again."
        ) from exc

    if account["status"] != "off":
        patient_auth.audit_first(
            supabase,
            clinic_id=clinic_id,
            event_type="patient_account.access_turned_off",
            ref_type="patient_account",
            ref_id=account["id"],
            actor=patient_auth.patient_actor(patient_id),
            payload={"account_id": account["id"], "patient_id": patient_id,
                     "was": account["status"], "reason": "withdrawn"},
            patient_id=patient_id,
        )
        rows = supabase.table("patient_accounts").update(
            {
                "status": "off",
                "turned_off_at": clock.now().isoformat(),
                "turned_off_by": session.user.id,
            }
        ).eq("id", account["id"]).eq("status", account["status"]).execute().data
        if rows:
            account = rows[0]
            supabase.table("patient_sign_in_links").update(
                {"revoked_at": clock.now().isoformat()}
            ).eq("account_id", account["id"]).is_("used_at", "null").is_(
                "revoked_at", "null"
            ).execute()
    try:
        gotrue.ban(account["user_id"])
        supabase.rpc("end_patient_sessions", {"p_user_id": account["user_id"]}).execute()
    except Exception as exc:
        logger.error("withdrawn account %s but ending sessions failed (%s)",
                     account["id"], type(exc).__name__)
        raise HTTPException(
            status_code=502, detail="Your account is closed, but we could not sign you out. "
            "Please close this page."
        ) from exc
    return _account_out(supabase, patient_auth.account_by_id(supabase, account["id"]))
