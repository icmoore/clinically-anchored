"""The categories a patient picks before writing a scheduled message.

Stored on the message as the KEY. Labels are the web app's business and will change; a key
that is retired stays valid in old rows, so only ever add keys here (and stop offering a
retired one by removing it from OFFERED). The keys are PLACEHOLDERS pending the surgeon."""

OFFERED = ("recovery_update", "symptom_or_wound", "medication", "admin")


def is_offered(key: str | None) -> bool:
    return key in OFFERED
