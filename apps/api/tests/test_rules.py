"""Direct unit coverage of the (placeholder) red-flag rule engine against
the nested per-symptom answer shape documented on schemas.CheckInCreate."""

from clinically_anchored_api.core.rules import evaluate_red_flags


def test_no_symptoms_reported_is_not_a_red_flag():
    is_flag, matched = evaluate_red_flags({})
    assert is_flag is False
    assert matched == []


def test_severe_pain_by_scale():
    is_flag, matched = evaluate_red_flags({"pain": {"scale": 9, "trend": "same"}})
    assert is_flag is True
    assert "severe or worsening pain" in matched


def test_worsening_pain_trend_flags_even_at_low_scale():
    is_flag, matched = evaluate_red_flags({"pain": {"scale": 3, "trend": "worse"}})
    assert is_flag is True
    assert "severe or worsening pain" in matched


def test_mild_stable_pain_is_not_a_red_flag():
    is_flag, _ = evaluate_red_flags({"pain": {"scale": 3, "trend": "better"}})
    assert is_flag is False


def test_bleeding_soaking_through_clothes_is_heavy():
    is_flag, matched = evaluate_red_flags(
        {
            "bleeding": {
                "description": "soaking_through_clothes",
                "dressing_change_frequency": "once_a_day",
            }
        }
    )
    assert is_flag is True
    assert "heavy bleeding" in matched


def test_bleeding_changed_hourly_is_heavy_even_if_tissue_only():
    is_flag, matched = evaluate_red_flags(
        {
            "bleeding": {
                "description": "tissue_paper_only",
                "dressing_change_frequency": "hourly",
            }
        }
    )
    assert is_flag is True
    assert "heavy bleeding" in matched


def test_light_bleeding_is_not_a_red_flag():
    is_flag, _ = evaluate_red_flags(
        {
            "bleeding": {
                "description": "tissue_paper_only",
                "dressing_change_frequency": "once_a_day",
            }
        }
    )
    assert is_flag is False


def test_fever_at_or_above_threshold_flags():
    fever = {"took_temperature": True, "temperature_c": 38.5}
    is_flag, matched = evaluate_red_flags({"fever": fever})
    assert is_flag is True
    assert "fever >= 38.5C" in matched


def test_fever_below_threshold_does_not_flag():
    is_flag, _ = evaluate_red_flags({"fever": {"took_temperature": True, "temperature_c": 37.8}})
    assert is_flag is False


def test_fever_reported_without_a_reading_does_not_flag():
    # No thermometer reading -- nothing to threshold against.
    is_flag, _ = evaluate_red_flags({"fever": {"took_temperature": False}})
    assert is_flag is False


def test_pus_like_discharge_flags():
    is_flag, matched = evaluate_red_flags({"discharge": {"character": "pus_like"}})
    assert is_flag is True
    assert "concerning wound discharge" in matched


def test_clear_discharge_does_not_flag():
    is_flag, _ = evaluate_red_flags({"discharge": {"character": "clear_watery"}})
    assert is_flag is False


def test_any_reported_dizziness_flags():
    is_flag, matched = evaluate_red_flags({"dizziness": {"trend": "same", "orthostatic": True}})
    assert is_flag is True
    assert "dizziness reported" in matched


def test_no_gas_with_distension_flags_possible_obstruction():
    is_flag, matched = evaluate_red_flags({"no_bowel_movement": {"days": 3, "distension": True}})
    assert is_flag is True
    assert "possible bowel obstruction / leak signs" in matched


def test_no_gas_without_distension_does_not_flag():
    is_flag, _ = evaluate_red_flags({"no_bowel_movement": {"days": 4, "distension": False}})
    assert is_flag is False


def test_worsening_painful_distension_flags():
    distension = {"trend": "worse", "associated_pain": True}
    is_flag, matched = evaluate_red_flags({"distension": distension})
    assert is_flag is True
    assert "possible bowel obstruction / leak signs" in matched


def test_bloody_diarrhea_flags():
    diarrhea = {"blood_in_stool": True, "frequency_per_day": 3}
    is_flag, matched = evaluate_red_flags({"diarrhea": diarrhea})
    assert is_flag is True
    assert "bloody diarrhea" in matched


def test_non_bloody_diarrhea_does_not_flag():
    is_flag, _ = evaluate_red_flags({"diarrhea": {"blood_in_stool": False, "frequency_per_day": 3}})
    assert is_flag is False


def test_multiple_matched_rules_are_all_reported():
    is_flag, matched = evaluate_red_flags(
        {
            "pain": {"scale": 9, "trend": "worse"},
            "fever": {"took_temperature": True, "temperature_c": 39.1},
        }
    )
    assert is_flag is True
    assert set(matched) == {"severe or worsening pain", "fever >= 38.5C"}


def test_malformed_answers_do_not_crash_the_engine():
    # A patient link is untrusted input -- a symptom value that isn't the
    # expected dict shape should be ignored, not raise.
    is_flag, matched = evaluate_red_flags({"pain": "a lot", "fever": True, "bleeding": None})
    assert is_flag is False
    assert matched == []
