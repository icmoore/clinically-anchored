"""core/brief.py: the three-bullet AI patient summary is parsed strictly and every cited record is
verified against the database. Nothing is repaired; any problem discards the whole summary."""

import pytest

from clinically_anchored_api.core.brief import (
    CHECK_IN,
    MESSAGE,
    TOUCHPOINT,
    Source,
    make_aliases,
    parse_brief,
    verify_brief,
)
from clinically_anchored_api.core.citations import CitationError
from tests.fakes import CLINIC_A, CLINIC_B, PATIENT_A, PATIENT_B, FakeSupabase, new_id

M1, M2, C1, T1, T2 = (new_id() for _ in range(5))
ALIASES = make_aliases([M1, M2], [C1], [T1, T2])

GOOD = (
    "- The picture now: Reported a pain score of 8 and a red flag; the clinic replied. [C1, M2]\n"
    "- Trends: Pain 8 at check-in, 6 in a later message; bleeding not reported. [C1, M1]\n"
    "- Loose ends: Office to book a results visit. [T2]"
)


def test_labels_resolve_to_the_records_they_stand_for():
    assert ALIASES["M1"] == Source(MESSAGE, M1)
    assert ALIASES["M2"] == Source(MESSAGE, M2)
    assert ALIASES["C1"] == Source(CHECK_IN, C1)
    assert ALIASES["T2"] == Source(TOUCHPOINT, T2)
    assert set(ALIASES) == {"M1", "M2", "C1", "T1", "T2"}


def test_three_labelled_bullets_in_order_with_their_sources():
    bullets = parse_brief(GOOD, ALIASES)
    assert [b.label for b in bullets] == ["The picture now", "Trends", "Loose ends"]
    assert bullets[0].text == "Reported a pain score of 8 and a red flag; the clinic replied."
    assert bullets[0].sources == (Source(CHECK_IN, C1), Source(MESSAGE, M2))
    assert bullets[1].sources == (Source(CHECK_IN, C1), Source(MESSAGE, M1))
    assert bullets[2].sources == (Source(TOUCHPOINT, T2),)


def test_labels_are_case_insensitive_separate_groups_and_duplicates_collapse():
    raw = GOOD.replace("[C1, M2]", "[c1] [m2, C1]").replace("[T2]", "[t2]")
    bullets = parse_brief(raw, ALIASES)
    assert bullets[0].sources == (Source(CHECK_IN, C1), Source(MESSAGE, M2))
    assert bullets[2].sources == (Source(TOUCHPOINT, T2),)


def test_none_is_a_valid_loose_end_when_it_cites_what_was_checked():
    raw = GOOD.replace("Office to book a results visit.", "None.")
    assert parse_brief(raw, ALIASES)[2].text == "None."


def test_plain_dashes_asterisks_and_numbers_are_accepted_as_bullet_markers():
    raw = GOOD.replace("- The", "* The").replace("- Trends", "2. Trends")
    raw = raw.replace("- Loose", "\u2022 Loose")
    assert [b.label for b in parse_brief(raw, ALIASES)] == [
        "The picture now", "Trends", "Loose ends",
    ]


def test_blank_lines_between_bullets_are_fine():
    assert len(parse_brief(GOOD.replace("\n", "\n\n"), ALIASES)) == 3


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("", "expected exactly 3 bullets, got 0"),
        ("- The picture now: ok. [C1]", "got 1"),
        (GOOD.split("\n")[0] + "\n" + GOOD.split("\n")[1], "got 2"),
        (GOOD + "\n- Extra: more. [M1]", "got 4"),
        (GOOD + "\nSome closing remark.", "got 4"),
    ],
)
def test_anything_other_than_three_bullets_is_discarded(raw, why):
    with pytest.raises(CitationError, match=why):
        parse_brief(raw, ALIASES)


def test_a_missing_bullet_label_is_discarded():
    raw = GOOD.replace("Trends: ", "")
    with pytest.raises(CitationError, match="bullet 2 does not start with 'Trends:'"):
        parse_brief(raw, ALIASES)


def test_a_misspelt_or_re_cased_bullet_label_is_discarded():
    for bad in ("Trend:", "trends:", "The picture:", "Loose end:"):
        label = {"Trend:": "Trends:", "trends:": "Trends:", "The picture:": "The picture now:",
                 "Loose end:": "Loose ends:"}[bad]
        with pytest.raises(CitationError, match="does not start with"):
            parse_brief(GOOD.replace(label, bad), ALIASES)


def test_misordered_bullets_are_discarded():
    first, second, third = GOOD.split("\n")
    with pytest.raises(CitationError, match="bullet 1 does not start with 'The picture now:'"):
        parse_brief("\n".join([second, first, third]), ALIASES)
    with pytest.raises(CitationError, match="bullet 2 does not start with 'Trends:'"):
        parse_brief("\n".join([first, third, second]), ALIASES)


