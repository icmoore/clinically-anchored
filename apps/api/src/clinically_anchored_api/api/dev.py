"""Dev-only endpoints. Never mounted unless ENVIRONMENT=development -- see
main.py, which only includes this router in that case. This exists purely
to stand in for the real link delivery flow (SMS/email), which doesn't exist yet
(a clinician copies the link from the dashboard)."""

from fastapi import APIRouter, HTTPException

from clinically_anchored_api.core import episodes
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.security import create_episode_token
from clinically_anchored_api.schemas import DevLinkRequest, DevLinkResponse

router = APIRouter(prefix="/dev", tags=["dev"])


@router.post("/episode-links", response_model=DevLinkResponse)
def mint_episode_link(body: DevLinkRequest) -> DevLinkResponse:
    episode = episodes.get_episode(get_supabase(), body.episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found.")
    return DevLinkResponse(
        token=create_episode_token(
            episode_id=episode["id"], generation=episode["link_generation"]
        )
    )
