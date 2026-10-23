"""Reconciliation: finds business rows with no audit event (and the reverse),
against an in-memory fake that honours eq / in_ / order / range."""

import json
from datetime import UTC, datetime

import pytest

from clinically_anchored_api.core import reconcile as rec

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class _Q:
    def __init__(self, rows):
        self.rows, self.eqs, self.ins, self.rng = rows, [], [], None

    def select(self, *_a):
        return self

    def eq(self, c, v):
        self.eqs.append((c, v))
        return self

    def in_(self, c, vs):
        self.ins.append((c, vs))
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def execute(self):
        rows = [
            r
            for r in self.rows
            if all(r.get(c) == v for c, v in self.eqs) and all(r.get(c) in vs for c, vs in self.ins)
        ]
        if self.rng:
            rows = rows[self.rng[0] : self.rng[1] + 1]
        return type("R", (), {"data": rows})()


class _Db:
    def __init__(self):
        self.t = {n: [] for n in (
            "patients", "episodes", "check_ins", "messages", "consents", "message_drafts",
            "touchpoints", "thread_summaries", "patient_briefs", "care_provider_summaries",
            "care_provider_summary_edits", "extension_requests", "audit_log",
        )}

    def table(self, name):
        return _Q(self.t[name])

    def audit(self, clinic, event, ref_id):
        self.t["audit_log"].append(
            {"clinic_id": clinic, "event_type": event, "metadata": {"ref_id": ref_id, "salt": "s"}}
        )


@pytest.fixture
def db():
    return _Db()


def _gaps(report):
    return {(g.table, g.row_id, g.event_type) for g in report.unaudited}


def test_fully_audited_data_is_clean(db):
    db.t["check_ins"].append(
        {"id": "ci1", "clinic_id": A, "created_at": "2026-10-01T10:00:00Z", "reviewed_at": None}
    )
    db.audit(A, "check_in.submitted", "ci1")
    report = rec.reconcile(db)
    assert report.ok and report.unaudited == [] and report.dangling == []
    assert report.checked["check_in.submitted"] == 1
    assert report.checked["check_in.reviewed"] == 0  # never reviewed: nothing to audit


def test_finds_orphans_in_every_table(db):
    t = "2026-10-01T10:00:00Z"
    db.t["patients"].append({"id": "p1", "clinic_id": A, "created_at": t})
    db.t["check_ins"].append({"id": "ci1", "clinic_id": A, "created_at": t, "reviewed_at": t})
    db.t["messages"].append(
        {"id": "m1", "clinic_id": A, "created_at": t, "read_at": t, "sender": "patient"}
    )
    db.t["consents"].append(
        {"id": "c1", "clinic_id": A, "granted_at": t, "revoked_at": t, "patient_id": "p1"}
    )
    db.t["episodes"].append({"id": "e1", "clinic_id": A, "created_at": t, "closed_at": t})
    report = rec.reconcile(db)
    assert not report.ok
    assert _gaps(report) == {
        ("episodes", "e1", "episode.created"),
        ("episodes", "e1", "episode.closed"),
        ("patients", "p1", "patient.created"),
        ("check_ins", "ci1", "check_in.submitted"),
        ("check_ins", "ci1", "check_in.reviewed"),
        ("messages", "m1", "message.sent"),
        ("messages", "m1", "message.read"),
        ("consents", "c1", "consent.granted"),
        ("consents", "c1", "consent.revoked"),
    }


def test_drafts_are_checked_per_transition(db):
    t = "2026-10-01T10:00:00Z"
    db.t["message_drafts"] += [
        {"id": "d-pending", "clinic_id": A, "created_at": t, "decided_at": None,
         "status": "pending"},
        {"id": "d-ok", "clinic_id": A, "created_at": t, "decided_at": t, "status": "approved"},
        {"id": "d-edit", "clinic_id": A, "created_at": t, "decided_at": t, "status": "edited"},
        {"id": "d-rej", "clinic_id": A, "created_at": t, "decided_at": t, "status": "rejected"},
    ]
    for ref in ("d-pending", "d-ok", "d-edit", "d-rej"):
        db.audit(A, "draft.generated", ref)
    db.audit(A, "draft.approved", "d-ok")
    db.audit(A, "draft.approved", "d-edit")  # wrong event for an edited draft: doesn't count
    report = rec.reconcile(db)
    assert _gaps(report) == {
        ("message_drafts", "d-edit", "draft.edited"),
        ("message_drafts", "d-rej", "draft.rejected"),
    }
    assert report.checked["draft.generated"] == 4
    assert report.checked["draft.approved"] == 1  # only approved drafts are examined for it
    assert [(d.event_type, d.ref_id) for d in report.dangling] == [("draft.approved", "d-edit")]


