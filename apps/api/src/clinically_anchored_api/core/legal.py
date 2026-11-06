"""The current Terms, Privacy Notice and consent wording: which versions, and what they hash to.

The documents live in apps/web/content/legal/ (config.json names the current versions). The api
deploys without that folder, so `scripts/sync_legal` writes `legal_current.json` next to this file
and a test fails if it has drifted. This module reads it and answers two questions:

  * what does a new acceptance have to say (the versions and hashes the api itself stores), and
  * does a patient's LATEST acceptance still match, or must they see "Before you start" again.

Changing a version in config.json, then running scripts/sync_legal, is all it takes to send every
patient back to the screen: nothing here is remembered per patient except their acceptance rows.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from clinically_anchored_api.core import consent_wording

_FILE = Path(__file__).with_name("legal_current.json")

ACCEPTED = "accepted"
DECLINED = "declined"
WITHDRAWN = "withdrawn"
ACTIONS = (ACCEPTED, DECLINED, WITHDRAWN)

COLUMNS = (
    "id, clinic_id, patient_id, episode_id, action, terms_version, privacy_version, "
    "consent_version, terms_sha256, privacy_sha256, consent_sha256, created_at"
)


@dataclass(frozen=True)
class Current:
    terms_version: str
    terms_sha256: str
    privacy_version: str
    privacy_sha256: str
    consent_version: str  # "<key>:<version>;..." for the consent sentences, ordered by key
    consent_sha256: str
    consent_items: dict[str, str]  # consent key -> that sentence's own version
    effective_date: str


def current() -> Current:
    data = json.loads(_FILE.read_text(encoding="utf-8"))
    items = dict(data["consent"]["items"])
    # The consent keys the api requires and the ones in the config are the same list.
    if sorted(items) != sorted(consent_wording.REQUIRED):
        raise RuntimeError("legal_current.json and consent_wording.REQUIRED disagree.")
    return Current(
        terms_version=data["terms"]["version"],
        terms_sha256=data["terms"]["sha256"],
        privacy_version=data["privacy"]["version"],
        privacy_sha256=data["privacy"]["sha256"],
        consent_version=data["consent"]["version"],
        consent_sha256=data["consent"]["sha256"],
        consent_items=items,
        effective_date=data["effective_date"],
    )


def matches_current(row: dict | None) -> bool:
    """True when this acceptance row is an ACCEPTANCE of exactly the current versions and text."""
    if row is None or row["action"] != ACCEPTED:
        return False
    now = current()
    return (
        row["terms_version"] == now.terms_version
        and row["terms_sha256"] == now.terms_sha256
        and row["privacy_version"] == now.privacy_version
        and row["privacy_sha256"] == now.privacy_sha256
        and row["consent_version"] == now.consent_version
        and row["consent_sha256"] == now.consent_sha256
    )


def latest(supabase, clinic_id: str, patient_id: str) -> dict | None:
    """The patient's newest acceptance row (accepted, declined or withdrawn), or None."""
    rows = (
        supabase.table("patient_acceptances")
        .select(COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def needs_acceptance(supabase, clinic_id: str, patient_id: str) -> bool:
    return not matches_current(latest(supabase, clinic_id, patient_id))
