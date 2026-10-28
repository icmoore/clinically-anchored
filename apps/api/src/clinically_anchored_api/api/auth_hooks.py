"""Supabase Auth's "Send Email" hook: Supabase asks this endpoint to deliver every email it would
send (a magic link, a confirmation, a password reset...). This endpoint delivers none.

Why: patient sign-in codes are sent by the api through SES (core/mailer.py), and Supabase must
never send mail of its own. The anon key is public, so anyone can ask Supabase Auth to email a code
to any registered address; with this hook turned on that request reaches here and is dropped.
(Asking for a code this way can still replace a patient's pending code with one that is never
emailed; they ask the api for a new one. Nothing is leaked.)

Set up in the Supabase dashboard (Authentication -> Hooks -> Send Email): an HTTPS hook pointing at
`<api>/auth-hooks/send-email`, then copy its secret ("v1,whsec_...") into SEND_EMAIL_HOOK_SECRET on
Railway. Requests are verified with the Standard Webhooks signature; anything else is refused.
"""

import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from clinically_anchored_api.core.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth hooks"])

TOLERANCE_SECONDS = 300


def _key(secret: str) -> bytes:
    if secret.startswith("v1,"):
        secret = secret[3:]
    if secret.startswith("whsec_"):
        secret = secret[6:]
    return base64.b64decode(secret)


def verify_signature(secret: str, headers, body: bytes, *, now: float | None = None) -> bool:
    """Standard Webhooks: HMAC-SHA256 over "<id>.<timestamp>.<body>", compared in constant time to
    any of the signatures (space separated "v1,<base64>") in the header."""
    msg_id = headers.get("webhook-id")
    timestamp = headers.get("webhook-timestamp")
    signatures = headers.get("webhook-signature")
    if not (msg_id and timestamp and signatures):
        return False
    try:
        if abs((now if now is not None else time.time()) - int(timestamp)) > TOLERANCE_SECONDS:
            return False
        key = _key(secret)
    except ValueError:
        return False
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        hmac.compare_digest(expected, candidate.partition(",")[2])
        for candidate in signatures.split()
        if candidate.startswith("v1,")
    )


@router.post("/auth-hooks/send-email")
async def swallow_auth_email(request: Request) -> dict:
    secret = get_settings().send_email_hook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="The email hook is not configured.")
    body = await request.body()
    if not verify_signature(secret, request.headers, body):
        raise HTTPException(status_code=401, detail="Bad signature.")
    try:
        action = json.loads(body).get("email_data", {}).get("email_action_type")
    except ValueError:
        action = None
    # The kind of email only: never the address, and never the code or link in the payload.
    logger.warning("dropped a Supabase Auth email (%s): the api sends all patient mail", action)
    return {}
