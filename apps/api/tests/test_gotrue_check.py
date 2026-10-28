"""core/gotrue_check.py against a stand-in: it reports each step, and always deletes its user."""

from clinically_anchored_api.core import gotrue, gotrue_check


class Rpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return self

    def __getattr__(self, name):  # .execute().data
        raise AttributeError(name)


def test_every_step_is_reported_and_the_user_is_deleted_even_on_failure(monkeypatch):
    deleted = []

    class Admin:
        def delete_user(self, uid):
            deleted.append(uid)

    class Supa:
        auth = type("A", (), {"admin": Admin()})()

        def rpc(self, name, params):
            return Rpc({"created_at": "2026-10-01", "ended": 1, "id": "u-1"})

    monkeypatch.setattr(gotrue_check, "get_supabase", lambda: Supa())
    monkeypatch.setattr(gotrue, "ensure_patient_user", lambda email, user_id=None, **kw: "u-1")

    def boom(email):
        raise RuntimeError("generate_link refused")

    monkeypatch.setattr(gotrue, "generate_code", boom)
    results = gotrue_check.run()
    assert results[0] == ("create a confirmed patient user", True, "")
    failed = [name for name, ok, _ in results if not ok]
    assert any("generate_link" in n for n in failed)
    assert deleted == ["u-1"]  # cleaned up regardless
    assert results[-1][0].startswith("the throwaway user was deleted")


def test_main_exit_status_follows_the_results(monkeypatch, capsys):
    monkeypatch.setattr(gotrue_check, "run", lambda: [("a", True, ""), ("b", False, "why")])
    assert gotrue_check.main() == 1
    out = capsys.readouterr().out
    assert "PASS  a" in out and "FAIL  b  (why)" in out and "1 passed, 1 failed" in out
    monkeypatch.setattr(gotrue_check, "run", lambda: [("a", True, "")])
    assert gotrue_check.main() == 0


def test_nothing_is_attempted_if_the_user_cannot_be_made(monkeypatch):
    monkeypatch.setattr(gotrue_check, "get_supabase", lambda: object())
    monkeypatch.setattr(gotrue, "ensure_patient_user",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    results = gotrue_check.run()
    assert len(results) == 1 and results[0][1] is False


def test_a_code_of_the_wrong_length_is_its_own_failure_and_the_rest_still_run(monkeypatch):
    class Admin:
        def delete_user(self, uid):
            pass

    class Supa:
        auth = type("A", (), {"admin": Admin()})()

        def rpc(self, name, params):
            return Rpc({"created_at": "x", "ended": 1, "id": "u-1"})

    monkeypatch.setattr(gotrue_check, "get_supabase", lambda: Supa())
    monkeypatch.setattr(gotrue, "ensure_patient_user", lambda email, user_id=None, **kw: "u-1")
    monkeypatch.setattr(gotrue, "generate_code", lambda email: "27857844")

    def verify(email, code):
        if code == "27857844":
            return gotrue.AuthSession("u-1", "a", "r", 3600, None)
        raise gotrue.CodeRejected()

    monkeypatch.setattr(gotrue, "verify_code", verify)
    monkeypatch.setattr(
        gotrue, "user_for_token", lambda t: gotrue.AuthUser("u-1", None, "patient", "s")
    )
    results = {name: ok for name, ok, _ in gotrue_check.run()}
    assert results["generate_link returns a numeric code (and sends nothing)"] is True
    assert results["the code is 6 digits (dashboard setting)"] is False
    assert results["the right code gives a session"] is True  # not a cascade of failures
