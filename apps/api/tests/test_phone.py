"""North American phone number validation and normalisation."""

import pytest

from clinically_anchored_api.core.phone import normalize_na_phone


@pytest.mark.parametrize(
    "raw",
    [
        "905-555-0100", "(905) 555-0100", "905.555.0100", "9055550100", "905 555 0100",
        "+1 905 555 0100", "+1 (905) 555-0100", "1-905-555-0100", "1 905 555 0100",
        "  905-555-0100  ", "+19055550100",
    ],
)
def test_the_ways_people_write_a_number_all_become_one_form(raw):
    assert normalize_na_phone(raw) == "905-555-0100"


@pytest.mark.parametrize(
    "raw",
    [
        "",  # nothing
        "   ",
        "call the office",  # letters
        "905-555-CALL",
        "905-555-0100 ext 12",  # an extension needs its own field
        "905-555-0100 x12",
        "555-0100",  # 7 digits, no area code
        "905-555-010",  # 9 digits
        "905-555-01000",  # 11 digits not starting with 1
        "2905-555-0100",
        "+44 20 7946 0958",  # not North American
        "+52 55 1234 5678",
        "011-905-555-0100",  # starts with 0
        "005-555-0100",  # area code starts with 0
        "105-555-0100",  # area code starts with 1
        "905-055-0100",  # exchange starts with 0
        "905-155-0100",  # exchange starts with 1
        "911-555-0100",  # N11 area code
        "905-411-0100",  # N11 exchange
        "905-555-0100; drop table clinics",
    ],
)
def test_things_that_are_not_a_north_american_number_are_refused_with_a_reason(raw):
    with pytest.raises(ValueError) as err:
        normalize_na_phone(raw)
    assert str(err.value)  # a sentence the clinic can act on, never empty
