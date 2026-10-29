"""The public patient guide (apps/web, /guide) shows the message periods from
`apps/web/src/lib/active-schedule.json`, because a public page cannot ask the database. This
test is what keeps that file true: it fails the day the active schedule changes without the file.

When it fails: a new schedule version was made active (or the active one was edited). Update the
JSON file to the new row, then re-read the guide's schedule section (lib/guide-strings.ts) for
words that no longer fit. The guide is not the place to guess the schedule.
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.db

GUIDE_SCHEDULE = Path(__file__).resolve().parents[3] / "web" / "src" / "lib" / "active-schedule.json"


def test_the_guides_schedule_is_the_active_main_schedule(db):
    row = db.conn.execute(
        "select version, offsets_days, close_after_days, opens_at_local, timezone "
        "from message_schedules where active and kind = 'main' order by version desc limit 1"
    ).fetchone()
    assert row is not None, "no active main schedule"
    shown = json.loads(GUIDE_SCHEDULE.read_text())
    assert shown == {
        "version": row["version"],
        "offsets_days": list(row["offsets_days"]),
        "close_after_days": row["close_after_days"],
        "opens_at_local": row["opens_at_local"].strftime("%H:%M"),
        "timezone": row["timezone"],
    }


def test_the_guides_other_numbers_match_the_apis_defaults():
    """The guide also says how long a code works, how long a device stays signed in, how many wrong
    surgery dates lock an account and how long a single-use link lasts. These are the api's
    defaults (`core/config.py`, `core/email_templates.py`); a Railway variable that changes one
    would make the guide wrong, and this test cannot see that. The code length (6 digits) is a
    Supabase dashboard setting, checked by `core.gotrue_check`, not here."""
    from clinically_anchored_api.core.config import Settings
    from clinically_anchored_api.core.email_templates import CODE_VALID_MINUTES

    settings = Settings(_env_file=None)
    shown = json.loads(GUIDE_SCHEDULE.with_name("guide-facts.json").read_text())
    assert shown == {
        "codeDigits": 6,
        "codeValidMinutes": CODE_VALID_MINUTES,
        "sessionDays": settings.patient_session_max_age_days,
        "wrongDatesBeforeLock": settings.patient_identity_max_attempts,
        "linkValidHours": settings.patient_fallback_link_ttl_hours,
    }
