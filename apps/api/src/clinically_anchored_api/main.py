from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from clinically_anchored_api.api import (
    audit,
    auth_hooks,
    briefs,
    care_provider_summaries,
    check_ins,
    clinician_account,
    clinics,
    consents,
    demo_inbox,
    drafts,
    episodes,
    extensions,
    health,
    messages,
    patient,
    patient_accounts,
    patient_auth,
    patient_first_sign_in,
    procedures,
    queue,
    summaries,
    touchpoints,
)
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.core.db import get_supabase
from clinically_anchored_api.core.environment import check_environment

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Refuse to start unless APP_ENV matches the database's app_settings row (core/environment.py).
    Plain `TestClient(app)` doesn't run this; tests of the check call check_environment directly."""
    check_environment(get_supabase(), settings)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Clinically Anchored API",
    description="Backend service: check-in intake, red-flag rules, summary generation, audit log.",
    version="0.1.0",
)

app.add_middleware(CORSMiddleware, **settings.cors_options)

app.include_router(health.router)
app.include_router(audit.router)
app.include_router(clinics.router)
app.include_router(clinician_account.router)
app.include_router(episodes.router)
app.include_router(patient.router)
app.include_router(patient_auth.router)
app.include_router(patient_first_sign_in.router)
app.include_router(patient_accounts.router)
app.include_router(auth_hooks.router)
app.include_router(extensions.router)
app.include_router(procedures.router)
app.include_router(check_ins.router)
app.include_router(messages.router)
app.include_router(consents.router)
app.include_router(demo_inbox.router)
app.include_router(drafts.router)
app.include_router(queue.router)
app.include_router(summaries.router)
app.include_router(briefs.router)
app.include_router(care_provider_summaries.router)
app.include_router(touchpoints.router)
