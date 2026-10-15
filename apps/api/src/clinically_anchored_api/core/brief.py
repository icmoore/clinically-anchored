"""Parse an AI-written patient summary ("brief") into three cited bullets and verify them.

Same all-or-nothing rules as core/citations.py, for a different shape. The model writes exactly
three lines, in this order, each starting with its label and ending in the short labels of the
records it came from in square brackets:

    - The picture now: ... [C1, M3]
    - Trends: ... [C1, M3, M4]
    - Loose ends: None. [T2]

Labels are M1... (messages), C1... (check-ins) and T1... (calls and visits). The prompt shows the
model only these labels; the server maps each back to a real record (`aliases`) and then asks the
database whether that record exists for THIS clinic and THIS patient, because the service role
bypasses RLS and a model can invent anything. Anything else raises CitationError and the caller
discards the whole summary: a missing, extra, misordered or misspelt bullet label, a bullet with
no citation or an empty one, a label that was not given to the model, a bracket in the middle of
the text, or a cited record that is not this patient's. Nothing is repaired and no citation is
silently dropped.
"""

import re
import uuid
from dataclasses import dataclass

from clinically_anchored_api.core.citations import _BULLET, _GROUP, _TRAILING, CitationError

LABELS = ("The picture now", "Trends", "Loose ends")

MESSAGE, CHECK_IN, TOUCHPOINT = "message", "check_in", "touchpoint"
# Label prefix -> the kind of record, and the table that holds it.
KINDS = {"M": MESSAGE, "C": CHECK_IN, "T": TOUCHPOINT}
TABLES = {MESSAGE: "messages", CHECK_IN: "check_ins", TOUCHPOINT: "touchpoints"}


@dataclass(frozen=True)
class Source:
    kind: str  # message | check_in | touchpoint
    id: str  # canonical lowercase uuid


@dataclass(frozen=True)
class BriefBullet:
    label: str  # one of LABELS
    text: str
    sources: tuple[Source, ...]  # in the order cited, no duplicates


def make_aliases(
    message_ids: list[str], check_in_ids: list[str], touchpoint_ids: list[str]
) -> dict[str, Source]:
    """The labels shown to the model -> the records they stand for (each list in display order)."""
    aliases: dict[str, Source] = {}
    for prefix, ids in (("M", message_ids), ("C", check_in_ids), ("T", touchpoint_ids)):
        for n, raw in enumerate(ids, start=1):
            aliases[f"{prefix}{n}"] = Source(KINDS[prefix], str(uuid.UUID(str(raw))))
    return aliases


def parse_brief(raw: str, aliases: dict[str, Source]) -> list[BriefBullet]:
    """Split the model's output into the three bullets. Raises CitationError."""
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) != len(LABELS):
        raise CitationError(f"expected exactly {len(LABELS)} bullets, got {len(lines)}")
    bullets: list[BriefBullet] = []
    for number, (line, label) in enumerate(zip(lines, LABELS, strict=True), start=1):
        body = _BULLET.sub("", line).strip()
        prefix = f"{label}:"
        if not body.startswith(prefix):
            raise CitationError(f"bullet {number} does not start with '{prefix}'")
        body = body[len(prefix):].strip()
        trailing = _TRAILING.search(body)
        if not trailing:
            raise CitationError(f"bullet {number} has no citation")
        text = body[: trailing.start()].strip()
        if not text:
            raise CitationError(f"bullet {number} is only a citation")
        if "[" in text or "]" in text:
            raise CitationError(f"bullet {number} has a bracket that is not a trailing citation")
        sources: list[Source] = []
        for group in _GROUP.findall(trailing.group(1)):
            parts = [p for p in re.split(r"[,\s]+", group) if p]
            if not parts:
                raise CitationError(f"bullet {number} has an empty citation")
            for part in parts:
                source = aliases.get(part.strip().upper())
                if source is None:
                    raise CitationError(
                        "citation is not a record label given for this patient", (part[:64],)
                    )
                sources.append(source)
        bullets.append(BriefBullet(label, text, tuple(dict.fromkeys(sources))))
    return bullets


def verify_brief(
    supabase, *, clinic_id: str, patient_id: str, raw: str, aliases: dict[str, Source]
) -> list[BriefBullet]:
    """Parse `raw` and confirm, against the database, that every cited message, check-in and
    contact exists and belongs to this clinic and this patient. Returns the verified bullets or
    raises CitationError (reporting every record that did not verify, not just the first)."""
    bullets = parse_brief(raw, aliases)
    cited: dict[str, set[str]] = {}
    for bullet in bullets:
        for s in bullet.sources:
            cited.setdefault(s.kind, set()).add(s.id)
    missing: list[str] = []
    for kind, ids in cited.items():
        found = (
            supabase.table(TABLES[kind])
            .select("id")
            .eq("clinic_id", clinic_id)
            .eq("patient_id", patient_id)
            .in_("id", sorted(ids))
            .execute()
            .data
        )
        have = {str(row["id"]).lower() for row in found}
        missing += [f"{kind}:{i}" for i in sorted(ids) if i not in have]
    if missing:
        raise CitationError("cites records that are not this patient's", tuple(missing))
    return bullets
