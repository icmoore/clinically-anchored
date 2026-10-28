"""Patient accounts: who the signed-in patient is, and what state their account is in.

A patient signs in with their email and a 6-digit code (api/patient_auth.py) and gets a Supabase
Auth session. On every request the api turns that session into one account row and one patient;
nothing in a request (a path, a query, a body) can name a different patient. The rules, in order:

  1. the access token is valid and its session still exists (Supabase Auth checks, so ending a
     user's sessions stops the token at once; the result is remembered for 30 seconds);
  2. the user is a PATIENT user (app_metadata.account_type, set only by the service role) -- a
     clinician's token is refused here, and a patient's token is refused on every clinician route;
  3. the session is not older than `patient_session_max_age_days`;
  4. the user has an account, and its status lets the request through:
       active          everything
       invited         only the first sign-in steps (surgery date, consent)
       locked / off    nothing but "tell me my status"
     The status is read from the database on every request, so turning access off blocks the api
     immediately, whatever the token cache says.
"""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import Header, HTTPException, Request

from clinically_anchored_api.core import clock, episodes, gotrue, ratelimit
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase

logger = logging.getLogger(__name__)

ACCOUNT_COLUMNS = (
    "id, clinic_id, patient_id, user_id, status, invited_at, invited_by, activated_at, "
    "turned_off_at, turned_off_by, locked_at, last_sign_in_at, identity_verified_at, "
    "identity_failed_attempts, code_sent_at, code_failed_attempts"
)
LIVE = ("invited", "active")

# What a patient is told. Plain words; the office phone is on the screen next to these.
SIGN_IN_AGAIN = "Please sign in again."
TURNED_OFF = "Access to this account has been turned off. Please call your surgeon's office."
LOCKED = "This account is locked. Please call your surgeon's office."
NOT_A_PATIENT = "This is not a patient account."
NOT_FINISHED = "Please finish signing in first."
WRONG_DATE = "That date does not match what the office has. Please check it and try again."

_CACHE_SECONDS = 30
_CACHE_MAX = 5000
_lock = threading.Lock()
_cache: dict[str, tuple[float, gotrue.AuthUser, datetime | None]] = {}


def reset_cache() -> None:
    """Forget remembered tokens (tests)."""
    with _lock:
        _cache.clear()


def parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- accounts -------------------------------------------------------------------------------


def account_by_id(supabase, account_id: str) -> dict | None:
    rows = (
        supabase.table("patient_accounts").select(ACCOUNT_COLUMNS).eq("id", account_id)
        .execute().data
    )
    return rows[0] if rows else None


def account_for_patient(supabase, patient_id: str) -> dict | None:
    rows = (
        supabase.table("patient_accounts").select(ACCOUNT_COLUMNS).eq("patient_id", patient_id)
        .execute().data
    )
    return rows[0] if rows else None


def account_for_user(supabase, user_id: str) -> dict | None:
    """The user's live account, else their newest (so a turned-off patient is told so)."""
    rows = (
        supabase.table("patient_accounts").select(ACCOUNT_COLUMNS).eq("user_id", user_id)
        .order("created_at", desc=True).execute().data
    )
    live = [r for r in rows if r["status"] != "off"]
    return (live or rows or [None])[0]


def live_account_for_email(supabase, email: str) -> dict | None:
    """The invited or active account whose patient has this email, or None. The caller must not
    let anyone tell the difference between "none" and "not allowed"."""
    patients = supabase.table("patients").select("id").eq("email", email).execute().data
    if not patients:
        return None
    rows = (
        supabase.table("patient_accounts").select(ACCOUNT_COLUMNS)
        .in_("patient_id", [p["id"] for p in patients]).order("created_at", desc=True)
        .execute().data
    )
    live = [r for r in rows if r["status"] in LIVE]
    return live[0] if live else None


# --- audit ----------------------------------------------------------------------------------


def patient_actor(patient_id: str) -> dict:
    return {"type": "patient", "id": patient_id}


def system_actor() -> dict:
    return {"type": "system"}


def audit_first(
    supabase,
    *,
    clinic_id: str,
    event_type: str,
    ref_type: str,
    ref_id: str,
    actor: dict,
    payload: dict,
    patient_id: str | None = None,
) -> None:
    """Record the event BEFORE the change it describes. If it cannot be recorded nothing changes
    (500). If the change then fails the event is left without a row: reconcile lists that as a
    harmless dangling event, never as an unaudited change. Metadata is ids only: no codes, no
    email, no names (those, if any, go in the hashed payload)."""
    metadata = {"ref_type": ref_type, "ref_id": ref_id, "actor": actor}
    if patient_id:
        metadata["patient_id"] = patient_id
    try:
        record_event(
            supabase, clinic_id=clinic_id, event_type=event_type, payload=payload,
            metadata=metadata,
        )
    except AuditWriteError as exc:
        logger.error("%s not recorded, nothing changed: %s", event_type, exc)
        raise HTTPException(
            status_code=500, detail="Could not record that, so nothing was changed."
        ) from exc


def hash_link_token(token: str) -> str:
    """What is stored of a fallback link's secret (api/patient_accounts.py, api/patient_auth.py)."""
    return hashlib.sha256(token.encode()).hexdigest()


