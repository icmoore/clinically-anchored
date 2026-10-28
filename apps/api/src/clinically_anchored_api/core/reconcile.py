"""Reconcile business rows against the audit log (read-only).

Known gap: a business row (check-in, message, patient, consent...) and its audit
event are two separate writes. If the audit write fails after the row is saved,
the api returns 500 and logs it, and the row is left with no audit event. This
script finds those rows by cross-referencing each table against `audit_log`
metadata (`ref_type` / `ref_id`) and the expected `event_type`.

    python -m clinically_anchored_api.core.reconcile [--clinic ID] [--since ISO] [--json]

Needs the same SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY as the api (e.g. via
`railway run`, or a local .env). It never writes: whether and how to
record an orphan (e.g. a note in the log) is a human call, and it can never be
signed as if it had been audited at the time.

Exit status: 0 if every row is audited, 1 if any row is missing its event.

What it reports:
  * unaudited rows  -- the actionable gap above.
  * dangling events -- audit events whose row no longer exists. Informational:
    the log is append-only but business rows can legitimately vanish (e.g. a
    patient delete cascades), so this does not affect the exit status.

The extension events (`extension.requested/accepted/declined`) are written BEFORE their rows, so a
save that then lost a race leaves an event with no row: that shows up as a dangling event, not
a gap.

Patient-account events (`patient_account.*`) are written BEFORE the change they describe, like the
extension events: a change that then failed leaves a dangling event, never a gap. The outbox is
checked for `patient_account.sign_in_code_sent` only (invite emails are audited as
`patient_account.invited`, which a re-send and a re-invite repeat).

Not covered: `link.issued` and `episode.link_rotated` have no business row of their own
(links are stateless signed tokens, and the URL is only returned after the audit write
succeeds), so they cannot leave an orphan. Rows that predate an event type existing (patients
created by seed migrations, messages read before `message.read` was audited)
will show up as unaudited; use --since to scope to the period you care about.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

_PAGE = 1000


@dataclass(frozen=True)
class Check:
    """One expectation: every row in `table` with `when` set must have an
    audit event of `event_type` whose metadata.ref_id is the row's id."""

    table: str
    ref_type: str
    event_type: str
    when: str  # timestamp column that dates the action; null = action never happened
    only: tuple[tuple[str, str], ...] = ()  # (column, value) pairs a row must also match
    exclude: tuple[tuple[str, str], ...] = ()  # rows matching any of these pairs are not checked


CHECKS = [
    Check("patients", "patient", "patient.created", "created_at"),
    Check("episodes", "episode", "episode.created", "created_at"),
    Check("episodes", "episode", "episode.closed", "closed_at"),
    Check("check_ins", "check_in", "check_in.submitted", "created_at"),
    Check("check_ins", "check_in", "check_in.reviewed", "reviewed_at"),
    # The two extension thread entries (migration 16) are audited as extension.* events instead.
    Check(
        "messages", "message", "message.sent", "created_at",
        exclude=(("origin", "extension_request"), ("origin", "extension_decision")),
    ),
    Check("messages", "message", "message.read", "read_at"),
    Check("consents", "consent", "consent.granted", "granted_at"),
    Check("consents", "consent", "consent.revoked", "revoked_at"),
    Check("touchpoints", "touchpoint", "touchpoint.logged", "created_at"),
    Check("thread_summaries", "summary", "summary.generated", "generated_at"),
    Check("patient_briefs", "patient_brief", "patient_brief.generated", "generated_at"),
    Check(
        "care_provider_summaries", "care_provider_summary", "care_provider_summary.generated",
        "generated_at",
    ),
    Check(
        "care_provider_summary_edits", "care_provider_summary_edit",
        "care_provider_summary.edited", "edited_at",
    ),
    Check("extension_requests", "extension_request", "extension.requested", "requested_at"),
    Check(
        "extension_requests", "extension_request", "extension.accepted", "decided_at",
        (("status", "accepted"),),
    ),
    Check(
        "extension_requests", "extension_request", "extension.declined", "decided_at",
        (("status", "declined"),),
    ),
    # Patient accounts (migration 18). Each event is written BEFORE the change, so a change that
    # then failed shows up as a dangling event, not a gap.
    Check("patient_accounts", "patient_account", "patient_account.invited", "invited_at"),
    Check("patient_accounts", "patient_account", "patient_account.activated", "activated_at"),
    Check(
        "patient_accounts", "patient_account", "patient_account.access_turned_off",
        "turned_off_at",
    ),
    Check("patient_accounts", "patient_account", "patient_account.identity_locked", "locked_at"),
    Check(
        "patient_sign_in_links", "patient_sign_in_link", "patient_account.fallback_link_issued",
        "issued_at",
    ),
    Check(
        "patient_sign_in_links", "patient_sign_in_link", "patient_account.fallback_link_used",
        "used_at",
    ),
    # (migration 17's outbox) the code email's id is decided before the send, so the event can
    # come first. Invite emails are audited as patient_account.invited instead.
    Check(
        "email_outbox", "email", "patient_account.sign_in_code_sent", "created_at",
        (("template", "sign_in_code"),),
    ),
    Check("message_drafts", "draft", "draft.generated", "created_at"),
    Check("message_drafts", "draft", "draft.approved", "decided_at", (("status", "approved"),)),
    Check("message_drafts", "draft", "draft.edited", "decided_at", (("status", "edited"),)),
    Check("message_drafts", "draft", "draft.rejected", "decided_at", (("status", "rejected"),)),
]


