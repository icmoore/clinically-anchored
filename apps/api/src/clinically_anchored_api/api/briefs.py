"""On-demand AI patient summary (the "brief") for the Physician Summary box.

Generated only when a clinician (or delegate) calls the endpoint -- never by a background job and
never because something new arrived. It is three labelled bullets ("The picture now", "Trends",
"Loose ends"), each citing the records it came from: M1... messages, C1... check-ins, T1...
calls/visits. The server maps labels back to real ids and verifies every one against the database,
scoped to this clinic and this patient (core/brief.py); a summary with anything wrong is discarded
whole, never repaired.

What the model is shown, and only this: the episode's computed state, the episode's check-in(s)
with their answers, own words and the red-flag rules they matched, the episode's message thread
(with categories) and the calls/visits logged since the episode began. It is NOT shown the
Conversation Summary or any earlier AI output.

A verified brief is audited as `patient_brief.generated` (model id, prompt version and counts in
readable metadata; the text and citations only in the hashed payload). If the audit write fails
nothing is shown. Only after it succeeds is the brief saved (migration 13, `patient_briefs`, one
row per generation, never edited), so a saved brief has always been audited. The newest one is read
back when a clinician opens the patient (`GET .../patient-brief/latest`), without calling the
model. If saving fails the brief is still returned and the failure is logged."""

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException

from clinically_anchored_api.api.messages import _require_patient_in_clinic
from clinically_anchored_api.core import ai, clock, episodes, rules
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.auth import ClinicMember, require_clinic_member
from clinically_anchored_api.core.brief import BriefBullet, make_aliases, verify_brief
from clinically_anchored_api.core.citations import CitationError
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.schedule import EpisodeState
from clinically_anchored_api.core.thread_text import speaker
from clinically_anchored_api.core.touchpoints import COLUMNS as TOUCHPOINT_COLUMNS
from clinically_anchored_api.schemas import (
    BriefBulletOut,
    BriefCountsOut,
    BriefSourceOut,
    PatientBriefOut,
)

logger = logging.getLogger(__name__)
# Timings go through uvicorn's logger so they actually print. Durations and counts only --
# never record or summary text.
timing_log = logging.getLogger("uvicorn.error.briefs")

router = APIRouter(tags=["patient-briefs"])

_TEMPLATE = ("patient_brief", "v1")
# The most recent records the model is shown; older ones are left out and the response says so.
MESSAGE_LIMIT = 50
CONTACT_LIMIT = 30
# Calls, visits and emails. The automatic "message" rows only repeat the messages themselves.
CONTACT_KINDS = ("call", "visit", "email")

_PHASES = {
    "locked_checkin": "the check-in is not done yet",
    "waiting": "waiting for the first message window",
    "open": "a message window is open",
    "used": "the patient has written in the open window",
    "closed": "the care period has ended",
}


def _one_line(text: str) -> str:
    """Keep a record on its own line: free text can't smuggle in lines that look like other
    records."""
    return " ".join(str(text).split())


def _local(value, tz) -> str:
    when = episodes.parse_ts(value)
    return when.astimezone(tz).strftime("%Y-%m-%d %H:%M") if when else "unknown time"


def _episode_text(episode: dict, state: EpisodeState) -> str:
    windows = "; ".join(
        f"{w.number} {w.status.value.replace('_', ' ')}"
        + (f" ({w.missed_reason.value.replace('_', ' ')})" if w.missed_reason else "")
        for w in state.windows
    )
    return (
        f"Surgery date {episode['surgery_date']}; post-op day {state.post_op_day}. "
        f"Status: {_PHASES[state.phase.value]}. Message windows: {windows}."
    )


def _check_in_text(label: str, row: dict, tz) -> str:
    answers = row.get("answers") if isinstance(row.get("answers"), dict) else {}
    own_words = answers.get("concerns_text")
    structured = {k: v for k, v in answers.items() if k != "concerns_text"}
    matched = rules.evaluate_red_flags(answers)[1]
    head = (
        f"[{label}] Check-in completed {_local(row['created_at'], tz)}"
        + (f" (post-op day {row['post_op_day']})" if row.get("post_op_day") is not None else "")
        + f". Red flag: {'yes' if row.get('is_red_flag') else 'no'}"
        + (f" (rules matched: {'; '.join(matched)})" if matched else "")
        + ". Reviewed by the clinic: "
        + (f"yes, {_local(row['reviewed_at'], tz)}" if row.get("reviewed_at") else "not yet")
        + "."
    )
    lines = [head, "Answers: " + json.dumps(structured, sort_keys=True, separators=(",", ":"))]
    if own_words:
        lines.append(f"Patient's own words: \"{_one_line(own_words)}\"")
    return "\n".join(lines)


