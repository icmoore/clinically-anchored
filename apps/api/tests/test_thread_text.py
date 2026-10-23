"""How message authors are shown to the model."""

import pytest

from clinically_anchored_api.core.thread_text import speaker


@pytest.mark.parametrize(
    ("message", "shown"),
    [
        ({"sender": "clinician", "origin": "clinician"}, "clinician"),
        ({"sender": "patient", "origin": "scheduled", "interval_no": 3,
          "category": "symptom_or_wound"}, "patient (symptom_or_wound, window 3)"),
        ({"sender": "patient", "origin": "reply", "interval_no": None, "category": None},
         "patient (reply)"),
        ({"sender": "patient"}, "patient"),  # a row from before Messaging v2
        ({"sender": "system"}, "system"),
        # Follow-up extensions (migration 16): their own kinds, and their own window numbers.
        ({"sender": "patient", "origin": "extension_request"}, "patient (extension request)"),
        ({"sender": "clinician", "origin": "extension_decision"},
         "clinician (extension decision)"),
        ({"sender": "patient", "origin": "scheduled", "interval_no": 2, "category": "admin",
          "extension_no": 1}, "patient (admin, extension window 2)"),
    ],
)
def test_speaker(message, shown):
    assert speaker(message) == shown


def test_never_uses_square_brackets_which_the_summary_format_reserves_for_citations():
    message = {"sender": "patient", "origin": "scheduled", "interval_no": 1, "category": "admin"}
    shown = speaker(message)
    assert "[" not in shown and "]" not in shown
