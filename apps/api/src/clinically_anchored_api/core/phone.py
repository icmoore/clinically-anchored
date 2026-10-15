"""North American phone numbers (the clinic's office line, shown to patients on urgent screens).

A number is accepted in the ways people write one, and stored in ONE form, `905-555-0100`, so the
web app can show it and build a `tel:` link from it without guessing:

  905-555-0100   (905) 555-0100   905.555.0100   9055550100   +1 905 555 0100   1-905-555-0100

Rules: ten digits after an optional leading country code 1 (or +1); the area code and the exchange
(the first three digits and the next three) may not start with 0 or 1, and neither may be a service
code like 911 or 411 (N11). No letters, no extension: an extension would need its own field. A
number is not checked for being in service; only that it is shaped like one.
"""

import re

_ALLOWED = re.compile(r"^\+?[\d\s().\-]+$")


def normalize_na_phone(raw: str) -> str:
    """Return the number as `NXX-NXX-XXXX`, or raise ValueError saying what is wrong."""
    text = raw.strip()
    if not text or not _ALLOWED.match(text):
        raise ValueError("Enter a phone number using digits only (for example 905-555-0100).")
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") and not digits.startswith("1"):
        raise ValueError("Only North American numbers are supported (+1).")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("A North American number has 10 digits, such as 905-555-0100.")
    area, exchange = digits[:3], digits[3:6]
    for label, part in (("area code", area), ("exchange", exchange)):
        if part[0] in "01":
            raise ValueError(f"The {label} ({part}) cannot start with 0 or 1.")
        if part[1:] == "11":
            raise ValueError(f"The {label} ({part}) is a service number, not an office line.")
    return f"{area}-{exchange}-{digits[6:]}"
