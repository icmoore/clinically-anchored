"""How a message's author is shown to the model in prompts (draft replies, summaries).

Under Messaging v2 a patient's message carries what they chose to write about (a category
key) and which scheduled window it used, or it is their one reply to a clinic message. The
prompts show that next to the speaker, in parentheses -- never square brackets, which the
summary format reserves for citations. Keys, not the web app's labels: labels change."""


def speaker(message: dict) -> str:
    if message.get("sender") != "patient":
        return str(message.get("sender"))
    if message.get("origin") == "reply":
        return "patient (reply)"
    details = [
        message[k] if k == "category" else f"window {message[k]}"
        for k in ("category", "interval_no")
        if message.get(k) is not None
    ]
    return f"patient ({', '.join(details)})" if details else "patient"