def test_unaudited_draft_generation_is_reported(db):
    db.t["message_drafts"].append(
        {"id": "d1", "clinic_id": A, "created_at": "2026-10-01T10:00:00Z", "decided_at": None,
         "status": "pending"}
    )
    assert _gaps(rec.reconcile(db)) == {("message_drafts", "d1", "draft.generated")}


def test_each_event_is_matched_independently(db):
    # Submitted was audited but the later review wasn't: only the review is a gap.
    t = "2026-10-01T10:00:00Z"
    db.t["check_ins"].append({"id": "ci1", "clinic_id": A, "created_at": t, "reviewed_at": t})
    db.audit(A, "check_in.submitted", "ci1")
    assert _gaps(rec.reconcile(db)) == {("check_ins", "ci1", "check_in.reviewed")}


def test_event_type_must_match_not_just_ref_id(db):
    # link.issued also carries ref_type=patient/ref_id=<patient id>; it is not
    # evidence that patient.created was audited.
    db.t["patients"].append({"id": "p1", "clinic_id": A, "created_at": "2026-10-01T00:00:00Z"})
    db.audit(A, "link.issued", "p1")
    assert _gaps(rec.reconcile(db)) == {("patients", "p1", "patient.created")}


def test_event_under_the_wrong_clinic_does_not_count(db):
    db.t["messages"].append(
        {"id": "m1", "clinic_id": A, "created_at": "2026-10-01T00:00:00Z", "read_at": None}
    )
    db.audit(B, "message.sent", "m1")
    report = rec.reconcile(db)
    assert _gaps(report) == {("messages", "m1", "message.sent")}
    assert [d.clinic_id for d in report.dangling] == [B]


def test_dangling_audit_events_are_reported_but_do_not_fail(db):
    db.audit(A, "message.sent", "gone")
    report = rec.reconcile(db)
    assert report.ok
    assert [(d.event_type, d.ref_id) for d in report.dangling] == [("message.sent", "gone")]


def test_since_filters_reports_but_not_what_counts_as_audited(db):
    db.t["patients"] += [
        {"id": "old", "clinic_id": A, "created_at": "2026-09-01T00:00:00Z"},  # e.g. seed data
        {"id": "new", "clinic_id": A, "created_at": "2026-10-02T00:00:00Z"},
        {"id": "old-ok", "clinic_id": A, "created_at": "2026-09-01T00:00:00Z"},
    ]
    db.audit(A, "patient.created", "old-ok")
    report = rec.reconcile(db, since=datetime(2026, 10, 1, tzinfo=UTC))
    assert _gaps(report) == {("patients", "new", "patient.created")}
    assert report.dangling == []  # old-ok's event still pairs with its row


def test_clinic_filter_scopes_everything(db):
    t = "2026-10-01T00:00:00Z"
    db.t["patients"] += [
        {"id": "pa", "clinic_id": A, "created_at": t},
        {"id": "pb", "clinic_id": B, "created_at": t},
    ]
    assert _gaps(rec.reconcile(db, clinic_id=B)) == {("patients", "pb", "patient.created")}


def test_pages_through_large_tables(db, monkeypatch):
    monkeypatch.setattr(rec, "_PAGE", 2)
    for i in range(5):
        db.t["messages"].append(
            {"id": f"m{i}", "clinic_id": A, "created_at": "2026-10-01T00:00:00Z", "read_at": None}
        )
        if i != 3:
            db.audit(A, "message.sent", f"m{i}")
    report = rec.reconcile(db)
    assert report.checked["message.sent"] == 5
    assert _gaps(report) == {("messages", "m3", "message.sent")}


def test_nothing_is_ever_written(db):
    class _ReadOnly(_Q):
        def insert(self, *_a):
            raise AssertionError("reconcile must not write")

        update = delete = upsert = insert

    db.table = lambda name: _ReadOnly(db.t[name])
    db.t["patients"].append({"id": "p1", "clinic_id": A, "created_at": "2026-10-01T00:00:00Z"})
    rec.reconcile(db)


