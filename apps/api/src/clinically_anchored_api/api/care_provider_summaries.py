"""On-demand AI care provider summary for the Physician Summary box.

A short plain paragraph the physician can pass on to the patient's other care provider. Generated
only when a clinic member calls the endpoint -- never by a background job and never because
something new arrived. Unlike the AI patient summary (api/briefs.py) it has no bullets and no
citations, so there is nothing to verify against the database; the server checks its shape instead
(core/provider_summary.py) and discards anything that is empty, too long, several paragraphs or
carries a record label. Nothing is repaired.

What the model is shown, and only this: the procedure and the episode's computed state, the
episode's check-in(s) with their answers, own words and the red-flag rules they matched, the
episode's message thread and the calls/visits logged since the episode began (api/briefs.py
`gather_record`, the same read the AI patient summary uses). It is NOT shown the Conversation
Summary, the AI patient summary or any earlier AI output.

A physician may edit the text before passing it on (`POST .../{summary_id}/edit`). The AI's row is
never changed: the edit is a new row in `care_provider_summary_edits` (migration 15) naming its
summary, audited as `care_provider_summary.edited` before it is saved, and the newest edit is what
is shown and copied. Only the newest summary of a patient can be edited.

A checked summary is audited as `care_provider_summary.generated` (model id, prompt version and
counts in readable metadata; the text only in the hashed payload). If the audit write fails nothing
is shown. Only after it succeeds is the summary saved (migration 14, `care_provider_summaries`, one
row per generation, never edited), so a saved summary has always been audited. The newest one is
read back when a clinician opens the patient (`GET .../care-provider-summary/latest`), without
calling the model. If saving fails the summary is still returned and the failure is logged."""

import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.api.briefs import (
    _PHASES,
    _check_in_text,
    _contact_text,
    _counts_since,
    _one_line,
    gather_record,
)
from clinically_anchored_api.api.messages import _require_patient_in_clinic
from clinically_anchored_api.core import ai, clock
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.provider_summary import SummaryFormatError, validate_summary
from clinically_anchored_api.core.thread_text import speaker
from clinically_anchored_api.schemas import (
    BriefCountsOut,
    CareProviderSummaryEdit,
    CareProviderSummaryOut,
)

logger = logging.getLogger(__name__)
# Timings go through uvicorn's logger so they actually print. Durations and counts only --
# never record or summary text.
timing_log = logging.getLogger("uvicorn.error.care_provider_summaries")

router = APIRouter(tags=["care-provider-summaries"])

_TEMPLATE = ("care_provider_summary", "v1")


def _procedure_name(supabase, episode: dict) -> str:
    rows = (
        supabase.table("procedures")
        .select("id, name")
        .eq("id", episode["procedure_id"])
        .execute()
        .data
    )
    return rows[0]["name"] if rows else "unknown procedure"


def _episode_text(supabase, episode: dict, state) -> str:
    return (
        f"Procedure: {_procedure_name(supabase, episode)}. Surgery date {episode['surgery_date']}; "
        f"post-op day {state.post_op_day}. Status of the care period: "
        f"{_PHASES[state.phase.value]}."
    )


def _out(row: dict, new_since: BriefCountsOut, edit: dict | None = None) -> CareProviderSummaryOut:
    return CareProviderSummaryOut(
        summary_id=str(row["id"]),
        patient_id=str(row["patient_id"]),
        episode_id=str(row["episode_id"]),
        generated_at=str(row["generated_at"]),
        model_id=row["model_id"],
        prompt_version=row["prompt_version"],
        text=row["summary_text"],
        covers=BriefCountsOut(**row["covers"]),
        truncated=row["truncated"],
        new_since=new_since,
        edited_text=edit["edited_text"] if edit else None,
        edited_at=str(edit["edited_at"]) if edit else None,
    )