def test_a_bullet_with_no_citation_is_discarded_including_none():
    raw = GOOD.replace(" [T2]", "")
    with pytest.raises(CitationError, match="bullet 3 has no citation"):
        parse_brief(raw, ALIASES)
    with pytest.raises(CitationError, match="bullet 3 has no citation"):
        parse_brief(GOOD.replace("Office to book a results visit. [T2]", "None."), ALIASES)


def test_an_empty_citation_or_a_citation_only_bullet_is_discarded():
    with pytest.raises(CitationError, match="no citation|empty citation"):
        parse_brief(GOOD.replace("[T2]", "[]"), ALIASES)
    with pytest.raises(CitationError, match="only a citation"):
        parse_brief(GOOD.replace("Office to book a results visit. [T2]", "[T2]"), ALIASES)
    with pytest.raises(CitationError, match="only a citation|no citation"):
        parse_brief(GOOD.replace("Loose ends: Office to book a results visit. [T2]", "Loose ends:"),
                    ALIASES)


@pytest.mark.parametrize("label", ["M3", "C2", "T3", "T0", "X1", "M", "1", "M1a", "abc"])
def test_a_label_the_model_was_not_given_is_discarded(label):
    with pytest.raises(CitationError, match="not a record label"):
        parse_brief(GOOD.replace("[T2]", f"[{label}]"), ALIASES)


def test_a_real_id_instead_of_a_label_is_discarded():
    with pytest.raises(CitationError, match="not a record label"):
        parse_brief(GOOD.replace("[T2]", f"[{T2}]"), ALIASES)


def test_a_bracket_in_the_middle_of_a_bullet_is_discarded():
    with pytest.raises(CitationError, match="bracket"):
        parse_brief(GOOD.replace("Pain 8 at", "Pain [8] at"), ALIASES)


# --- verification against the database ------------------------------------------------------


def db_with(**rows) -> FakeSupabase:
    fake = FakeSupabase()
    for table, items in rows.items():
        fake.tables[table] = items
    return fake


def records(patient=PATIENT_A, clinic=CLINIC_A):
    row = {"clinic_id": clinic, "patient_id": patient}
    return dict(
        messages=[{"id": M1, **row}, {"id": M2, **row}],
        check_ins=[{"id": C1, **row}],
        touchpoints=[{"id": T1, **row}, {"id": T2, **row}],
    )


def verify(fake, raw=GOOD, patient=PATIENT_A, clinic=CLINIC_A, aliases=ALIASES):
    return verify_brief(fake, clinic_id=clinic, patient_id=patient, raw=raw, aliases=aliases)


def test_every_cited_message_check_in_and_touchpoint_must_exist_for_this_patient():
    bullets = verify(db_with(**records()))
    assert [len(b.sources) for b in bullets] == [2, 2, 1]


@pytest.mark.parametrize("table", ["messages", "check_ins", "touchpoints"])
def test_a_cited_record_that_does_not_exist_is_discarded(table):
    rows = records()
    rows[table] = []
    with pytest.raises(CitationError, match="not this patient's") as err:
        verify(db_with(**rows))
    assert err.value.message_ids  # every offending record is reported


@pytest.mark.parametrize("table", ["messages", "check_ins", "touchpoints"])
def test_another_patients_record_is_discarded(table):
    rows = records()
    rows[table] = [{**r, "patient_id": PATIENT_B} for r in rows[table]]
    with pytest.raises(CitationError, match="not this patient's"):
        verify(db_with(**rows))


@pytest.mark.parametrize("table", ["messages", "check_ins", "touchpoints"])
def test_another_clinics_record_is_discarded(table):
    rows = records()
    rows[table] = [{**r, "clinic_id": CLINIC_B} for r in rows[table]]
    with pytest.raises(CitationError, match="not this patient's"):
        verify(db_with(**rows))


def test_every_offending_record_is_reported_not_just_the_first():
    rows = records()
    rows["messages"], rows["touchpoints"] = [], []
    with pytest.raises(CitationError) as err:
        verify(db_with(**rows))
    kinds = {i.split(":")[0] for i in err.value.message_ids}
    assert kinds == {"message", "touchpoint"}


def test_a_record_id_is_not_trusted_just_because_it_has_a_label():
    # Even if a label pointed at someone else's record (a bug upstream), the database check
    # catches it.
    foreign = make_aliases([new_id(), M2], [C1], [T1])
    raw = GOOD.replace("[T2]", "[T1]")
    with pytest.raises(CitationError, match="not this patient's"):
        verify(db_with(**records()), raw=raw, aliases=foreign)
