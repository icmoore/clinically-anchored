"""Writing the patient contact/time log (data-catalogue D11, migration 8).

One place inserts a touchpoint and audits it (`touchpoint.logged`), whether a
clinician logged it by hand (api/touchpoints.py) or the api wrote it because a
clinician message was just sent (`log_message_sent`, called from the two send
paths). Audit payloads carry the touchpoint fields (hashed); metadata carries
only ids and the actor, like every other event."""

import logging

from clinically_anchored_api.core.audit import record_event

logger = logging.getLogger(__name__)

COLUMNS = (
    "id, clinic_id, patient_id, kind, source, occurred_at, duration_minutes, note, "
    "logged_by, created_at"
)

SYSTEM_ACTOR = {"type": "system"}


def audit_touchpoint(supabase, *, row: dict, actor: dict) -> None:
    """Record `touchpoint.logged` for a stored row. Raises AuditWriteError."""
    record_event(
        supabase,
        clinic_id=row["clinic_id"],
        event_type="touchpoint.logged",
        payload={
            "touchpoint_id": row["id"],
            "patient_id": row["patient_id"],
            "kind": row["kind"],
            "source": row["source"],
            "occurred_at": row["occurred_at"],
            "duration_minutes": row["duration_minutes"],
            "note": row["note"],
            "logged_by": row["logged_by"],
        },
        metadata={"ref_type": "touchpoint", "ref_id": row["id"], "actor": actor},
    )


def insert_touchpoint(supabase, values: dict) -> dict:
    """Insert one touchpoint row and return it. No audit; callers decide how a
    failed audit write is handled."""
    result = supabase.table("touchpoints").insert(values).execute()
    if not result.data:
        raise RuntimeError("touchpoint was not saved")
    return result.data[0]


def log_message_sent(supabase, *, clinic_id: str, message: dict) -> None:
    """Auto-log a `message` touchpoint for a clinician message that was just sent.

    Best-effort: the message is already sent, so a failure here is logged and
    swallowed rather than failing the request (the same stance reconcile.py takes
    toward a business write that outruns its audit write). A missed row is a gap
    in the contact log, not a lost message."""
    try:
        row = insert_touchpoint(
            supabase,
            {
                "clinic_id": clinic_id,
                "patient_id": message["patient_id"],
                "kind": "message",
                "source": "auto",
                "occurred_at": message["created_at"],
                "logged_by": None,
            },
        )
    except Exception:
        logger.exception("message %s sent but its touchpoint was not logged", message.get("id"))
        return
    try:
        audit_touchpoint(supabase, row=row, actor=SYSTEM_ACTOR)
    except Exception:  # AuditWriteError or anything else: still must not fail the send
        logger.exception("touchpoint %s logged but audit write failed", row.get("id"))
