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
    """The words around the variables mention no surgery, symptom, medicine or date."""
    e = (invite() if template == t.INVITE else code_email())
    words = re.sub(r"[^a-z ]", " ", (e.subject + " " + e.text).lower())
    for banned in ("surgery", "operation", "procedure", "pain", "fever", "wound", "medication",
                   "symptom", "diagnos", "patient", "check-in"):
        assert banned not in words, banned