# --- rate limits ----------------------------------------------------------------------------


def client_ip(request: Request) -> str:
    """The caller's address: the socket's, or (behind `trusted_proxy_hops` proxies) the one our
    nearest proxy recorded, counted from the right of X-Forwarded-For."""
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def limit_by_ip(request: Request, kind: str) -> None:
    """429 once one client address makes too many sign-in attempts of this kind in a minute."""
    limit = get_settings().patient_auth_per_ip_per_minute
    if limit <= 0:
        return
    wait = ratelimit.hit(("patient-auth", kind, client_ip(request)), limit)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Too many tries. Please wait a moment and try again.",
            headers={"Retry-After": str(wait)},
        )


# --- the signed-in patient --------------------------------------------------------------------


@dataclass(frozen=True)
class PatientSession:
    user: gotrue.AuthUser
    account: dict

    @property
    def clinic_id(self) -> str:
        return self.account["clinic_id"]

    @property
    def patient_id(self) -> str:
        return self.account["patient_id"]


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail=SIGN_IN_AGAIN)
    return authorization[7:].strip()


def _remembered(supabase, token: str) -> tuple[gotrue.AuthUser, datetime | None]:
    key = hashlib.sha256(token.encode()).hexdigest()
    t = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > t:
            return hit[1], hit[2]
    try:
        user = gotrue.user_for_token(token)
    except gotrue.InvalidSession as exc:
        raise HTTPException(status_code=401, detail=SIGN_IN_AGAIN) from exc
    started = None
    if user.session_id:
        row = supabase.rpc("patient_session_started", {"p_session_id": user.session_id}).execute()
        started = parse_ts(row.data["created_at"]) if row.data else None
    with _lock:
        if len(_cache) > _CACHE_MAX:
            _cache.clear()
        _cache[key] = (t + _CACHE_SECONDS, user, started)
    return user, started


def resolve_session(request: Request, authorization: str | None) -> PatientSession:
    """The signed-in patient and their account, whatever its status (see the module docstring)."""
    supabase = get_supabase()
    user, started = _remembered(supabase, _bearer(authorization))
    if user.account_type != gotrue.ACCOUNT_TYPE:
        raise HTTPException(status_code=403, detail=NOT_A_PATIENT)
    max_age = timedelta(days=get_settings().patient_session_max_age_days)
    if started is not None and clock.now() - started > max_age:
        raise HTTPException(status_code=401, detail=SIGN_IN_AGAIN)
    account = account_for_user(supabase, user.id)
    if account is None:
        raise HTTPException(status_code=403, detail=NOT_A_PATIENT)
    ratelimit.limit_patient_link(request, account["id"])
    return PatientSession(user=user, account=account)


def _require_status(session: PatientSession, allowed: tuple[str, ...]) -> PatientSession:
    status = session.account["status"]
    if status in allowed:
        return session
    if status == "locked":
        raise HTTPException(status_code=403, detail=LOCKED)
    if status == "off":
        raise HTTPException(status_code=403, detail=TURNED_OFF)
    raise HTTPException(status_code=403, detail=NOT_FINISHED)


def require_patient_account(
    request: Request, authorization: str | None = Header(default=None)
) -> PatientSession:
    """Any status: for 'what is my account's state?'."""
    return resolve_session(request, authorization)


def require_first_sign_in(
    request: Request, authorization: str | None = Header(default=None)
) -> PatientSession:
    """An invited patient finishing the first sign-in."""
    return _require_status(resolve_session(request, authorization), ("invited",))


def require_active_patient(
    request: Request, authorization: str | None = Header(default=None)
) -> PatientSession:
    """A patient whose first sign-in is done and whose access is on."""
    return _require_status(resolve_session(request, authorization), ("active",))


# --- the surgery-date check ---------------------------------------------------------------------


def check_identity(supabase, account: dict, surgery_date: date, *, actor: dict) -> dict:
    """Compare the date a patient typed with their latest care period's surgery date, and apply the
    outcome under the account's row lock (migration 18): {"outcome": "verified"} or
    {"outcome": "wrong", "attempts_left": n} or {"outcome": "locked"} or {"outcome": "not_allowed"}.

    The miss that locks the account is audited BEFORE it is applied. (If two misses race, one of
    them may leave a lock event the database then does not need: a dangling event, which
    reconcile lists as informational.)"""
    episode = episodes.latest_episode(supabase, account["clinic_id"], account["patient_id"])
    matches = episode is not None and episodes.parse_date(episode["surgery_date"]) == surgery_date
    maximum = get_settings().patient_identity_max_attempts
    if (
        not matches
        and account["status"] in LIVE
        and account["identity_failed_attempts"] + 1 >= maximum
    ):
        audit_first(
            supabase,
            clinic_id=account["clinic_id"],
            event_type="patient_account.identity_locked",
            ref_type="patient_account",
            ref_id=account["id"],
            actor=actor,
            payload={"account_id": account["id"], "patient_id": account["patient_id"]},
            patient_id=account["patient_id"],
        )
    return supabase.rpc(
        "record_patient_identity",
        {"p_account_id": account["id"], "p_matches": matches, "p_max": maximum},
    ).execute().data
