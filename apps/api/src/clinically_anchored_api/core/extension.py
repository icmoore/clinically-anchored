"""Follow-up extensions: what the patient may do after the care period ends, as a pure function.

Nothing here reads a clock or a database; `extension_status` takes `now`, the ended episode's state
and the request (if any), so every rule is tested with a pinned instant. The numbers (how long
the patient has to ask) are data: they come from the extension schedule row (migration 16,
`request_window_days`). The numbers and the wording are PLACEHOLDERS pending the surgeon.

The rules (docs/api/README.md has them in full):
  * Only an episode that ended BY SCHEDULE can ask: not a discharge, not while the term still runs.
  * The patient has `request_window_days` after the end to ask, one request per episode. Until
    then the link works in a limited "ended" mode: the ended message, the request form, and the
    request's status and outcome. Nothing else.
  * Pending: the link stays in ended mode however long the clinic takes.
  * Declined (final for this episode): the outcome stays readable until the later of the end of
    the request window and `request_window_days` after the decision, so a decision made late is
    still seen. No form.
  * Accepted: a new episode (core/schedule.py, kind "extension") takes over; this module is about
    the episode that ended.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from clinically_anchored_api.core.schedule import EpisodeState, Schedule

REASON_MAX = 500  # characters; also checked by migration 16
DECISION_MESSAGE_MAX = 5000  # the clinician's words; same limit as a message


class Standing(StrEnum):
    NOT_APPLICABLE = "not_applicable"  # still running, discharged, or no extension schedule exists
    ELIGIBLE = "eligible"  # ended by schedule; may ask until `request_by`
    PENDING = "pending"  # asked; the clinic has not decided
    DECLINED = "declined"  # decided no; final for this episode
    ACCEPTED = "accepted"  # decided yes; a new episode took over
    LAPSED = "lapsed"  # nobody asked in time, or the outcome has been visible long enough


@dataclass(frozen=True)
class ExtensionStatus:
    standing: Standing
    request_by: datetime | None  # last moment to ask (UTC), when ended by schedule
    link_open_until: datetime | None  # None = no limit (a pending request)

    @property
    def can_request(self) -> bool:
        return self.standing is Standing.ELIGIBLE

    @property
    def link_works(self) -> bool:
        """Does the patient link still open the limited "ended" screen?"""
        return self.standing in (
            Standing.ELIGIBLE,
            Standing.PENDING,
            Standing.DECLINED,
            Standing.ACCEPTED,
        )


def request_deadline(closed_at: datetime, extension: Schedule) -> datetime:
    """`request_window_days` calendar days after the end, at the same local time of day (so a
    clock change does not move it by an hour)."""
    local = closed_at.astimezone(extension.tz)
    later = local.replace(tzinfo=None) + timedelta(days=extension.request_window_days)
    return later.replace(tzinfo=extension.tz).astimezone(UTC)


def extension_status(
    *, state: EpisodeState, request: dict | None, extension: Schedule | None, now: datetime
) -> ExtensionStatus:
    """Where an ended episode stands on follow-up. `request` is its extension_requests row (or
    None), `extension` the extension schedule (the request's, or the newest active one; None if
    the feature is off)."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    if (
        not state.closed
        or state.close_reason != "schedule_complete"
        or state.closed_at is None
        or extension is None
    ):
        return ExtensionStatus(Standing.NOT_APPLICABLE, None, None)

    by = request_deadline(state.closed_at, extension)
    status = request["status"] if request else None

    if status == "pending":
        return ExtensionStatus(Standing.PENDING, by, None)
    if status == "accepted":
        return ExtensionStatus(Standing.ACCEPTED, by, None)
    if status == "declined":
        decided = _ts(request["decided_at"])
        until = max(by, request_deadline(decided, extension))
        standing = Standing.DECLINED if now < until else Standing.LAPSED
        return ExtensionStatus(standing, by, until)
    standing = Standing.ELIGIBLE if now < by else Standing.LAPSED
    return ExtensionStatus(standing, by, by)


def _ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def decision_entry_body(decision: str, message: str | None) -> str:
    """The text of the thread entry for a decision (what summaries and briefs read)."""
    head = f"Extension request {decision}."
    return f"{head} {message}" if message else head
