"""Sending email: Amazon SES (`ca-central-1` unless SES_REGION says otherwise), and the outbox that
records every attempt.

What every send does, and nothing more:
  * runs in SES `ca-central-1` by default, and only the SES_REGION setting can change that -- never
    the ambient AWS_REGION, as with the Bedrock wrapper (core/ai.py), which fixes its endpoint the
    same way. A different region is a deliberate, logged override. Credentials come from boto3's
    standard chain (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in the process environment);
  * writes an `email_outbox` row first (status 'queued'), sends, then settles the row ('sent' with
    SES's message id, or 'failed' with an error CODE -- never a provider message, never the body);
  * for a DEMO clinic (clinics.is_demo) sends nothing: the rendered email, sign-in code included,
    goes into the outbox row (status 'demo') for the owner-only demo inbox. That is the only case
    in which a code is stored anywhere;
  * refuses to send real email to reserved test domains (example.com/.org/.net, *.test,
    *.invalid, *.example), in any clinic: the row is 'refused'. A demo clinic is not affected,
    since it sends nothing;
  * never raises for a delivery problem. It returns the outbox row and the caller reads `status`.
    A database error does raise: no row means we cannot say what happened.

Sign-in codes are never logged. Neither are recipient addresses: logs carry the outbox id.

Smoke test for the SES setup (sends one "[TEST]" invite, writes nothing to the database):

    python -m clinically_anchored_api.core.mailer --to you@your-verified-address.example
"""

import argparse
import logging
import re
import sys
from datetime import UTC, datetime
from email.utils import formataddr
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException

from clinically_anchored_api.core import email_templates
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.email_templates import RenderedEmail

logger = logging.getLogger(__name__)

DEFAULT_SES_REGION = "ca-central-1"

# Domains that never reach a person (RFC 2606 / 6761). example.com and friends include their
# subdomains.
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_SUFFIXES = (".test", ".invalid", ".example")

_ADDRESS = re.compile(r"^[^\s@<>(),;:\"\\\[\]]+@[^\s@<>(),;:\"\\\[\]]+\.[^\s@<>(),;:\"\\\[\]]+$")

_OUTBOX_COLUMNS = (
    "id, clinic_id, patient_id, recipient, template, status, provider_message_id, error, "
    "created_at, sent_at"
)


def normalize_address(address: str) -> str:
    """Trimmed and lower-cased: how an address is compared and stored."""
    return address.strip().lower()


def clean_email(value: str | None) -> str | None:
    """A clinic-entered email, normalised, or None if left empty. 422 if it is not an address."""
    if value is None or not value.strip():
        return None
    email = normalize_address(value)
    if not is_valid_address(email):
        raise HTTPException(status_code=422, detail="That is not a valid email address.")
    return email


def is_valid_address(address: str) -> bool:
    return len(address) <= 254 and _ADDRESS.match(address) is not None


def is_reserved_domain(address: str) -> bool:
    domain = address.rpartition("@")[2].lower().rstrip(".")
    if domain.endswith(_RESERVED_SUFFIXES):
        return True
    return any(domain == d or domain.endswith("." + d) for d in _RESERVED_DOMAINS)


def patient_sign_in_url() -> str | None:
    """Where the invite points: the web app's patient sign-in page. None if the web app's address
    is not configured (development falls back to localhost)."""
    settings = get_settings()
    base = settings.web_base_url or (
        "http://localhost:3000" if settings.environment == "development" else ""
    )
    return f"{base.rstrip('/')}/sign-in" if base else None


def ses_region() -> str:
    return get_settings().ses_region


@lru_cache
def _client(region: str):
    if region != DEFAULT_SES_REGION:
        logger.warning(
            "Email goes through SES in %s, not %s (SES_REGION is set). Remove SES_REGION to "
            "keep it in Canada.", region, DEFAULT_SES_REGION,
        )
    return boto3.client(
        "sesv2",
        region_name=region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5,
                      read_timeout=15),
    )


def _from_header() -> str | None:
    settings = get_settings()
    if not settings.ses_from_address:
        return None
    if settings.ses_from_name:
        return formataddr((settings.ses_from_name, settings.ses_from_address), charset="utf-8")
    return settings.ses_from_address


def deliver(to: str, email: RenderedEmail) -> str:
    """One SES send. Returns SES's message id. Raises ClientError / BotoCoreError, or
    ValueError("ses_not_configured") when no sender address is set."""
    sender = _from_header()
    if sender is None:
        raise ValueError("ses_not_configured")
    settings = get_settings()
    params = {
        "FromEmailAddress": sender,
        "Destination": {"ToAddresses": [to]},
        "Content": {
            "Simple": {
                "Subject": {"Data": email.subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": email.text, "Charset": "UTF-8"},
                    "Html": {"Data": email.html, "Charset": "UTF-8"},
                },
            }
        },
    }
    if settings.ses_configuration_set:
        params["ConfigurationSetName"] = settings.ses_configuration_set
    return _client(ses_region()).send_email(**params)["MessageId"]


