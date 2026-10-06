"""On-demand AI summary of a patient's message thread.

Generated only when a clinician (or delegate) calls the endpoint -- never by a
background job and never because a message arrived. The model must cite the
message ids behind every claim; before anything is returned, every citation is
verified against the database (core/citations.py). A summary with an
unverifiable citation is discarded whole -- not shown, not repaired, no citation
silently dropped -- and the caller gets an error.

Nothing is stored: a verified summary is returned and audited as
`summary.generated` (model id and prompt version in readable metadata; the
summary text and its citations only in the hashed payload). If the audit write
fails the summary is not returned either, so no AI text is ever shown unrecorded."""

import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.api.messages import _require_patient_in_clinic
from clinically_anchored_api.core import ai
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.citations import CitationError, verify_summary
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.schemas import SummaryLineOut, SummaryOut

logger = logging.getLogger(__name__)
# Timings go through uvicorn's logger so they actually print (the app's own loggers sit at the
# default WARNING level). Durations and counts only -- never message or summary text.
timing_log = logging.getLogger("uvicorn.error.summaries")

router = APIRouter(tags=["summaries"])

_TEMPLATE = ("summary", "v3")
# The most recent messages the model is shown; older ones are left out and the
# response says so (`truncated`).
THREAD_LIMIT = 50


def _one_line(text: str) -> str:
    """Flatten a message so it stays on its own "[id] sender: text" line -- a
    message can't smuggle in extra lines that look like other messages."""
    return " ".join(text.split())


@router.post("/clinics/{clinic_id}/patients/{patient_id}/summaries", response_model=SummaryOut)
def generate_summary(
    clinic_id: str, patient_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> SummaryOut:
    started = time.perf_counter()
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)

    fetched = (
        supabase.table("messages")
        .select("id, sender, body")
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("created_at", desc=True)
        .limit(THREAD_LIMIT + 1)
        .execute()
        .data
    )
    if not fetched:
        raise HTTPException(status_code=422, detail="This patient has no messages to summarise.")
    truncated = len(fetched) > THREAD_LIMIT
    thread = list(reversed(fetched[:THREAD_LIMIT]))  # oldest first
    fetched_at = time.perf_counter()

    # The model sees short labels (M1, M2, ...), not 36-character ids: it has far less to
    # copy, and the server -- not the model -- maps each label back to the real message id.
    aliases = {f"M{n}": str(uuid.UUID(str(m["id"]))) for n, m in enumerate(thread, start=1)}
    template = ai.load_template(*_TEMPLATE)
    system, prompt = template.render(
        messages="\n".join(
            f"[M{n}] {m['sender']}: {_one_line(m['body'])}" for n, m in enumerate(thread, start=1)
        )
    )
    try:
        result = ai.generate(
            clinic_id=clinic_id,
            prompt_version=template.prompt_version,
            system=system,
            prompt=prompt,
        )
    except ai.AIDailyCapExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ai.AIInvocationError as exc:
        logger.error("summary generation failed for clinic %s: %s", clinic_id, exc)
        detail = "Summary generation failed. Try again."
        raise HTTPException(status_code=502, detail=detail) from exc
    generated_at_t = time.perf_counter()

    try:
        lines = verify_summary(
            supabase, clinic_id=clinic_id, patient_id=patient_id, raw=result.text, aliases=aliases
        )
    except CitationError as exc:
        # Log why (ids and structure only, never the text); show nothing.
        logger.error(
            "summary for patient %s discarded, citations not verifiable: %s", patient_id, exc
        )
        raise HTTPException(
            status_code=502,
            detail="The generated summary could not be verified against the thread, so it was "
            "discarded. Try again.",
        ) from exc

    verified_at = time.perf_counter()
    summary_id = str(uuid.uuid4())
    generated_at = datetime.now(UTC).isoformat()
    try:
        record_event(
            supabase,
            clinic_id=clinic_id,
            event_type="summary.generated",
            payload={
                "summary_id": summary_id,
                "patient_id": patient_id,
                "lines": [{"text": ln.text, "citations": list(ln.citations)} for ln in lines],
                "message_ids": [m["id"] for m in thread],
                "model_id": result.model_id,
                "prompt_version": result.prompt_version,
            },
            metadata={
                "ref_type": "summary",
                "ref_id": summary_id,
                "patient_id": patient_id,
                "actor": {"type": "member", "id": member.user_id, "role": member.role},
                "model_id": result.model_id,
                "prompt_version": result.prompt_version,
                "covers_messages": len(thread),
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            },
        )
    except AuditWriteError as exc:
        logger.error("summary %s generated but audit write failed: %s", summary_id, exc)
        raise HTTPException(
            status_code=500, detail="Summary could not be recorded, so it was not shown."
        ) from exc

    done = time.perf_counter()
    timing_log.info(
        "summary timings: messages=%d fetch=%.1fs model=%.1fs verify=%.1fs audit=%.1fs "
        "total=%.1fs input_tokens=%d output_tokens=%d model_retries=%d",
        len(thread), fetched_at - started, generated_at_t - fetched_at,
        verified_at - generated_at_t, done - verified_at, done - started,
        result.usage.input_tokens, result.usage.output_tokens, result.retry_attempts,
    )

    return SummaryOut(
        summary_id=summary_id,
        patient_id=patient_id,
        generated_at=generated_at,
        model_id=result.model_id,
        prompt_version=result.prompt_version,
        lines=[SummaryLineOut(text=ln.text, citations=list(ln.citations)) for ln in lines],
        covers_messages=len(thread),
        truncated=truncated,
    )