@dataclass(frozen=True)
class Gap:
    table: str
    row_id: str
    clinic_id: str
    event_type: str  # the audit event that should exist
    at: str  # when the unaudited action happened


@dataclass(frozen=True)
class Dangling:
    clinic_id: str
    event_type: str
    ref_id: str


@dataclass
class Report:
    checked: dict[str, int] = field(default_factory=dict)  # event_type -> rows examined
    unaudited: list[Gap] = field(default_factory=list)
    dangling: list[Dangling] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unaudited


def _parse(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _pages(make_query):
    """Yield every row of a query, a page at a time. `make_query` builds a fresh
    query ending in .order(), so each page can add its own range."""
    offset = 0
    while True:
        rows = make_query().range(offset, offset + _PAGE - 1).execute().data
        yield from rows
        if len(rows) < _PAGE:
            return
        offset += _PAGE


def reconcile(supabase, *, clinic_id: str | None = None, since: datetime | None = None) -> Report:
    """Cross-reference every check against the audit log. `since` limits which
    unaudited rows are reported (by the date of the action), not what counts
    as audited."""
    report = Report()

    event_types = sorted({c.event_type for c in CHECKS})

    def audit_query():
        q = supabase.table("audit_log").select("clinic_id, event_type, metadata")
        if clinic_id:
            q = q.eq("clinic_id", clinic_id)
        return q.in_("event_type", event_types).order("id")

    audited: set[tuple[str, str, str]] = set()
    for row in _pages(audit_query):
        ref_id = (row["metadata"] or {}).get("ref_id")
        if ref_id:
            audited.add((row["clinic_id"], row["event_type"], ref_id))

    present: set[tuple[str, str, str]] = set()
    rows_by_table: dict[str, list[dict]] = {}
    for check in CHECKS:
        if check.table not in rows_by_table:

            # Ids, timestamps and status only: no message text, names or answers.
            same_table = [c for c in CHECKS if c.table == check.table]
            wanted = (
                {c.when for c in same_table}
                | {col for c in same_table for col, _ in c.only + c.exclude}
            )
            cols = ", ".join(["id", "clinic_id", *sorted(wanted)])

            def table_query(table=check.table, cols=cols):
                q = supabase.table(table).select(cols)
                if clinic_id:
                    q = q.eq("clinic_id", clinic_id)
                return q.order("id")

            rows_by_table[check.table] = list(_pages(table_query))

        examined = 0
        for row in rows_by_table[check.table]:
            if (
                row.get(check.when) is None
                or any(row.get(c) != v for c, v in check.only)
                or any(row.get(c) == v for c, v in check.exclude)
            ):
                continue
            examined += 1
            key = (row["clinic_id"], check.event_type, row["id"])
            present.add(key)
            if key in audited:
                continue
            if since and _parse(row[check.when]) < since:
                continue
            report.unaudited.append(
                Gap(check.table, row["id"], row["clinic_id"], check.event_type, row[check.when])
            )
        report.checked[check.event_type] = examined

    report.dangling = [
        Dangling(*key) for key in sorted(audited - present)
    ]
    report.unaudited.sort(key=lambda g: (g.at, g.table, g.row_id))
    return report


def format_report(report: Report) -> str:
    lines = ["Rows examined per event type:"]
    lines += [f"  {event}: {n}" for event, n in sorted(report.checked.items())]
    lines.append("")
    if report.unaudited:
        lines.append(f"UNAUDITED: {len(report.unaudited)} row(s) have no matching audit event")
        for g in report.unaudited:
            lines.append(
                f"  {g.table} {g.row_id}  clinic {g.clinic_id}  expected {g.event_type}  at {g.at}"
            )
    else:
        lines.append("OK: every examined row has its audit event.")
    if report.dangling:
        lines.append("")
        lines.append(
            f"Note: {len(report.dangling)} audit event(s) refer to rows that no longer exist "
            "(informational):"
        )
        for d in report.dangling:
            lines.append(f"  {d.event_type} ref {d.ref_id}  clinic {d.clinic_id}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find business rows with no matching audit event (read-only)."
    )
    parser.add_argument("--clinic", help="limit to one clinic id")
    parser.add_argument(
        "--since",
        type=_parse,
        help="only report unaudited rows whose action is at/after this ISO timestamp "
        "(e.g. 2026-10-01 or 2026-10-01T12:00:00Z)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    from clinically_anchored_api.core.db import get_supabase

    report = reconcile(get_supabase(), clinic_id=args.clinic, since=args.since)
    if args.json:
        print(json.dumps({"ok": report.ok, **asdict(report)}, indent=2))
    else:
        print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
