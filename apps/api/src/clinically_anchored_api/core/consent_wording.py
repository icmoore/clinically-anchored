"""What a patient agrees to at their first sign-in, and which wording they agreed to.

PLACEHOLDER: the consent list and its wording are product and legal decisions that are not made
yet (see core/config.py `consent_types`). The api needs two things from this module: the types
every patient must give before the account is active, and the version of the wording the web
screen shows, so what is stored in `consents` says exactly what the patient saw. The wording
itself lives in the web app's patient strings, keyed by this version; change both together.
"""

MESSAGING = "messaging"
AI_ASSISTED_REPLIES = "ai_assisted_replies"
REQUIRED = (MESSAGING, AI_ASSISTED_REPLIES)

CURRENT_VERSION = "2026-10-v1"
