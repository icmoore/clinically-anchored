"""A one-off check that the real Supabase project behaves the way core/gotrue.py assumes.

The tests replace Supabase Auth with a stand-in, so they cannot tell whether the real service
accepts our calls. This does. It creates ONE throwaway Auth user (an address at no-email.invalid,
no clinic, no patient, nothing else is written), walks through everything patient sign-in depends
on, prints PASS or FAIL for each step, and deletes the user at the end, whatever happens:

    railway run python -m clinically_anchored_api.core.gotrue_check

Needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and SUPABASE_ANON_KEY (all from Railway) and
migration 18 applied. It touches the shared database, but only `auth.users` / `auth.sessions` for
that one user. No email is sent (the address cannot receive any, and nothing here asks Supabase to
send one).
"""

import re
import sys

from clinically_anchored_api.core import gotrue
from clinically_anchored_api.core.db import get_supabase


def _step(results: list, name: str, check):
    try:
        outcome = check()
    except Exception as exc:  # report, never crash: the cleanup must still run
        results.append((name, False, f"{type(exc).__name__}: {exc}"[:200]))
        return None
    results.append((name, True, ""))
    return outcome


def run() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    supabase = get_supabase()
    email = gotrue.placeholder_email()
    uid = None
    try:
        uid = _step(results, "create a confirmed patient user",
                    lambda: gotrue.ensure_patient_user(email))
        if uid is None:
            return results

        def code_is_digits():
            code = gotrue.generate_code(email)
            assert re.fullmatch(r"\d{6,10}", code), f"got {code!r}"
            return code

        code = _step(results, "generate_link returns a numeric code (and sends nothing)",
                     code_is_digits)

        def code_is_six_digits():
            assert code and len(code) == 6, (
                f"Supabase makes {len(code or '')}-digit codes: set Email OTP Length to 6 in the "
                "dashboard (Authentication -> Sign In / Providers -> Email)"
            )

        _step(results, "the code is 6 digits (dashboard setting)", code_is_six_digits)

        def wrong_code_is_refused():
            try:
                gotrue.verify_code(email, "000000" if code != "000000" else "000001")
            except gotrue.CodeRejected:
                return
            raise AssertionError("a wrong code was accepted")

        _step(results, "a wrong code is refused", wrong_code_is_refused)

        def right_code_signs_in():
            session = gotrue.verify_code(email, code)
            assert session.user_id == uid, "session belongs to another user"
            return session

        session = _step(results, "the right code gives a session", right_code_signs_in)
        if session is None:
            return results

        def code_is_single_use():
            try:
                gotrue.verify_code(email, code)
            except gotrue.CodeRejected:
                return
            raise AssertionError("a spent code was accepted again")

        _step(results, "the same code cannot be used twice", code_is_single_use)

        def token_names_the_patient():
            user = gotrue.user_for_token(session.access_token)
            assert user.id == uid and user.account_type == "patient", user
            assert user.session_id, "no session_id claim in the access token"
            return user

        user = _step(results, "the token is accepted and marks a patient user",
                     token_names_the_patient)

        def session_has_a_start_time():
            row = supabase.rpc("patient_session_started", {"p_session_id": user.session_id}
                               ).execute().data
            assert row and row["created_at"], row

        if user is not None:
            _step(results, "the session's start time is readable (migration 18)",
                  session_has_a_start_time)

        def ending_sessions_stops_the_token():
            ended = supabase.rpc("end_patient_sessions", {"p_user_id": uid}).execute().data
            assert ended and ended["ended"] >= 1, ended
            try:
                gotrue.user_for_token(session.access_token)
            except gotrue.InvalidSession:
                return
            raise AssertionError("the token still works after its session was deleted")

        _step(results, "ending the user's sessions stops the access token at once",
              ending_sessions_stops_the_token)

        def ban_blocks_signing_in():
            gotrue.ban(uid)
            try:
                gotrue.mint_session(email)
            except Exception:  # a refusal of any kind
                return
            raise AssertionError("a banned user got a session")

        _step(results, "a banned user cannot get a new session", ban_blocks_signing_in)

        def unban_restores_it():
            gotrue.ensure_patient_user(email, user_id=uid)
            assert gotrue.mint_session(email).user_id == uid

        _step(results, "un-banning lets them sign in again", unban_restores_it)

        def lookup_by_email():
            found = supabase.rpc("find_auth_user_by_email", {"p_email": email.upper()}
                                 ).execute().data
            assert found and found["id"] == uid, found

        _step(results, "find_auth_user_by_email finds the user (migration 18)", lookup_by_email)
    finally:
        if uid is not None:
            try:
                get_supabase().auth.admin.delete_user(uid)
                results.append(("the throwaway user was deleted", True, ""))
            except Exception as exc:
                results.append(("the throwaway user was deleted", False,
                                f"{type(exc).__name__}: delete {uid} by hand"))
    return results


def main() -> int:
    results = run()
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