def _contact_text(label: str, row: dict, tz) -> str:
    minutes = f", {row['duration_minutes']} min" if row.get("duration_minutes") else ""
    note = _one_line(row["note"]) if row.get("note") else "(no note)"
    return f"[{label}] {row['kind']}, {_local(row['occurred_at'], tz)}{minutes}: {note}"


def _counts_since(supabase, brief: dict) -> BriefCountsOut:
    """What has been added since the brief was generated: messages and check-ins of the same
    episode, and calls/visits/emails logged (not merely occurred) after it."""
    since = episodes.parse_ts(brief["generated_at"])

    def rows(table: str, columns: str, **filters) -> list[dict]:
        query = (
            supabase.table(table)
            .select(columns)
            .eq("clinic_id", brief["clinic_id"])
            .eq("patient_id", brief["patient_id"])
        )
        for column, value in filters.items():
            query = query.eq(column, value)
        return [r for r in query.execute().data if episodes.parse_ts(r["created_at"]) > since]

    return BriefCountsOut(
        messages=len(rows("messages", "id, created_at", episode_id=brief["episode_id"])),
        check_ins=len(rows("check_ins", "id, created_at", episode_id=brief["episode_id"])),
        touchpoints=len(
            [r for r in rows("touchpoints", "id, kind, created_at") if r["kind"] in CONTACT_KINDS]
        ),
    )


def _out(brief: dict, new_since: BriefCountsOut) -> PatientBriefOut:
    return PatientBriefOut(
        brief_id=str(brief["id"]),
        patient_id=str(brief["patient_id"]),
        episode_id=str(brief["episode_id"]),
        generated_at=str(brief["generated_at"]),
        model_id=brief["model_id"],
        prompt_version=brief["prompt_version"],
        bullets=[
            BriefBulletOut(
                label=b["label"],
                text=b["text"],
                sources=[BriefSourceOut(**s) for s in b["sources"]],
            )
            for b in brief["bullets"]
        ],
        covers=BriefCountsOut(**brief["covers"]),
        truncated=brief["truncated"],
        new_since=new_since,
    )


def _bullets_json(bullets: list[BriefBullet]) -> list[dict]:
    return [
        {
            "label": b.label,
            "text": b.text,
            "sources": [{"kind": s.kind, "id": s.id} for s in b.sources],
        }
        for b in bullets
    ]


def _save(supabase, row: dict) -> None:
    """Keep the brief that was just shown (already audited). Never fails the request."""
    try:
        supabase.table("patient_briefs").insert(row).execute()
    except Exception:
        logger.exception("patient brief generated and audited but not saved")