def _newest_edit(supabase, summary_id: str) -> dict | None:
    rows = (
        supabase.table("care_provider_summary_edits")
        .select("id, summary_id, edited_text, edited_at")
        .eq("summary_id", summary_id)
        .order("edited_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def _newest_summary(supabase, clinic_id: str, patient_id: str) -> dict | None:
    rows = (
        supabase.table("care_provider_summaries")
        .select(
            "id, clinic_id, patient_id, episode_id, summary_text, covers, truncated, model_id, "
            "prompt_version, generated_at"
        )
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("generated_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def _save(supabase, row: dict) -> None:
    """Keep the summary that was just shown (already audited). Never fails the request."""
    try:
        supabase.table("care_provider_summaries").insert(row).execute()
    except Exception:
        logger.exception("care provider summary generated and audited but not saved")


@router.get(
    "/clinics/{clinic_id}/patients/{patient_id}/care-provider-summary/latest",
    response_model=CareProviderSummaryOut | None,
)
def latest_summary(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> CareProviderSummaryOut | None:
    """The newest saved care provider summary for this patient, or null. It reads what was saved
    and counts what has come in since; it never calls the model and writes nothing."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    row = _newest_summary(supabase, clinic_id, patient_id)
    if row is None:
        return None
    return _out(row, _counts_since(supabase, row), _newest_edit(supabase, row["id"]))


@router.post(
    "/clinics/{clinic_id}/patients/{patient_id}/care-provider-summary",
    response_model=CareProviderSummaryOut,
)
def generate_summary(
    clinic_id: str, patient_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> CareProviderSummaryOut:
    started = time.perf_counter()
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)

    record = gather_record(supabase, clinic_id, patient_id)
    episode, tz = record.episode, record.tz
    fetched_at = time.perf_counter()

    template = ai.load_template(*_TEMPLATE)
    system, prompt = template.render(
        episode=_episode_text(supabase, episode, record.state),
        check_ins="\n".join(
            _check_in_text(f"C{n}", c, tz) for n, c in enumerate(record.check_ins, 1)
        )
        or "(none yet)",
        messages="\n".join(
            f"[M{n}] {speaker(m)}: {_one_line(m['body'])}" for n, m in enumerate(record.thread, 1)
        )
        or "(none yet)",
        contacts="\n".join(
            _contact_text(f"T{n}", t, tz) for n, t in enumerate(record.contacts, 1)
        )
        or "(none)",
    )
    try:
        result = ai.generate(
            clinic_id=clinic_id,
            prompt_version=template.prompt_version,
            system=system,
            prompt=prompt,
            max_tokens=512,
        )
    except ai.AIDailyCapExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ai.AIInvocationError as exc:
        logger.error("care provider summary failed for clinic %s: %s", clinic_id, exc)
        raise HTTPException(
            status_code=502, detail="Summary generation failed. Try again."
        ) from exc
    generated_at_t = time.perf_counter()

    try:
        text = validate_summary(result.text)
    except SummaryFormatError as exc:
        # Log why (never the text); show nothing.
        logger.error("care provider summary for patient %s discarded: %s", patient_id, exc)
        raise HTTPException(
            status_code=502,
            detail="The generated summary was not in the expected form, so it was discarded. "
            "Try again.",
        ) from exc

    checked_at = time.perf_counter()
    summary_id = str(uuid.uuid4())
    generated_at = clock.now().isoformat()
    covers = {
        "messages": len(record.thread),
        "check_ins": len(record.check_ins),
        "touchpoints": len(record.contacts),
    }
    try:
        record_event(
            supabase,
            clinic_id=clinic_id,
            event_type="care_provider_summary.generated",
            payload={
                "summary_id": summary_id,
                "patient_id": patient_id,
                "episode_id": episode["id"],
                "text": text,
                "message_ids": [m["id"] for m in record.thread],
                "check_in_ids": [c["id"] for c in record.check_ins],
                "touchpoint_ids": [t["id"] for t in record.contacts],
                "model_id": result.model_id,
                "prompt_version": result.prompt_version,
            },
            metadata={
                "ref_type": "care_provider_summary",
                "ref_id": summary_id,
                "patient_id": patient_id,
                "episode_id": episode["id"],
                "actor": {"type": "member", "id": member.user_id, "role": member.role},
                "model_id": result.model_id,
                "prompt_version": result.prompt_version,
                "covers_messages": covers["messages"],
                "covers_check_ins": covers["check_ins"],
                "covers_touchpoints": covers["touchpoints"],
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
            },
        )
    except AuditWriteError as exc:
        logger.error(
            "care provider summary %s generated but audit write failed: %s", summary_id, exc
        )
        raise HTTPException(
            status_code=500, detail="Summary could not be recorded, so it was not shown."
        ) from exc

    row = {
        "id": summary_id,
        "clinic_id": clinic_id,
        "patient_id": patient_id,
        "episode_id": episode["id"],
        "summary_text": text,
        "covers": covers,
        "truncated": record.truncated,
        "model_id": result.model_id,
        "prompt_version": result.prompt_version,
        "generated_by": member.user_id,
        "generated_at": generated_at,
    }
    _save(supabase, row)

    done = time.perf_counter()
    timing_log.info(
        "care provider summary timings: messages=%d check_ins=%d contacts=%d fetch=%.1fs "
        "model=%.1fs check=%.1fs audit=%.1fs total=%.1fs input_tokens=%d output_tokens=%d "
        "model_retries=%d",
        covers["messages"], covers["check_ins"], covers["touchpoints"], fetched_at - started,
        generated_at_t - fetched_at, checked_at - generated_at_t, done - checked_at,
        done - started, result.usage.input_tokens, result.usage.output_tokens,
        result.retry_attempts,
    )
    return _out(row, BriefCountsOut())


@router.post(
    "/clinics/{clinic_id}/patients/{patient_id}/care-provider-summary/{summary_id}/edit",
    response_model=CareProviderSummaryOut,
)
def edit_summary(
    clinic_id: str,
    patient_id: str,
    summary_id: str,
    body: CareProviderSummaryEdit,
    member: ClinicMember = Depends(require_clinic_member),
) -> CareProviderSummaryOut:
    """Save the physician's edited text. The AI's text is kept as it was; the edit is a new row
    that names it. The text is the physician's own, so it is not shape-checked like the AI's, only
    kept non-blank and within the length limit."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    row = _newest_summary(supabase, clinic_id, patient_id)
    if row is None or str(row["id"]) != summary_id:
        # Either this id is not one of this patient's summaries, or a newer one exists.
        known = (
            supabase.table("care_provider_summaries")
            .select("id")
            .eq("clinic_id", clinic_id)
            .eq("patient_id", patient_id)
            .eq("id", summary_id)
            .execute()
            .data
        )
        if not known:
            raise HTTPException(status_code=404, detail="Summary not found.")
        raise HTTPException(
            status_code=409,
            detail="A newer summary exists for this patient. Reload the page to see it.",
        )

    text = body.text.strip()
    previous = _newest_edit(supabase, summary_id)
    shown = previous["edited_text"] if previous else row["summary_text"]
    if text == shown.strip():
        raise HTTPException(
            status_code=422, detail="Nothing has changed, so there is nothing to save."
        )

    edit_id = str(uuid.uuid4())
    edited_at = clock.now().isoformat()
    try:
        record_event(
            supabase,
            clinic_id=clinic_id,
            event_type="care_provider_summary.edited",
            payload={
                "edit_id": edit_id,
                "summary_id": summary_id,
                "patient_id": patient_id,
                "episode_id": row["episode_id"],
                "text": text,
            },
            metadata={
                "ref_type": "care_provider_summary_edit",
                "ref_id": edit_id,
                "summary_id": summary_id,
                "patient_id": patient_id,
                "episode_id": row["episode_id"],
                "actor": {"type": "member", "id": member.user_id, "role": member.role},
            },
        )
    except AuditWriteError as exc:
        logger.error("care provider summary edit %s: audit write failed: %s", edit_id, exc)
        raise HTTPException(
            status_code=500, detail="The edit could not be recorded, so it was not saved."
        ) from exc

    edit = {
        "id": edit_id,
        "clinic_id": clinic_id,
        "patient_id": patient_id,
        "summary_id": summary_id,
        "edited_text": text,
        "edited_by": member.user_id,
        "edited_at": edited_at,
    }
    try:
        supabase.table("care_provider_summary_edits").insert(edit).execute()
    except Exception as exc:
        # Unlike a generated summary, the edit IS the point of this call: do not pretend it saved.
        logger.exception("care provider summary edit %s audited but not saved", edit_id)
        raise HTTPException(
            status_code=500, detail="The edit could not be saved. Try again."
        ) from exc
    return _out(row, _counts_since(supabase, row), edit)
