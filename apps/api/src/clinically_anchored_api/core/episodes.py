"""Episodes: loading them, computing their state, and the audit events around them.

An episode is one patient's post-op period (migration 9). Which interval is open, whether
it is used, what was missed -- all of that is COMPUTED here by core/schedule.py from the
stored facts; only the facts are in the database. This module gathers the facts (the
schedule version, which intervals have a message, whether a doctor's message awaits a
reply) and hands them to the engine.

A naturally ended episode is closed lazily: the first request that notices it asks the
database to close it (`settle`), and only the call that actually changes the row audits
`episode.closed`. The engine reports it closed either way, so nothing waits for that write.
"""

import logging
from collections import defaultdict
from datetime import date, datetime

from fastapi import HTTPException

from clinically_anchored_api.core import clock
from clinically_anchored_api.core.audit import AuditWriteError, record_event
from clinically_anchored_api.core.extension import ExtensionStatus, extension_status
from clinically_anchored_api.core.schedule import EpisodeState, Schedule, episode_state

logger = logging.getLogger(__name__)

COLUMNS = (
    "id, clinic_id, patient_id, procedure_id, surgery_date, schedule_version, "
    "checkin_completed_at, link_generation, closed_at, close_reason, closed_by, "
    "created_by, created_at, kind, extends_episode_id, started_at"
)
SYSTEM_ACTOR = {"type": "system"}
_CHUNK = 50  # episodes per batched query (keeps clear of the 1000-row response cap)


def parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def member_actor(member) -> dict:
    return {"type": "member", "id": member.user_id, "role": member.role}


# --- loading --------------------------------------------------------------------------------


def get_episode(supabase, episode_id: str, *, clinic_id: str | None = None) -> dict | None:
    query = supabase.table("episodes").select(COLUMNS).eq("id", episode_id)
    if clinic_id:
        query = query.eq("clinic_id", clinic_id)
    rows = query.execute().data
    return rows[0] if rows else None