def _error_code(exc: Exception) -> str:
    """A short code for the outbox: SES's error code, or the exception's class. Never the message
    (it can echo the address)."""
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code") or "ClientError")[:100]
    if isinstance(exc, ValueError) and str(exc) == "ses_not_configured":
        return "ses_not_configured"
    return type(exc).__name__[:100]


def _insert(supabase, row: dict) -> dict:
    return supabase.table("email_outbox").insert(row).execute().data[0]


def send_email(
    supabase,
    *,
    clinic_id: str,
    patient_id: str | None,
    to: str,
    template: str,
    code: str | None = None,
    outbox_id: str | None = None,
) -> dict:
    """Send (or, for a demo clinic, store) one email and return its outbox row. `code` is the
    sign-in code for the sign_in_code template. `outbox_id` lets a caller that audited the send
    first name the row it is about to cause. Read `row["status"]`: 'sent', 'demo', 'failed' or
    'refused'."""
    to = normalize_address(to)
    base = {"clinic_id": clinic_id, "patient_id": patient_id, "recipient": to, "template": template}
    if outbox_id:
        base["id"] = outbox_id

    if not is_valid_address(to):
        # The outbox only accepts a plausible length; garbage the clinic typed is not kept.
        shown = to if 3 <= len(to) <= 320 else "(invalid address)"
        return _insert(
            supabase, {**base, "recipient": shown, "status": "refused", "error": "invalid_address"}
        )

    clinic = (
        supabase.table("clinics")
        .select("name, office_phone, is_demo")
        .eq("id", clinic_id)
        .execute()
        .data
    )
    if not clinic:
        raise LookupError("clinic not found")
    clinic = clinic[0]

    sign_in_url = patient_sign_in_url() if template == email_templates.INVITE else None
    if template == email_templates.INVITE and sign_in_url is None:
        return _insert(supabase, {**base, "status": "failed", "error": "web_base_url_missing"})
    email = email_templates.render(
        template,
        clinic_name=clinic["name"],
        office_phone=clinic.get("office_phone"),
        sign_in_url=sign_in_url,
        code=code,
    )

    if clinic.get("is_demo"):
        return _insert(
            supabase,
            {
                **base,
                "status": "demo",
                "rendered_subject": email.subject,
                "rendered_text": email.text,
                "rendered_html": email.html,
            },
        )
    if is_reserved_domain(to):
        return _insert(supabase, {**base, "status": "refused", "error": "reserved_domain"})

    row = _insert(supabase, {**base, "status": "queued"})
    try:
        message_id = deliver(to, email)
    except Exception as exc:  # SES, botocore, or a missing sender: the row says which
        error = _error_code(exc)
        logger.warning("email %s (%s) failed: %s", row["id"], template, error)
        update = {"status": "failed", "error": error}
    else:
        update = {
            "status": "sent",
            "provider_message_id": message_id,
            "sent_at": datetime.now(UTC).isoformat(),
        }
    settled = (
        supabase.table("email_outbox").update(update).eq("id", row["id"]).execute().data
    )
    return settled[0] if settled else {**row, **update}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send one [TEST] invite through SES.")
    parser.add_argument("--to", required=True, help="a verified SES address (sandbox) or any")
    parser.add_argument("--dry-run", action="store_true", help="print the email; send nothing")
    args = parser.parse_args(argv)

    to = normalize_address(args.to)
    if not is_valid_address(to):
        print("That is not a valid email address.", file=sys.stderr)
        return 2
    if is_reserved_domain(to):
        print("Refusing to send to a reserved test domain.", file=sys.stderr)
        return 2
    base = email_templates.render(
        email_templates.INVITE,
        clinic_name="SES test",
        office_phone=None,
        sign_in_url=patient_sign_in_url() or "https://example.org/sign-in",
    )
    email = RenderedEmail(f"[TEST] {base.subject}", base.text, base.html)
    if args.dry_run:
        print(f"From: {_from_header()}\nTo: {to}\nSubject: {email.subject}\n\n{email.text}")
        return 0
    try:
        message_id = deliver(to, email)
    except Exception as exc:
        print(f"Send failed: {_error_code(exc)}", file=sys.stderr)
        return 1
    print(f"Sent. SES message id: {message_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
