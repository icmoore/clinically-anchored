"""Check an AI-written care provider summary before it is shown.

The summary is one plain paragraph with no citations, so there is nothing to verify against the
database. What is checked is its shape, with the same all-or-nothing rule as the other summaries:
anything wrong raises SummaryFormatError and the caller discards the whole thing. Nothing is
repaired or trimmed. It must not be empty, must not be longer than the prompt allows (with some
slack), must be one paragraph, and must not carry a record label ("[M1]", "[C2]", "[T3]") that
the model was told to keep out of its answer.
"""

import re

# The prompt asks for at most 120 words; this is the point where it clearly did not listen.
MAX_CHARS = 1500

_LABEL = re.compile(r"\[\s*[MCT]\d+\b", re.IGNORECASE)


class SummaryFormatError(Exception):
    pass


def validate_summary(raw: str) -> str:
    """The summary text, stripped, or SummaryFormatError."""
    text = raw.strip()
    if not text:
        raise SummaryFormatError("the summary is empty")
    if len(text) > MAX_CHARS:
        raise SummaryFormatError(f"the summary is too long ({len(text)} characters)")
    if "\n\n" in text or re.search(r"^\s*([-*•]|\d+[.)]|#)\s", text, flags=re.MULTILINE):
        raise SummaryFormatError("the summary is not a single paragraph of prose")
    if _LABEL.search(text):
        raise SummaryFormatError("the summary carries a record label")
    return text