def latest_episode(supabase, clinic_id: str, patient_id: str) -> dict | None:
    rows = (
        supabase.table("episodes")
        .select(COLUMNS)
        .eq("clinic_id", clinic_id)
        .eq("patient_id", patient_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def get_schedule(supabase, version: int) -> Schedule:
    rows = supabase.table("message_schedules").select("*").eq("version", version).execute().data
    if not rows:
        raise LookupError(f"schedule version {version} does not exist")
    return Schedule.from_row(rows[0])


def default_schedule(supabase) -> Schedule:
    """The schedule a NEW episode gets: the newest active version (highest `version` with
    `active = true`, migration 11). Existing episodes never move: each keeps the version it
    recorded. Raises LookupError if no version is active."""
    rows = (
        supabase.table("message_schedules")
        .select("*")
        .eq("active", True)
        .eq("kind", "main")
        .order("version", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise LookupError("no active message schedule exists")
    return Schedule.from_row(rows[0])


def default_extension_schedule(supabase) -> Schedule | None:
    """The extension schedule a NEW request is made under: the newest active one (migration 16),
    or None when follow-up extensions are switched off."""
    rows = (
        supabase.table("message_schedules")
        .select("*")
        .eq("active", True)
        .eq("kind", "extension")
        .order("version", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return Schedule.from_row(rows[0]) if rows else None


def schedules_for(supabase, versions: set[int]) -> dict[int, Schedule]:
    rows = (
        supabase.table("message_schedules").select("*").in_("version", sorted(versions))
        .execute().data
    )
    return {r["version"]: Schedule.from_row(r) for r in rows}


# --- state ----------------------------------------------------------------------------------


def _facts(supabase, episode_ids: list[str]) -> dict[str, tuple[set[int], bool]]:
    """episode id -> (intervals with a message, whether a doctor's message awaits a reply)."""
    messages: dict[str, list[dict]] = defaultdict(list)
    for i in range(0, len(episode_ids), _CHUNK):
        rows = (
            supabase.table("messages")
            .select("id, episode_id, origin, interval_no, reply_to_message_id, created_at")
            .in_("episode_id", episode_ids[i : i + _CHUNK])
            .order("created_at")
            .execute()
            .data
        )
        for row in rows:
            messages[row["episode_id"]].append(row)

    facts = {}
    for eid in episode_ids:
        rows = messages.get(eid, [])
        used = {r["interval_no"] for r in rows if r["origin"] == "scheduled"}
        doctors = [r for r in rows if r["origin"] == "clinician"]
        replied = {r["reply_to_message_id"] for r in rows if r["origin"] == "reply"}
        # The reply answers the LATEST doctor message; older unanswered ones are superseded.
        latest = max(doctors, key=lambda r: (r["created_at"], r["id"]), default=None)
        facts[eid] = (used, latest is not None and latest["id"] not in replied)
    return facts


def states_for(
    supabase, episodes: list[dict], now: datetime, schedules: dict[int, Schedule] | None = None
) -> dict[str, EpisodeState]:
    """Engine state for each episode (batched). Reads only; nothing is closed here."""
    if not episodes:
        return {}
    schedules = schedules or schedules_for(supabase, {e["schedule_version"] for e in episodes})
    facts = _facts(supabase, [e["id"] for e in episodes])
    return {
        e["id"]: episode_state(
            surgery_date=parse_date(e["surgery_date"]),
            schedule=schedules[e["schedule_version"]],
            now=now,
            checkin_completed_at=parse_ts(e["checkin_completed_at"]),
            closed_at=parse_ts(e["closed_at"]),
            close_reason=e["close_reason"],
            used_intervals=facts[e["id"]][0],
            reply_available=facts[e["id"]][1],
            started_at=parse_ts(e.get("started_at")),
        )
        for e in episodes
    }


def state_for(supabase, episode: dict, now: datetime) -> EpisodeState:
    return states_for(supabase, [episode], now)[episode["id"]]


# --- extensions: chains of episodes and the request an ended episode may have ----------------


def extended_by(supabase, episode_id: str) -> dict | None:
    """The extension episode that continues this one, if a request was accepted."""
    rows = (
        supabase.table("episodes").select(COLUMNS).eq("extends_episode_id", episode_id)
        .execute().data
    )
    return rows[0] if rows else None


def resolve_tail(supabase, episode: dict) -> dict:
    """Follow accepted extensions to the newest episode of the chain. A patient's original link
    names the first episode; this is the one it opens."""
    seen = {episode["id"]}
    while (nxt := extended_by(supabase, episode["id"])) is not None and nxt["id"] not in seen:
        episode = nxt
        seen.add(episode["id"])
    return episode


def chain(supabase, episode: dict) -> list[dict]:
    """Every episode of the chain this one belongs to, first to newest (just this one if it was
    never extended and extends nothing)."""
    root = episode
    seen = {root["id"]}
    while root.get("extends_episode_id") and root["extends_episode_id"] not in seen:
        parent = get_episode(supabase, root["extends_episode_id"])
        if parent is None:
            break
        root = parent
        seen.add(root["id"])
    out = [root]
    while (nxt := extended_by(supabase, out[-1]["id"])) is not None and nxt["id"] not in seen:
        out.append(nxt)
        seen.add(nxt["id"])
    return out


def tag_extension_windows(supabase, messages: list[dict]) -> list[dict]:
    """Mark each message that belongs to a follow-up extension with `extension_no` (1, 2, ...),
    so a prompt can tell "extension window 3" from the main period's window 3. Needs `episode_id`
    on each message; adds one query only when an extension exists among them."""
    ids = sorted({m["episode_id"] for m in messages if m.get("episode_id")})
    if not ids:
        return messages
    rows = (
        supabase.table("episodes").select(COLUMNS).in_("id", ids).eq("kind", "extension")
        .execute().data
    )
    numbers = {r["id"]: len(chain(supabase, r)) - 1 for r in rows}
    return [{**m, "extension_no": numbers[m["episode_id"]]} if m.get("episode_id") in numbers
            else m for m in messages]


REQUEST_COLUMNS = (
    "id, clinic_id, patient_id, episode_id, schedule_version, request_message_id, requested_at, "
    "status, decided_by, decided_at, decision_message, decision_entry_id, extension_episode_id"
)


def request_for(supabase, episode_id: str) -> dict | None:
    """The extension request made about this (ended) episode, if any: there is at most one."""
    rows = (
        supabase.table("extension_requests").select(REQUEST_COLUMNS)
        .eq("episode_id", episode_id).execute().data
    )
    return rows[0] if rows else None


def ended_context(
    supabase, episode: dict, state: EpisodeState, now: datetime
) -> tuple[dict | None, ExtensionStatus]:
    """For an episode that may have ended by schedule: its request (if any) and where it stands
    on follow-up. A closed episode only; an open one is NOT_APPLICABLE."""
    request = request_for(supabase, episode["id"]) if state.closed else None
    if request is not None:
        extension = get_schedule(supabase, request["schedule_version"])
    else:
        extension = default_extension_schedule(supabase) if state.closed else None
    return request, extension_status(state=state, request=request, extension=extension, now=now)


# --- closing --------------------------------------------------------------------------------


def audit_episode(
    supabase, *, event_type: str, episode: dict, actor: dict, payload: dict | None = None
) -> None:
    """Record one episode event. Raises AuditWriteError."""
    record_event(
        supabase,
        clinic_id=episode["clinic_id"],
        event_type=event_type,
        payload={
            "episode_id": episode["id"],
            "patient_id": episode["patient_id"],
            **(payload or {}),
        },
        metadata={
            "ref_type": "episode",
            "ref_id": episode["id"],
            "patient_id": episode["patient_id"],
            "schedule_version": episode["schedule_version"],
            "actor": actor,
        },
    )


def close(
    supabase, episode: dict, *, reason: str, closed_by: str | None, closed_at: datetime | None,
    actor: dict,
) -> tuple[dict, bool]:
    """Ask the database to close the episode (idempotent). Returns (row, changed); audits
    `episode.closed` only when this call is the one that changed it. Raises AuditWriteError
    after the close is already committed, like every other write."""
    out = (
        supabase.rpc(
            "close_episode",
            {
                "p_clinic_id": episode["clinic_id"],
                "p_episode_id": episode["id"],
                "p_reason": reason,
                "p_closed_by": closed_by,
                "p_closed_at": closed_at.isoformat() if closed_at else None,
            },
        )
        .execute()
        .data
    )
    row, changed = out["episode"], out["changed"]
    if changed:
        audit_episode(
            supabase,
            event_type="episode.closed",
            episode=row,
            actor=actor,
            payload={
                "reason": row["close_reason"],
                "closed_at": row["closed_at"],
                "closed_by": row["closed_by"],
            },
        )
    return row, changed


def settle(supabase, episode: dict, state: EpisodeState) -> dict:
    """If the schedule has run out but the episode is not marked closed, close it now
    (effective at the instant the last interval ended). Never raises: the patient's
    request must not fail because of an audit hiccup; reconcile finds a missing event."""
    if episode["closed_at"] is not None or state.close_reason != "schedule_complete":
        return episode
    try:
        row, _changed = close(
            supabase, episode, reason="schedule_complete", closed_by=None,
            closed_at=state.closed_at, actor=SYSTEM_ACTOR,
        )
        return row
    except AuditWriteError as exc:
        logger.error("episode %s closed but audit write failed: %s", episode["id"], exc)
    except Exception:
        logger.exception("could not settle episode %s", episode["id"])
    refreshed = get_episode(supabase, episode["id"])
    return refreshed or episode


def current_state(
    supabase, episode: dict, now: datetime | None = None
) -> tuple[dict, EpisodeState]:
    """The episode and its state, settling a naturally ended one first."""
    now = now or clock.now()
    state = state_for(supabase, episode, now)
    return settle(supabase, episode, state), state


def open_episode_or_409(
    supabase, clinic_id: str, patient_id: str, now: datetime | None = None
) -> tuple[dict, EpisodeState]:
    """The patient's open episode (settling a naturally ended one first), or a 409: a
    clinician message into a closed episode could never be seen, because the link is dead."""
    episode = latest_episode(supabase, clinic_id, patient_id)
    if episode is not None:
        episode, state = current_state(supabase, episode, now)
        if not state.closed:
            return episode, state
    raise HTTPException(status_code=409, detail="This patient has no open care period.")
