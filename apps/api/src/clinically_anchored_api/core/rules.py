"""Red-flag rule engine.

Fixed rules a clinician writes, not model judgement -- nothing here decides
that a patient is fine. A check-in that matches a rule gets pulled to the
top of the clinician's queue (is_red_flag = true on the check_ins row).

*** PLACEHOLDER RULES ***
The thresholds below (e.g. 38.5C for fever, a pain score of 8+) are Ian/
Claude's conservative clinical inference from Sarah's Oct 2026 email about
the post-op check-in redesign, written to get a working draft in front of
her for review -- not sourced from her, not clinically validated, and NOT
for use with a real patient. The phone call in "Next step" in decisions-
and-open-questions.md is still what settles her actual red-flag list;
replace this file (ideally sourced from her post-op instructions/protocol
library, data-catalogue D13) once that happens.

Answers are the nested shape documented on schemas.py's CheckInCreate --
one optional dict per symptom the patient ticked, keyed by symptom name.
"""

from collections.abc import Callable

Rule = tuple[str, Callable[[dict], bool]]


def _sub(answers: dict, key: str) -> dict:
    """The nested answers dict for one symptom, or {} if not reported /
    not a dict (a patient link is untrusted input; never assume shape)."""
    value = answers.get(key)
    return value if isinstance(value, dict) else {}


def _severe_pain(answers: dict) -> bool:
    pain = _sub(answers, "pain")
    scale = pain.get("scale")
    return (isinstance(scale, (int, float)) and scale >= 8) or pain.get("trend") == "worse"


def _heavy_bleeding(answers: dict) -> bool:
    bleeding = _sub(answers, "bleeding")
    return bleeding.get("description") == "soaking_through_clothes" or bleeding.get(
        "dressing_change_frequency"
    ) in ("hourly", "every_2_3_hours")


def _high_fever(answers: dict) -> bool:
    fever = _sub(answers, "fever")
    temp = fever.get("temperature_c")
    return bool(fever.get("took_temperature")) and isinstance(temp, (int, float)) and temp >= 38.5


def _concerning_discharge(answers: dict) -> bool:
    discharge = _sub(answers, "discharge")
    return discharge.get("character") in ("bloody", "pus_like")


def _dizziness_reported(answers: dict) -> bool:
    # Any reported dizziness post-op can signal bleeding/anemia -- flag on
    # report alone rather than trying to grade severity from free-form trend.
    return bool(answers.get("dizziness"))


def _possible_bowel_obstruction(answers: dict) -> bool:
    # Bowel-resection-only symptoms (no_bowel_movement / distension). Not
    # passing gas for several days plus distension is a standard ileus/
    # obstruction/anastomotic-leak red flag; a patient who hasn't had these
    # symptoms simply won't have submitted this key at all.
    no_bm = _sub(answers, "no_bowel_movement")
    days = no_bm.get("days")
    if no_bm.get("distension") and isinstance(days, (int, float)) and days >= 3:
        return True
    distension = _sub(answers, "distension")
    return distension.get("trend") == "worse" and bool(distension.get("associated_pain"))


def _bloody_diarrhea(answers: dict) -> bool:
    return bool(_sub(answers, "diarrhea").get("blood_in_stool"))


PLACEHOLDER_RULES: list[Rule] = [
    ("severe or worsening pain", _severe_pain),
    ("heavy bleeding", _heavy_bleeding),
    ("fever >= 38.5C", _high_fever),
    ("concerning wound discharge", _concerning_discharge),
    ("dizziness reported", _dizziness_reported),
    ("possible bowel obstruction / leak signs", _possible_bowel_obstruction),
    ("bloody diarrhea", _bloody_diarrhea),
]


def evaluate_red_flags(answers: dict) -> tuple[bool, list[str]]:
    """Returns (is_red_flag, [matched rule descriptions])."""
    matched = [description for description, check in PLACEHOLDER_RULES if check(answers)]
    return (len(matched) > 0, matched)
