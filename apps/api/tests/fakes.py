"""Shared test doubles for the api's unit tests.

`FakeSupabase` is a small in-memory stand-in for the supabase-py client: tables of dicts,
the query methods the api uses (honouring eq / in_ / is_ filters, so cross-clinic checks are
really exercised), and `rpc` handlers a test registers. It is deliberately NOT a re-
implementation of the database's functions: what those do is tested against a real Postgres
in tests/db (including a full API walk-through). Unit tests here register canned handlers and
check how the api maps their answers and errors.

`patch_link` stands in for a patient's episode link in tests of routes that only need "a
valid link for this patient": it patches the episode lookup so no episode tables are needed.
"""

import itertools
import sys
import uuid
from datetime import UTC, date, datetime, time

from clinically_anchored_api.core import episodes as episodes_core
from clinically_anchored_api.core import security
from clinically_anchored_api.core.extension import extension_status
from clinically_anchored_api.core.schedule import Schedule, episode_state

CLINIC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLINIC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PATIENT_A = "a1a1a1a1-a1a1-a1a1-a1a1-a1a1a1a1a1a1"
PATIENT_B = "b1b1b1b1-b1b1-b1b1-b1b1-b1b1b1b1b1b1"
EPISODE_A = "e1e1e1e1-e1e1-e1e1-e1e1-e1e1e1e1e1e1"
EPISODE_B = "e2e2e2e2-e2e2-e2e2-e2e2-e2e2e2e2e2e2"
PROC_A = "d1d1d1d1-d1d1-d1d1-d1d1-d1d1d1d1d1d1"
USER = "99999999-9999-9999-9999-999999999999"
AUTH = {"Authorization": "Bearer good-jwt"}

V1 = Schedule(1, (2, 3, 5, 7, 14, 21, 28, 35, 42), 7, time(7, 0), "America/Toronto")
# Both schedules the database has (migrations 9 and 11). v1 is the old placeholder; episodes made
# before v2 keep it. New episodes get the newest ACTIVE version: v2.
V2 = Schedule(2, (2, 5, 10, 17, 24, 31, 38, 45, 52), 7, time(7, 0), "America/Toronto")
V1_ROW = {
    "version": 1, "offsets_days": [2, 3, 5, 7, 14, 21, 28, 35, 42], "close_after_days": 7,
    "opens_at_local": "07:00:00", "timezone": "America/Toronto", "active": True, "kind": "main",
    "request_window_days": None,
}
V2_ROW = {
    "version": 2, "offsets_days": [2, 5, 10, 17, 24, 31, 38, 45, 52], "close_after_days": 7,
    "opens_at_local": "07:00:00", "timezone": "America/Toronto", "active": True, "kind": "main",
    "request_window_days": None,
}
# Extension v1 (migration 16): placeholder numbers, as in the migration.
EXT = Schedule(3, (7, 14, 21, 28, 35), 7, time(7, 0), "America/Toronto", "extension", 30)
EXT_ROW = {
    "version": 3, "offsets_days": [7, 14, 21, 28, 35], "close_after_days": 7,
    "opens_at_local": "07:00:00", "timezone": "America/Toronto", "active": True,
    "kind": "extension", "request_window_days": 30,
}
# A Saturday morning, interval 1 of a Thursday 2026-10-01 surgery, 07:30 Toronto time.
NOW = datetime(2026, 10, 3, 11, 30, tzinfo=UTC)

_counter = itertools.count(1)


def new_id() -> str:
    return str(uuid.UUID(int=next(_counter)))


