"""The patient's link: one signed token per episode.

Patients are not Supabase Auth users (see docs/database) -- there is no patient password.
They reach the app through a link that carries a token naming their EPISODE and the link
generation it was issued under. The token itself proves nothing but that we issued it; on
every request the api loads the episode and decides (see `require_patient_link`):

  * a link is revoked by bumping the episode's `link_generation` ("reset link"): the old
    token still verifies, but its generation no longer matches;
  * an episode that has closed (discharged, or the schedule ran out) refuses everything
    except the state endpoint, which answers "closed" and nothing else.

So the token is not what expires: it has no short lifetime, only a long backstop. The URL
for a given generation is always the same, so the clinic can copy it again later without
the api storing it.

This is not yet the real delivery flow -- nothing sends these over SMS/email; a clinician
copies the link from the dashboard.
"""

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, Query, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from clinically_anchored_api.core import clock, episodes
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.ratelimit import limit_patient_link
from clinically_anchored_api.core.schedule import EpisodeState


class InvalidLinkToken(Exception):
    """A link token that is missing, tampered with or too old."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().checkin_link_secret, salt="link:episode")


def create_episode_token(*, episode_id: str, generation: int) -> str:
    return _serializer().dumps({"e": episode_id, "g": generation})


def verify_episode_token(token: str) -> tuple[str, int]:
    """Returns (episode_id, generation) or raises InvalidLinkToken. Needs no database."""
    try:
        data = _serializer().loads(
            token, max_age=get_settings().patient_link_backstop_max_age_seconds
        )
    except SignatureExpired as exc:
        raise InvalidLinkToken("This link has expired.") from exc
    except BadSignature as exc:
        raise InvalidLinkToken("This link is not valid.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("e"), str) or not isinstance(
        data.get("g"), int
    ):
        raise InvalidLinkToken("This link is not valid.")
    return data["e"], data["g"]


@dataclass(frozen=True)
class PatientLink:
    """A verified link: the episode it opens and where that episode stands right now."""

    episode: dict
    state: EpisodeState
    now: datetime

    @property
    def episode_id(self) -> str:
        return self.episode["id"]

    @property
    def clinic_id(self) -> str:
        return self.episode["clinic_id"]

    @property
    def patient_id(self) -> str:
        return self.episode["patient_id"]


def _require_link(*, allow_closed: bool):
    def dependency(request: Request, token: str = Query(..., description="Patient link token")):
        try:
            episode_id, generation = verify_episode_token(token)
        except InvalidLinkToken as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        limit_patient_link(request, episode_id)  # 429; only validly signed tokens are counted

        supabase = get_supabase()
        episode = episodes.get_episode(supabase, episode_id)
        if episode is None:
            raise HTTPException(status_code=401, detail="This link is not valid.")
        if episode["link_generation"] != generation:
            raise HTTPException(status_code=401, detail="This link is no longer valid.")

        now = clock.now()
        episode, state = episodes.current_state(supabase, episode, now)
        if state.closed and not allow_closed:
            raise HTTPException(status_code=410, detail="This care period has ended.")
        return PatientLink(episode=episode, state=state, now=now)

    return dependency


# FastAPI dependencies for patient-facing routes.
#   401  the link is not valid, or was reset
#   410  the episode has closed (not raised by `require_patient_link_even_if_closed`)
#   429  the patient exceeded their request budget
# Clinic and patient come from the episode, never from the URL or body.
require_patient_link = _require_link(allow_closed=False)
require_patient_link_even_if_closed = _require_link(allow_closed=True)
