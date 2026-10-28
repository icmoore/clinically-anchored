from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from clinically_anchored_api.api import (
    audit,
    auth_hooks,
    briefs,
    care_provider_summaries,
    check_ins,
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

settings = get_settings()

app = FastAPI(
    title="Clinically Anchored API",
    description="Backend service: check-in intake, red-flag rules, summary generation, audit log.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(audit.router)
app.include_router(clinics.router)
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