def stamp() -> str:
    return f"2026-10-01T00:00:{next(_counter) % 60:02d}+00:00"


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db: "FakeSupabase", name: str):
        self.db, self.name = db, name
        self._eq: list[tuple[str, object]] = []
        self._in: list[tuple[str, list]] = []
        self._null: list[str] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._insert = self._update = None
        self._delete = False

    # filters / shaping
    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq.append((col, val))
        return self

    def in_(self, col, vals):
        self._in.append((col, list(vals)))
        return self

    def is_(self, col, val):
        assert val == "null"
        self._null.append(col)
        return self

    def order(self, col, desc=False):
        self._order.append((col, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, a, b):
        self._limit = b - a + 1
        return self

    # writes
    def insert(self, payload):
        self._insert = payload
        return self

    def update(self, payload):
        self._update = payload
        return self

    def delete(self):
        self._delete = True
        return self

    def _matches(self, row) -> bool:
        return (
            all(row.get(c) == v for c, v in self._eq)
            and all(row.get(c) in vs for c, vs in self._in)
            and all(row.get(c) is None for c in self._null)
        )

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self._insert is not None:
            if self.db.fail_insert.get(self.name):
                raise self.db.fail_insert.pop(self.name)
            row = {**self.db.defaults.get(self.name, {}), **self._insert}
            row.setdefault("id", new_id())
            row.setdefault("created_at", stamp())
            rows.append(row)
            return Result([dict(row)])
        matched = [r for r in rows if self._matches(r)]
        if self._update is not None:
            if self.db.fail_update.get(self.name):
                raise self.db.fail_update.pop(self.name)
            for r in matched:
                r.update(self._update)
        if self._delete:
            for r in matched:
                rows.remove(r)
            return Result([dict(r) for r in matched])
        for col, desc in reversed(self._order):
            matched.sort(key=lambda r, c=col: (r.get(c) is None, r.get(c)), reverse=desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return Result([dict(r) for r in matched])


class Rpc:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        self.db.rpc_calls.append((self.name, self.params))
        handler = self.db.rpc_handlers.get(self.name)
        if handler is None:
            raise AssertionError(f"unexpected rpc {self.name}")
        return Result(handler(self.params))


class _Auth:
    def get_user(self, jwt):
        if jwt != "good-jwt":
            raise ValueError("bad jwt")
        return type("UR", (), {"user": type("U", (), {"id": USER, "email": "u@example.com"})()})()


class FakeSupabase:
    def __init__(self, role: str = "clinician"):
        self.auth = _Auth()
        self.rpc_handlers: dict = {}
        self.rpc_calls: list[tuple[str, dict]] = []
        self.fail_insert: dict = {}
        self.fail_update: dict = {}
        self.defaults = {
            "messages": {"read_at": None, "interval_no": None, "category": None,
                         "reply_to_message_id": None},
            "episodes": {"link_generation": 1, "checkin_completed_at": None, "closed_at": None,
                         "close_reason": None, "closed_by": None, "kind": "main",
                         "extends_episode_id": None, "started_at": None},
            "patients": {"contact": None},
        }
        self.tables: dict[str, list[dict]] = {
            "clinic_members": [{"clinic_id": CLINIC_A, "user_id": USER, "role": role}],
            "clinics": [{"id": CLINIC_A, "name": "A", "office_phone": "905-555-0100"},
                        {"id": CLINIC_B, "name": "B", "office_phone": None}],
            "patients": [
                {"id": PATIENT_A, "clinic_id": CLINIC_A, "full_name": "Pat A", "contact": None,
                 "created_at": "2026-10-01T00:00:00+00:00"},
                {"id": PATIENT_B, "clinic_id": CLINIC_B, "full_name": "Pat B", "contact": None,
                 "created_at": "2026-10-01T00:00:00+00:00"},
            ],
            "procedures": [{"id": PROC_A, "clinic_id": CLINIC_A, "name": "Colectomy",
                            "category": "abdominal_bowel_resection", "active": True}],
            "message_schedules": [dict(V1_ROW), dict(V2_ROW)],
            "extension_requests": [],
            "episodes": [],
            "messages": [],
            "check_ins": [],
        }

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        return Rpc(self, name, params)

    def add_episode(self, *, id=EPISODE_A, clinic=CLINIC_A, patient=PATIENT_A,
                    surgery_date="2026-10-01", **extra) -> dict:
        row = {**self.defaults["episodes"], "id": id, "clinic_id": clinic, "patient_id": patient,
               "procedure_id": PROC_A, "surgery_date": surgery_date, "schedule_version": 1,
               "created_by": USER, "created_at": "2026-10-01T00:00:00+00:00", **extra}
        self.tables["episodes"].append(row)
        return row

    def add_message(self, *, id=None, sender="patient", origin=None, episode=EPISODE_A,
                    clinic=CLINIC_A, patient=PATIENT_A, body="text", **extra) -> dict:
        origin = origin or ("clinician" if sender == "clinician" else "scheduled")
        row = {**self.defaults["messages"], "id": id or new_id(), "clinic_id": clinic,
               "patient_id": patient, "episode_id": episode, "sender": sender, "origin": origin,
               "body": body, "created_at": stamp(), **extra}
        if origin == "scheduled":
            row.setdefault("interval_no", 1)
            row["category"] = row["category"] or "medication"
        self.tables["messages"].append(row)
        return row


def install(monkeypatch, fake: FakeSupabase, *, audit: bool = True) -> list[dict]:
    """Point every api module at `fake` and capture audit events (returned list)."""
    calls: list[dict] = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("clinically_anchored_api") or module is None:
            continue
        if hasattr(module, "get_supabase") and not name.endswith("core.db"):
            monkeypatch.setattr(module, "get_supabase", lambda: fake)
        if audit and hasattr(module, "record_event") and not name.endswith("core.audit"):
            monkeypatch.setattr(module, "record_event", lambda _db, **kw: calls.append(kw))
    return calls


def pin_clock(monkeypatch, now: datetime = NOW) -> None:
    from clinically_anchored_api.core import clock

    monkeypatch.setattr(clock, "now", lambda: now)


def message_row(**overrides) -> dict:
    """A full messages row, as the database returns one."""
    row = {
        "id": new_id(), "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "episode_id": EPISODE_A,
        "sender": "clinician", "body": "hello", "origin": "clinician", "interval_no": None,
        "category": None, "reply_to_message_id": None, "read_at": None, "created_at": stamp(),
    }
    return {**row, **overrides}


def canned_episode(**overrides) -> dict:
    return {
        "id": EPISODE_A, "clinic_id": CLINIC_A, "patient_id": PATIENT_A, "procedure_id": PROC_A,
        "surgery_date": "2026-10-01", "schedule_version": 1, "checkin_completed_at": None,
        "link_generation": 1, "closed_at": None, "close_reason": None, "closed_by": None,
        "created_by": USER, "created_at": "2026-10-01T00:00:00+00:00", **overrides,
    }


def token_for(episode: dict) -> str:
    return security.create_episode_token(
        episode_id=episode["id"], generation=episode["link_generation"]
    )


class Links:
    """Valid patient links without episode tables: each `token(...)` registers an episode, and
    the episode lookup is patched to find them by id. The state is computed for real by the
    engine from the facts you pass."""

    def __init__(self, monkeypatch, now: datetime = NOW):
        self.episodes: dict[str, dict] = {}
        self.facts: dict[str, dict] = {}
        self.requests: dict[str, dict] = {}
        self.now = now
        pin_clock(monkeypatch, now)
        monkeypatch.setattr(security.episodes, "get_episode", self._get)
        monkeypatch.setattr(security.episodes, "current_state", self._state)
        monkeypatch.setattr(security.episodes, "resolve_tail", lambda _db, ep: ep)
        monkeypatch.setattr(security.episodes, "ended_context", self._ended)
        if hasattr(security, "get_supabase"):
            monkeypatch.setattr(security, "get_supabase", lambda: None)

    def _get(self, _db, episode_id, **_k):
        row = self.episodes.get(episode_id)
        return dict(row) if row else None

    def _state(self, _db, ep, now_=None):
        f = self.facts[ep["id"]]
        state = episode_state(
            surgery_date=date.fromisoformat(ep["surgery_date"]), schedule=V1,
            now=now_ or self.now,
            checkin_completed_at=episodes_core.parse_ts(ep["checkin_completed_at"]),
            closed_at=episodes_core.parse_ts(ep["closed_at"]), close_reason=ep["close_reason"],
            used_intervals=set(f["used"]), reply_available=f["reply"],
        )
        return ep, state

    def _ended(self, _db, ep, state, now_):
        """No extension request by default; `requests[episode_id]` registers one, and the
        extension schedule is EXT."""
        request = self.requests.get(ep["id"])
        return request, extension_status(state=state, request=request, extension=EXT, now=now_)

    def token(self, *, clinic=CLINIC_A, patient=PATIENT_A, checked_in=True, used=(),
              reply=False, closed=False, generation=1, ended=False) -> str:
        """`closed`: discharged. `ended`: the schedule ran out (closed_at = the end of V1's last
        window for the surgery date below)."""
        episode = canned_episode(
            id=new_id(), clinic_id=clinic, patient_id=patient, link_generation=generation,
            checkin_completed_at="2026-10-02T12:00:00+00:00" if checked_in else None,
            closed_at="2026-10-02T13:00:00+00:00" if closed else None,
            close_reason="discharged" if closed else None, closed_by=USER if closed else None,
        )
        if ended:
            episode.update(closed_at="2026-11-30T12:00:00+00:00", close_reason="schedule_complete",
                           closed_by=None)
        self.episodes[episode["id"]] = episode
        self.facts[episode["id"]] = {"used": used, "reply": reply}
        return token_for(episode)


def send_rpcs(insert_message):
    """For tests whose own fake keeps a plain messages list: an `rpc(name, params)` that
    stores what send_clinician_message / send_patient_message would, as a full row.
    `insert_message(row_dict) -> stored row dict` adds id/created_at the way that fake does."""

    class _Call:
        def __init__(self, row):
            self._row = row

        def execute(self):
            return Result(self._row)

    def rpc(name, params):
        assert name in ("send_clinician_message", "send_patient_message"), name
        patient = name == "send_patient_message"
        row = insert_message(
            {
                "clinic_id": params["p_clinic_id"], "patient_id": PATIENT_A,
                "episode_id": params["p_episode_id"],
                "sender": "patient" if patient else "clinician", "body": params["p_body"],
                "origin": params["p_origin"] if patient else "clinician",
                "interval_no": params.get("p_interval_no"), "category": params.get("p_category"),
                "reply_to_message_id": None,
            }
        )
        return _Call(row)

    return rpc


def open_episode(monkeypatch) -> None:
    """For tests of routes that only need "this patient has an open episode"."""
    monkeypatch.setattr(
        episodes_core, "open_episode_or_409", lambda *_a, **_k: (canned_episode(), None)
    )
