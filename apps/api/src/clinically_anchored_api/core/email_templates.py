"""Every word of every email the api sends, in one place.

PLACEHOLDER WORDING -- pending clinical and clinic review. Do not treat as final.

Rules for what goes here:
  * no clinical content, ever: no procedure, no dates of surgery, no symptoms, no message text.
    An email says only who is writing (the clinic's name), what to do (a sign-in page, a code) and
    how to reach a person (the office phone);
  * short words and short sentences, like the patient screens (lib/patient-strings.ts);
  * plain text first, then a simple HTML copy of the same words (no images, no tracking).

`render` fills a template and escapes everything it inserts into the HTML.
"""

import re
from dataclasses import dataclass
from html import escape

INVITE = "invite"
SIGN_IN_CODE = "sign_in_code"
TEMPLATES = (INVITE, SIGN_IN_CODE)

# How long a sign-in code works, in the words of the email. The api enforces the real limit.
CODE_VALID_MINUTES = 10

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


# {clinic} is the clinic's name. {phone_line} is a sentence with the office phone, or nothing.
_SUBJECT = {
    INVITE: "{clinic}: you are invited to sign in",
    SIGN_IN_CODE: "Your {clinic} sign-in code",
}

# Paragraphs, in order. A paragraph with an empty result (no phone on file) is left out.
_PARAGRAPHS = {
    INVITE: [
        "Hello,",
        "{clinic} has invited you to use its secure messaging service.",
        "To start, open this page and enter this email address:",
        "{url}",
        "We will email you a code to sign in. You do not need a password.",
        "If you were not expecting this, you can ignore this email.",
        "{phone_line}",
    ],
    SIGN_IN_CODE: [
        "Hello,",
        "Your sign-in code for {clinic} is:",
        "{code}",
        "It works for {minutes} minutes. Do not share it with anyone.",
        "If you did not ask for this code, you can ignore this email.",
        "{phone_line}",
    ],
}

_PHONE_LINE = "If you have questions, call the office at {phone}."


def _clean(value: str) -> str:
    """One line, no control characters: a clinic name goes into a subject line."""
    return _CONTROL.sub(" ", value).strip()


def render(
    template: str,
    *,
    clinic_name: str,
    office_phone: str | None,
    sign_in_url: str | None = None,
    code: str | None = None,
    code_valid_minutes: int = CODE_VALID_MINUTES,
) -> RenderedEmail:
    """Fill `template`. The invite needs `sign_in_url`; the sign-in code needs `code`."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown email template {template!r}")
    if template == INVITE and not sign_in_url:
        raise ValueError("the invite needs a sign_in_url")
    if template == SIGN_IN_CODE and not code:
        raise ValueError("the sign-in code email needs a code")

    clinic = _clean(clinic_name) or "Your surgeon's office"
    phone = _clean(office_phone) if office_phone else ""
    values = {
        "clinic": clinic,
        "url": sign_in_url or "",
        "code": code or "",
        "minutes": str(code_valid_minutes),
        "phone_line": _PHONE_LINE.format(phone=phone) if phone else "",
    }

    def fill(text: str) -> str:
        return text.format(**values)

    paragraphs = [p for p in (fill(p) for p in _PARAGRAPHS[template]) if p]
    subject = fill(_SUBJECT[template])
    return RenderedEmail(
        subject=subject,
        text="\n\n".join(paragraphs) + "\n",
        html=_html(template, paragraphs, values),
    )


def _html(template: str, paragraphs: list[str], values: dict[str, str]) -> str:
    body = []
    for p in paragraphs:
        safe = escape(p)
        if template == INVITE and p == values["url"]:
            body.append(f'<p><a href="{escape(p, quote=True)}">{safe}</a></p>')
        elif template == SIGN_IN_CODE and p == values["code"]:
            body.append(
                '<p style="font-size:28px;font-weight:600;letter-spacing:4px;'
                f'font-family:Menlo,Consolas,monospace;margin:16px 0">{safe}</p>'
            )
        else:
            body.append(f"<p>{safe}</p>")
    return (
        '<!doctype html><html lang="en"><body style="font-family:Arial,Helvetica,sans-serif;'
        'font-size:16px;line-height:1.5;color:#1a1a1a;max-width:480px;margin:0 auto;padding:16px">'
        + "".join(body)
        + "</body></html>"
    )
