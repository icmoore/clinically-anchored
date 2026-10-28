"""A world for exercising patient accounts end to end against the real schema.

`FakeGoTrue` stands in for Supabase Auth: users, one-time codes, sessions. Its sessions are real
rows in the stub's `auth.sessions` table, so ending a user's sessions in SQL (what the api does on
turn-off) really invalidates their tokens, as it does at Supabase. It is NOT GoTrue: what it
cannot show is whether the real service accepts our calls (that is checked by hand, see the PR).

`Env` wires it together: two clinics (A a normal clinic whose mail goes to a fake SES, B a demo
clinic), staff in A (an owner, a delegate), the api running against the real migrations, a
signing key for the audit log.
"""

import base64
import itertools
import json
import re
from datetime import date, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from fastapi.testclient import TestClient

from clinically_anchored_api.core import gotrue, mailer, patient_auth
from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app
from tests.db.conftest import build_world, make_episode
from tests.db.pg_supabase import PgSupabase
from tests.fakes import install


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


class FakeGoTrue:
    def __init__(self, conn):
        self.conn = conn
        self.users: dict[str, dict] = {}
        self.codes: dict[str, str] = {}
        self.sessions_made = 0
        self.calls: list[str] = []
        self._code_numbers = itertools.count(100000)
        self.admin = _Admin(self)

    def user_obj(self, uid: str) -> Obj:
        u = self.users[uid]
        return Obj(id=uid, email=u["email"], app_metadata=dict(u["app_metadata"]))

    def by_email(self, email: str) -> str | None:
        return next((uid for uid, u in self.users.items() if u["email"] == email), None)

    # supabase-py: client.auth.get_user(jwt)
    def get_user(self, jwt: str):
        if jwt.startswith("user:"):  # a clinician, as in PgSupabase
            uid = jwt[5:]
            return Obj(user=Obj(id=uid, email="staff@example.org", app_metadata={}))
        part = jwt.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        uid = claims["sub"]
        row = self.conn.execute(
            "select 1 from auth.sessions where id = %s", (claims["session_id"],)
        ).fetchone()
        if row is None or self.users[uid]["banned"]:
            raise Exception("session_not_found")
        return Obj(user=self.user_obj(uid))

    # supabase-py: client.auth.verify_otp(...)
    def verify_otp(self, params: dict):
        self.calls.append("verify_otp")
        uid = self.by_email(params["email"])
        if uid is None or self.users[uid]["banned"] or self.codes.get(params["email"]) != params["token"]:
            raise Exception("otp_expired")
        del self.codes[params["email"]]
        return Obj(user=self.user_obj(uid), session=self.new_session(uid))

    def new_session(self, uid: str) -> Obj:
        sid = self.conn.execute(
            "insert into auth.sessions (user_id) values (%s) returning id", (uid,)
        ).fetchone()["id"]
        self.sessions_made += 1
        token = f"{_b64({'alg': 'none'})}.{_b64({'sub': uid, 'session_id': str(sid)})}.sig"
        return Obj(access_token=token, refresh_token=f"rt-{sid}", expires_in=3600,
                   expires_at=4102444800)


class _Admin:
    def __init__(self, gt: FakeGoTrue):
        self.gt = gt

    def create_user(self, attrs: dict):
        self.gt.calls.append("create_user")
        if self.gt.by_email(attrs["email"]):
            raise Exception("email_exists")
        uid = str(self.gt.conn.execute(
            "insert into auth.users (email) values (%s) returning id", (attrs["email"],)
        ).fetchone()["id"])
        self.gt.users[uid] = {"email": attrs["email"], "banned": False,
                              "app_metadata": dict(attrs.get("app_metadata") or {})}
        return Obj(user=self.gt.user_obj(uid))

    def update_user_by_id(self, uid: str, attrs: dict):
        self.gt.calls.append("update_user_by_id")
        u = self.gt.users[uid]
        if "email" in attrs:
            u["email"] = attrs["email"]
            self.gt.conn.execute("update auth.users set email = %s where id = %s",
                                 (attrs["email"], uid))
        u["app_metadata"].update(attrs.get("app_metadata") or {})
        if "ban_duration" in attrs:
            u["banned"] = attrs["ban_duration"] != "none"
        return Obj(user=self.gt.user_obj(uid))

    def get_user_by_id(self, uid: str):
        return Obj(user=self.gt.user_obj(uid))

    def generate_link(self, params: dict):
        self.gt.calls.append("generate_link")
        # Real GoTrue would quietly CREATE a user for an unknown address; the api must never rely
        # on that, so the fake refuses.
        assert self.gt.by_email(params["email"]), "generate_link for a user that does not exist"
        code = f"{next(self.gt._code_numbers):06d}"
        self.gt.codes[params["email"]] = code
        return Obj(properties=Obj(email_otp=code))


class FakeSes:
    def __init__(self):
        self.sent: list[dict] = []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": f"ses-{len(self.sent)}"}

    def last_to(self, address: str) -> dict | None:
        mine = [s for s in self.sent if s["Destination"]["ToAddresses"] == [address]]
        return mine[-1] if mine else None


CODE_LINE = re.compile(r"^(\d{6})$", re.MULTILINE)