@router.get(
    "/clinics/{clinic_id}/patients/{patient_id}/patient-brief/latest",
    response_model=PatientBriefOut | None,
)
def latest_brief(
    clinic_id: str, patient_id: str, _member: ClinicMember = Depends(require_clinic_member)
) -> PatientBriefOut | None:
    """The newest saved brief for this patient, or null. It reads what was saved and counts what
    has come in since; it never calls the model and writes nothing."""
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)
    rows = (
        supabase.table("patient_briefs")
        .select(
            "id, clinic_id, patient_id, episode_id, bullets, covers, truncated, model_id, "
            "prompt_version, generated_at"
        )
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("generated_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    return _out(rows[0], _counts_since(supabase, rows[0]))


@router.post(
    "/clinics/{clinic_id}/patients/{patient_id}/patient-brief", response_model=PatientBriefOut
)
def generate_brief(
    clinic_id: str, patient_id: str, member: ClinicMember = Depends(require_clinic_member)
) -> PatientBriefOut:
    started = time.perf_counter()
    supabase = get_supabase()
    _require_patient_in_clinic(supabase, clinic_id, patient_id)

    episode = episodes.latest_episode(supabase, clinic_id, patient_id)
    if episode is None:
        raise HTTPException(status_code=422, detail="This patient has no care period yet.")
    state = episodes.state_for(supabase, episode, clock.now())
    tz = episodes.get_schedule(supabase, episode["schedule_version"]).tz

    check_ins = (
        supabase.table("check_ins")
        .select("id, answers, is_red_flag, post_op_day, created_at, reviewed_at")
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .eq("episode_id", episode["id"])
        .order("created_at")
        .execute()
        .data
    )
    fetched = (
        supabase.table("messages")
        .select("id, sender, body, origin, interval_no, category")
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .eq("episode_id", episode["id"])
        .order("created_at", desc=True)
        .limit(MESSAGE_LIMIT + 1)
        .execute()
        .data
    )
    if not check_ins and not fetched:
        raise HTTPException(
            status_code=422,
            detail="This patient has no check-in and no messages yet, so there is nothing to "
            "summarise.",
        )
    began = episodes.parse_ts(episode["created_at"])
    contacts_all = [
        r
        for r in (
            supabase.table("touchpoints")
            .select(TOUCHPOINT_COLUMNS)
            .eq("clinic_id", clinic_id)
            .eq("patient_id", patient_id)
            .in_("kind", list(CONTACT_KINDS))
            .order("occurred_at", desc=True)
            .limit(200)
            .execute()
            .data
        )
        if episodes.parse_ts(r["occurred_at"]) >= began
    ]
    truncated = len(fetched) > MESSAGE_LIMIT or len(contacts_all) > CONTACT_LIMIT
    thread = list(reversed(fetched[:MESSAGE_LIMIT]))  # oldest first
    contacts = list(reversed(contacts_all[:CONTACT_LIMIT]))
    fetched_at = time.perf_counter()

    aliases = make_aliases(
        [m["id"] for m in thread], [c["id"] for c in check_ins], [t["id"] for t in contacts]
    )
    template = ai.load_template(*_TEMPLATE)
    system, prompt = template.render(
        episode=_episode_text(episode, state),
        check_ins="\n".join(_check_in_text(f"C{n}", c, tz) for n, c in enumerate(check_ins, 1))
        or "(none yet)",
        messages="\n".join(
            f"[M{n}] {speaker(m)}: {_one_line(m['body'])}" for n, m in enumerate(thread, 1)
        )
        or "(none yet)",
        contacts="\n".join(_contact_text(f"T{n}", t, tz) for n, t in enumerate(contacts, 1))
        or "(none)",
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
        logger.error("patient brief generation failed for clinic %s: %s", clinic_id, exc)
        raise HTTPException(
            status_code=502, detail="Summary generation failed. Try again."
        ) from exc
    generated_at_t = time.perf_counter()

    try:
        bullets = verify_brief(
            supabase, clinic_id=clinic_id, patient_id=patient_id, raw=result.text, aliases=aliases
        )
    except CitationError as exc:
        # Log why (structure and ids only, never the text); show nothing.
        logger.error("patient brief for patient %s discarded, not verifiable: %s", patient_id, exc)
        raise HTTPException(
            status_code=502,
            detail="The generated summary could not be verified against the record, so it was "
            "discarded. Try again.",
        ) from exc

    verified_at = time.perf_counter()
    brief_id = str(uuid.uuid4())
    generated_at = clock.now().isoformat()
    covers = {"messages": len(thread), "check_ins": len(check_ins), "touchpoints": len(contacts)}
    try:
        record_event(
            supabase,
            clinic_id=clinic_id,
            event_type="patient_brief.generated",
            payload={
                "brief_id": brief_id,
                "patient_id": patient_id,
                "episode_id": episode["id"],
                "bullets": _bullets_json(bullets),
                "message_ids": [m["id"] for m in thread],
                "check_in_ids": [c["id"] for c in check_ins],
                "touchpoint_ids": [t["id"] for t in contacts],
                "model_id": result.model_id,
                "prompt_version": result.prompt_version,
            },
            metadata={
                "ref_type": "patient_brief",
                "ref_id": brief_id,
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
        logger.error("patient brief %s generated but audit write failed: %s", brief_id, exc)
        raise HTTPException(
            status_code=500, detail="Summary could not be recorded, so it was not shown."
        ) from exc

    row = {
        "id": brief_id,
        "clinic_id": clinic_id,
        "patient_id": patient_id,
        "episode_id": episode["id"],
        "bullets": _bullets_json(bullets),
        "covers": covers,
        "truncated": truncated,
        "model_id": result.model_id,
        "prompt_version": result.prompt_version,
        "generated_by": member.user_id,
        "generated_at": generated_at,
    }
    _save(supabase, row)

    done = time.perf_counter()
    timing_log.info(
        "patient brief timings: messages=%d check_ins=%d contacts=%d fetch=%.1fs model=%.1fs "
        "verify=%.1fs audit=%.1fs total=%.1fs input_tokens=%d output_tokens=%d model_retries=%d",
        covers["messages"], covers["check_ins"], covers["touchpoints"], fetched_at - started,
        generated_at_t - fetched_at, verified_at - generated_at_t, done - verified_at,
        done - started, result.usage.input_tokens, result.usage.output_tokens,
        result.retry_attempts,
    )
    return _out(row, BriefCountsOut())
