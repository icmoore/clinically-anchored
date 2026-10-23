"""How a message's author is shown to the model in prompts (draft replies, summaries).

Under Messaging v2 a patient's message carries what they chose to write about (a category
key) and which scheduled window it used, or it is their one reply to a clinic message. The
prompts show that next to the speaker, in parentheses -- never square brackets, which the
summary format reserves for citations. Follow-up extension entries (migration 16) are their own
kinds, labelled so a model never reads them as a symptom report. Keys, not the web app's labels:
labels change."""


def speaker(message: dict) -> str:
    """`message["extension_no"]`, when the caller knows it, says the message's window is one of a
    follow-up extension's (1, 2, ...): its windows number from 1 again, so the label says so."""
    origin = message.get("origin")
    if origin == "extension_request":
        return "patient (extension request)"
    if origin == "extension_decision":
        return "clinician (extension decision)"
    if message.get("sender") != "patient":
        return str(message.get("sender"))
    if origin == "reply":
        return "patient (reply)"
    window = "extension window" if message.get("extension_no") else "window"
    details = [
        message[k] if k == "category" else f"{window} {message[k]}"
        for k in ("category", "interval_no")
        if message.get(k) is not None
    ]
    return f"patient ({', '.join(details)})" if details else "patient"
