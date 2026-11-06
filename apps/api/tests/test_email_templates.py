"""core/email_templates.py: the words of every email; nothing clinical or unescaped gets in."""

import re

import pytest

from clinically_anchored_api.core import email_templates as t

URL = "https://app.example.org/sign-in"


def invite(**kw):
    return t.render(t.INVITE, clinic_name="Lakeview Surgical", office_phone="905-555-0100",
                    sign_in_url=URL, **kw)


def code_email(**kw):
    return t.render(t.SIGN_IN_CODE, clinic_name="Lakeview Surgical", office_phone="905-555-0100",
                    code="123456", **kw)


def test_invite_has_the_clinic_the_page_and_the_phone_in_text_and_html():
    e = invite()
    assert "Lakeview Surgical" in e.subject
    for body in (e.text, e.html):
        assert "Lakeview Surgical" in body
        assert URL in body
        assert "905-555-0100" in body
    assert f'<a href="{URL}">' in e.html
    assert "123456" not in e.text  # an invite carries no code


def test_sign_in_code_email_has_the_code_and_how_long_it_works():
    e = code_email()
    assert "123456" in e.text and "123456" in e.html
    assert "10 minutes" in e.text
    assert "Lakeview Surgical" in e.subject
    assert "123456" not in e.subject  # a code never goes in a subject line
    assert t.render(t.SIGN_IN_CODE, clinic_name="C", office_phone=None, code="1",
                    code_valid_minutes=5).text.count("5 minutes") == 1


GUIDE = "https://app.example.org/guide"


@pytest.mark.parametrize("make", [invite, code_email])
def test_both_emails_point_at_the_patient_guide_when_there_is_an_address_for_it(make):
    e = make(guide_url=GUIDE)
    for body in (e.text, e.html):
        assert "New to this? Read the patient guide:" in body
        assert GUIDE in body
    assert f'<a href="{GUIDE}">' in e.html
    assert GUIDE not in e.subject


@pytest.mark.parametrize("make", [invite, code_email])
def test_without_a_guide_address_the_guide_lines_are_left_out_without_a_hole(make):
    e = make()
    assert "guide" not in e.text.lower() and "guide" not in e.html.lower()
    assert "\n\n\n" not in e.text and "None" not in e.text


def test_no_phone_on_file_leaves_the_phone_line_out_without_a_hole():
    e = t.render(t.INVITE, clinic_name="C", office_phone=None, sign_in_url=URL)
    assert "call the office" not in e.text and "None" not in e.text
    assert "\n\n\n" not in e.text and not e.text.rstrip("\n").endswith("\n")
    assert "{" not in e.text and "{" not in e.html


def test_nothing_the_clinic_typed_can_break_the_html_or_the_subject():
    e = t.render(t.INVITE, clinic_name='<script>x</script>\r\nBcc: a@b.c', office_phone='"><b>',
                 sign_in_url=URL)
    assert "<script>" not in e.html and "&lt;script&gt;" in e.html
    assert '"><b>' not in e.html
    assert "\r" not in e.subject and "\n" not in e.subject


def test_braces_in_a_clinic_name_are_kept_as_typed():
    e = t.render(t.INVITE, clinic_name="Dr {Smith} {0}", office_phone=None, sign_in_url=URL)
    assert "Dr {Smith} {0}" in e.text


def test_needed_variables_are_required_and_unknown_templates_refused():
    with pytest.raises(ValueError):
        t.render(t.INVITE, clinic_name="C", office_phone=None)
    with pytest.raises(ValueError):
        t.render(t.SIGN_IN_CODE, clinic_name="C", office_phone=None)
    with pytest.raises(ValueError):
        t.render("newsletter", clinic_name="C", office_phone=None)


def test_a_blank_clinic_name_still_reads_properly():
    e = t.render(t.SIGN_IN_CODE, clinic_name="  ", office_phone=None, code="1")
    assert "Your surgeon's office" in e.text


@pytest.mark.parametrize("template", t.TEMPLATES)
def test_emails_carry_no_clinical_content(template):
    """The words around the variables mention no surgery, symptom, medicine or date. The one
    allowed mention is the invite's note that the first sign-in will ASK for the surgery date."""
    e = (invite() if template == t.INVITE else code_email())
    text = e.text.replace(t._FIRST_SIGN_IN_NOTE, "")
    words = re.sub(r"[^a-z ]", " ", (e.subject + " " + text).lower())
    for banned in ("surgery", "operation", "procedure", "pain", "fever", "wound", "medication",
                   "symptom", "diagnos", "patient", "check-in"):
        assert banned not in words, banned


def test_invite_says_the_first_sign_in_asks_for_the_surgery_date_after_the_code_line():
    e = invite()
    note = (
        "The first time you sign in, we will ask for the date of your surgery to confirm it's you."
    )
    code_line = "We will email you a code to sign in. You do not need a password."
    for body in (e.text, e.html.replace("&#x27;", "'")):
        assert note in body
        assert body.index(code_line) < body.index(note) < body.index("you were not expecting")
    assert note not in code_email().text  # only the invite says it


def test_no_email_states_a_date_or_the_surgery_date():
    """The invite names no date: not in the words, and not from anything a clinic passes in. The
    patient types their own date; the email only says that we will ask."""
    no_phone = {"clinic_name": "Lakeview Surgical", "office_phone": None}  # a phone is not a date
    emails = (
        t.render(t.INVITE, sign_in_url=URL, guide_url="https://app.example.org/guide", **no_phone),
        t.render(t.SIGN_IN_CODE, code="a1b2", **no_phone),
    )
    for e in emails:
        words = e.subject + " " + e.text
        assert not re.search(r"\b(19|20)\d\d\b", words)  # no year
        assert not re.search(r"\d{1,2}[/-]\d{1,2}", words)  # no 03/14 or 2026-03-14 style date
        months = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
        assert not re.search(rf"(?i)\b({months})[a-z]*\b \d", words)  # no "March 14"
