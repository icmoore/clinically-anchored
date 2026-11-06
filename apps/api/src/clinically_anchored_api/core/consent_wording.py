"""Which consents every patient must give at "Before you start", by key.

The wording, the versions and the Terms and Privacy Notice themselves are versioned content: see
core/legal.py and apps/web/content/legal/ (config.json names the current version of each). The api
needs only the keys here: both are required before an account becomes active. Every key must have a
sentence in config.json (core/legal.py refuses to load otherwise).
"""

MESSAGING = "messaging"
AI_ASSISTED_REPLIES = "ai_assisted_replies"
REQUIRED = (MESSAGING, AI_ASSISTED_REPLIES)
