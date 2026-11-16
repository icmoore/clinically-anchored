from fastapi import APIRouter

from clinically_anchored_api.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness/readiness check for Railway and uptime monitoring. `environment` is the APP_ENV
    the service started with, which startup verified against the database (core/environment.py);
    "unset" only appears where startup checks don't run (tests)."""
    return {"status": "ok", "environment": get_settings().app_env or "unset"}
