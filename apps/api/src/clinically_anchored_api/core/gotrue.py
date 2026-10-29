"""Everything the api asks of Supabase Auth for patient accounts, behind a few functions.

Supabase Auth keeps the users and their sessions; the api never lets it send mail. A sign-in code
is made with the Admin API (`generate_link` returns the code and sends nothing), emailed by
core/mailer.py, and later exchanged for a session by `verify_code` on a throwaway client that
holds the public anon key. Tests replace this module's functions; nothing else talks to GoTrue
about patients.
"""

import base64
import json
import uuid
from dataclasses import dataclass

from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase

ACCOUNT_TYPE = "patient"
# "Forever" for a ban: a turned-off patient cannot refresh a session or ask for a new one.
_BANNED = "876000h"


class InvalidSession(Exception):
    """The access token is missing, forged, expired or its session was ended."""


class CodeRejected(Exception):
    """Supabase Auth did not accept the code (wrong, spent, or expired)."""


class NotConfigured(Exception):
    """SUPABASE_ANON_KEY (or the URL) is not set."""


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None
    account_type: str | None
    session_id: str | None


@dataclass(frozen=True)
class AuthSession:
    user_id: str
    access_token: str
    refresh_token: str
    expires_in: int
    expires_at: int | None


def placeholder_email() -> str:
    """A patient with no email still needs an Auth user. `.invalid` never receives mail."""
    return f"patient-{uuid.uuid4().hex}@no-email.invalid"


def _claims(jwt: str) -> dict:
    """The payload of a JWT that Supabase Auth has ALREADY verified (never use on its own)."""
    try:
        part = jwt.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def user_for_token(jwt: str) -> AuthUser:
    """The user a valid access token belongs to. Supabase checks the signature, the expiry and that
    the session still exists, so a turned-off patient's token stops here."""
    try:
        user = get_supabase().auth.get_user(jwt).user
    except Exception as exc:
        raise InvalidSession() from exc
    if user is None:
        raise InvalidSession()
    metadata = getattr(user, "app_metadata", None) or {}
    return AuthUser(
        id=user.id,
        email=getattr(user, "email", None),
        account_type=metadata.get("account_type"),
        session_id=_claims(jwt).get("session_id"),
    )


def ensure_patient_user(
    email: str, *, user_id: str | None = None, find_by_email=None
) -> str:
    """The Auth user for a patient, created if needed, with this email, marked as a patient and not
    banned. `user_id` is the account's existing user (re-invite); otherwise `find_by_email` looks
    one up (a returning address). Returns the user id."""
    admin = get_supabase().auth.admin
    attributes = {
        "email": email,
        "email_confirm": True,
        "app_metadata": {"account_type": ACCOUNT_TYPE},
        "ban_duration": "none",
    }
    if user_id is None and find_by_email is not None:
        user_id = find_by_email(email)
    if user_id is not None:
        return admin.update_user_by_id(user_id, attributes).user.id
    attributes.pop("ban_duration")
    return admin.create_user(attributes).user.id


def set_user_email(user_id: str, email: str) -> None:
    """Rename a user's sign-in address. Leaves the ban as it is (a turned-off account stays off)."""
    get_supabase().auth.admin.update_user_by_id(user_id, {"email": email, "email_confirm": True})


def user_email(user_id: str) -> str | None:
    return get_supabase().auth.admin.get_user_by_id(user_id).user.email


def ban(user_id: str) -> None:
    get_supabase().auth.admin.update_user_by_id(user_id, {"ban_duration": _BANNED})


def generate_code(email: str) -> str:
    """A fresh 6-digit sign-in code for this user. Sends nothing; replaces any earlier code."""
    link = get_supabase().auth.admin.generate_link({"type": "magiclink", "email": email})
    return link.properties.email_otp


def _anon_client():
    from supabase import create_client

    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise NotConfigured()
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def verify_code(email: str, code: str) -> AuthSession:
    """Exchange a code for a session. Raises CodeRejected if Supabase Auth refuses it."""
    client = _anon_client()
    try:
        result = client.auth.verify_otp({"email": email, "token": code, "type": "email"})
    except Exception as exc:
        raise CodeRejected() from exc
    session = result.session
    if session is None or result.user is None:
        raise CodeRejected()
    return AuthSession(
        user_id=result.user.id,
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
        expires_at=session.expires_at,
    )


def mint_session(email: str) -> AuthSession:
    """A session for this user without asking for their code: for a fallback link the api has
    already checked itself. The code is made and used in the same breath and never leaves here."""
    return verify_code(email, generate_code(email))
