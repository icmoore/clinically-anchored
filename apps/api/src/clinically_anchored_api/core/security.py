"""Who a patient route is answering: the signed-in patient, and where their care period stands.

Patients sign in with their email and a code (core/patient_auth.py). On every request the api
turns the session into one account and one patient, finds that patient's latest care period, and
works out where it stands right now (`episodes.current_state`). Nothing in a request -- a path, a
query, a body -- can name a different patient or clinic.

The old per-episode patient link (`?token=`, a signed token naming an episode) is gone: those
links stop working (the web app says "This link has been replaced"), a `token` in a request is
ignored, and a request without a patient session is refused (401).

The routes' dependencies, by what they let through:

  require_patient                  an open care period only
  require_patient_ended_mode       the same, and also a care period that ENDED BY SCHEDULE while its
                                   limited "ended" mode lasts (core/extension.py): the patient may
                                   still ask for a follow-up extension
  require_patient_even_if_closed   any care period: used by the state endpoint, which answers
                                   "closed" and nothing else once it has

Refusals: 401 not signed in (or the session ended), 403 the account is not active (invited, locked,
turned off) or is not a patient's, 404 no care period yet, 410 the care period has closed, 429 the
patient exceeded their request budget. An accepted extension is a new episode that continues the
old one; the patient's latest care period is the newest of the chain (`episodes.resolve_tail`).
"""

from dataclasses import dataclass
from datetime import datetime

from fastapi import Header, HTTPException, Request

from clinically_anchored_api.core import clock, episodes, patient_auth
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.extension import ExtensionStatus
from clinically_anchored_api.core.schedule import EpisodeState


@dataclass(frozen=True)
class PatientContext:
    """A signed-in patient: their latest care period and where it stands right now."""

    episode: dict
    state: EpisodeState
    now: datetime
    extension: ExtensionStatus
    request: dict | None = None  # the extension request made about `episode`, if any

    @property
    def episode_id(self) -> str:
        return self.episode["id"]

    @property
    def clinic_id(self) -> str:
        return self.episode["clinic_id"]

    @property
    def patient_id(self) -> str:
        return self.episode["patient_id"]


def _open_episode(
    supabase, episode: dict, *, allow_closed: bool, ended_mode: bool
) -> PatientContext:
    """The episode as the patient meets it now, or the refusal for one that has closed."""
    now = clock.now()
    episode, state = episodes.current_state(supabase, episode, now)
    request, extension = episodes.ended_context(supabase, episode, state, now)
    if state.closed and not allow_closed:
        if not (ended_mode and extension.ended_mode_open):
            raise HTTPException(status_code=410, detail="This care period has ended.")
    return PatientContext(
        episode=episode, state=state, now=now, extension=extension, request=request
    )


def _require(*, allow_closed: bool, ended_mode: bool = False):
    def dependency(
        request: Request, authorization: str | None = Header(default=None)
    ) -> PatientContext:
        session = patient_auth.require_active_patient(request, authorization)
        supabase = get_supabase()
        episode = episodes.latest_episode(supabase, session.clinic_id, session.patient_id)
        if episode is None:
            raise HTTPException(status_code=404, detail="There is no care period yet.")
        return _open_episode(
            supabase, episodes.resolve_tail(supabase, episode),
            allow_closed=allow_closed, ended_mode=ended_mode,
        )

    return dependency


require_patient = _require(allow_closed=False)
require_patient_even_if_closed = _require(allow_closed=True)
require_patient_ended_mode = _require(allow_closed=False, ended_mode=True)