class Env:
    def __init__(self, db, monkeypatch):
        c = self.c = db.conn
        self.w = build_world(c)
        w = self.w
        self.delegate = str(c.execute(
            "insert into auth.users (email) values ('delegate@example.org') returning id"
        ).fetchone()["id"])
        c.execute("insert into clinic_members (clinic_id, user_id, role) values (%s, %s, 'delegate')",
                  (w.clinic_a, self.delegate))
        c.execute("update clinic_members set role = 'owner' where user_id = %s", (w.user_a,))
        c.execute("update clinics set name = 'Lakeview Surgical', office_phone = '905-555-0100' "
                  "where id = %s", (w.clinic_a,))
        c.execute("update clinics set name = 'Demo Clinic', is_demo = true where id = %s",
                  (w.clinic_b,))

        self.pg = PgSupabase(c)
        self.gt = FakeGoTrue(c)
        self.pg.auth = self.gt
        install(monkeypatch, self.pg, audit=False)
        monkeypatch.setattr(gotrue, "_anon_client", lambda: Obj(auth=self.gt))
        self.ses = FakeSes()
        monkeypatch.setattr(mailer.boto3, "client", lambda *a, **k: self.ses)
        mailer._client.cache_clear()
        patient_auth.reset_cache()

        settings = get_settings()
        for name, value in {
            "ses_from_address": "no-reply@clinic.example.org",
            "web_base_url": "https://app.clinic.example.org",
            "supabase_url": "http://auth.invalid",
            "supabase_anon_key": "anon-key",
            "patient_auth_per_ip_per_minute": 1000,
            "patient_link_reads_per_minute": 1000,
            "patient_link_writes_per_minute": 1000,
            "patient_code_cooldown_seconds": 0,
            "patient_codes_per_hour": 1000,
        }.items():
            monkeypatch.setattr(settings, name, value)
        key = Ed25519PrivateKey.generate().private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )
        monkeypatch.setattr(settings, "audit_signing_key", base64.b64encode(key).decode())
        monkeypatch.setattr(settings, "audit_key_id", "test-1")
        self.settings = settings
        self.client = TestClient(app)

    # --- people -----------------------------------------------------------------------------

    def staff(self, user: str | None = None) -> dict:
        return {"Authorization": f"Bearer user:{user or self.w.user_a}"}

    def add_patient(self, name="Pat Test", email="pat@mail.org", *, clinic=None, surgery=None,
                    with_episode=True) -> str:
        clinic = clinic or self.w.clinic_a
        pid = str(self.c.execute(
            "insert into patients (clinic_id, full_name, email) values (%s, %s, %s) returning id",
            (clinic, name, email),
        ).fetchone()["id"])
        if with_episode:
            user = self.w.user_a if clinic == self.w.clinic_a else self.w.user_b
            proc = self.w.proc_a if clinic == self.w.clinic_a else self.w.proc_b
            make_episode(self.c, self.w, patient=pid, clinic=clinic, procedure=proc, user=user,
                         surgery_date=surgery or (date.today() - timedelta(days=3)))
        return pid

    def surgery_date(self, patient_id: str) -> str:
        return str(self.c.execute("select surgery_date from episodes where patient_id = %s "
                                  "order by created_at desc limit 1", (patient_id,)
                                  ).fetchone()["surgery_date"])

    # --- clinic actions -----------------------------------------------------------------------

    def url(self, patient_id: str, tail: str, clinic=None) -> str:
        return f"/clinics/{clinic or self.w.clinic_a}/patients/{patient_id}{tail}"

    def act(self, patient_id: str, action: str, *, by=None, clinic=None):
        return self.client.post(self.url(patient_id, f"/account/{action}", clinic),
                                headers=self.staff(by))

    # --- patient side -------------------------------------------------------------------------

    def request_code(self, email: str):
        return self.client.post("/patient-auth/request-code", json={"email": email})

    def last_code(self, email: str, clinic=None) -> str | None:
        """The code in the newest sign-in email to this address: from the fake SES in a normal
        clinic, from the outbox in a demo one."""
        sent = self.ses.last_to(email)
        if sent is not None and (clinic or self.w.clinic_a) == self.w.clinic_a:
            text = sent["Content"]["Simple"]["Body"]["Text"]["Data"]
        else:
            row = self.c.execute(
                "select rendered_text from email_outbox where recipient = %s and "
                "template = 'sign_in_code' and status = 'demo' order by created_at desc limit 1",
                (email,)).fetchone()
            if row is None:
                return None
            text = row["rendered_text"]
        found = CODE_LINE.search(text)
        return found.group(1) if found else None

    def verify(self, email: str, code: str):
        return self.client.post("/patient-auth/verify", json={"email": email, "code": code})

    def sign_in(self, email: str, clinic=None) -> dict:
        """Request a code and use it: the signed-in headers."""
        assert self.request_code(email).status_code == 202
        code = self.last_code(email, clinic)
        assert code, "no code was sent"
        r = self.verify(email, code)
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def activate(self, patient_id: str, email: str, *, clinic=None) -> dict:
        """Invite (as the owner), sign in, give the surgery date, agree: an active patient's
        headers."""
        assert self.act(patient_id, "invite", clinic=clinic).status_code == 200
        headers = self.sign_in(email, clinic)
        r = self.client.post("/patient/identity", headers=headers,
                             json={"surgery_date": self.surgery_date(patient_id)})
        assert r.status_code == 200, r.text
        r = self.client.post("/patient/consent", headers=headers, json={
            "consent_text_version": "2026-10-v1", "consents": ["messaging", "ai_assisted_replies"]})
        assert r.status_code == 200 and r.json()["status"] == "active", r.text
        return headers

    def account(self, patient_id: str) -> dict:
        return self.c.execute("select * from patient_accounts where patient_id = %s",
                              (patient_id,)).fetchone()

    def audit_types(self) -> list[str]:
        return [r["event_type"] for r in self.c.execute("select event_type from audit_log "
                                                        "order by id").fetchall()]