def test_cli_exit_codes_and_output(db, monkeypatch, capsys):
    monkeypatch.setattr("clinically_anchored_api.core.db.get_supabase", lambda: db)
    assert rec.main([]) == 0
    assert "OK" in capsys.readouterr().out

    db.t["patients"].append({"id": "p1", "clinic_id": A, "created_at": "2026-10-01T00:00:00Z"})
    assert rec.main([]) == 1
    out = capsys.readouterr().out
    assert "UNAUDITED: 1" in out and "p1" in out and "patient.created" in out

    assert rec.main(["--json", "--since", "2026-10-02"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_unaudited_touchpoint_is_reported(db):
    t = "2026-10-01T10:00:00Z"
    db.t["touchpoints"] += [
        {"id": "t-ok", "clinic_id": A, "created_at": t},
        {"id": "t-orphan", "clinic_id": A, "created_at": t},
    ]
    db.audit(A, "touchpoint.logged", "t-ok")
    assert _gaps(rec.reconcile(db)) == {("touchpoints", "t-orphan", "touchpoint.logged")}


def test_episodes_are_checked_for_creation_and_close(db):
    t = "2026-10-01T10:00:00Z"
    db.t["episodes"] += [
        {"id": "e-open", "clinic_id": A, "created_at": t, "closed_at": None},
        {"id": "e-closed", "clinic_id": A, "created_at": t, "closed_at": t},
    ]
    db.audit(A, "episode.created", "e-open")
    db.audit(A, "episode.created", "e-closed")
    report = rec.reconcile(db)
    assert _gaps(report) == {("episodes", "e-closed", "episode.closed")}  # open one needs no close
    assert report.checked["episode.created"] == 2 and report.checked["episode.closed"] == 1
    db.audit(A, "episode.closed", "e-closed")
    assert rec.reconcile(db).ok


def test_saved_summaries_are_checked_against_summary_generated(db):
    t = "2026-10-01T10:00:00Z"
    db.t["thread_summaries"] += [
        {"id": "s-ok", "clinic_id": A, "generated_at": t},
        {"id": "s-orphan", "clinic_id": A, "generated_at": t},
    ]
    db.audit(A, "summary.generated", "s-ok")
    assert _gaps(rec.reconcile(db)) == {("thread_summaries", "s-orphan", "summary.generated")}


def test_saved_patient_briefs_are_checked_against_patient_brief_generated(db):
    t = "2026-10-01T10:00:00Z"
    db.t["patient_briefs"] += [
        {"id": "b-ok", "clinic_id": A, "generated_at": t},
        {"id": "b-orphan", "clinic_id": A, "generated_at": t},
    ]
    db.audit(A, "patient_brief.generated", "b-ok")
    report = rec.reconcile(db)
    assert _gaps(report) == {("patient_briefs", "b-orphan", "patient_brief.generated")}
    assert report.checked["patient_brief.generated"] == 2


def test_saved_care_provider_summaries_are_checked_against_their_audit_event(db):
    t = "2026-10-01T10:00:00Z"
    db.t["care_provider_summaries"] += [
        {"id": "p-ok", "clinic_id": A, "generated_at": t},
        {"id": "p-orphan", "clinic_id": A, "generated_at": t},
    ]
    db.audit(A, "care_provider_summary.generated", "p-ok")
    report = rec.reconcile(db)
    assert _gaps(report) == {
        ("care_provider_summaries", "p-orphan", "care_provider_summary.generated")
    }
    assert report.checked["care_provider_summary.generated"] == 2


def test_saved_summary_edits_are_checked_against_their_audit_event(db):
    t = "2026-10-01T10:00:00Z"
    db.t["care_provider_summary_edits"] += [
        {"id": "e-ok", "clinic_id": A, "edited_at": t},
        {"id": "e-orphan", "clinic_id": A, "edited_at": t},
    ]
    db.audit(A, "care_provider_summary.edited", "e-ok")
    report = rec.reconcile(db)
    assert _gaps(report) == {
        ("care_provider_summary_edits", "e-orphan", "care_provider_summary.edited")
    }
    assert report.checked["care_provider_summary.edited"] == 2
