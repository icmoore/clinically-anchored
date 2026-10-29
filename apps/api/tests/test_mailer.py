"""core/mailer.py: SES in ca-central-1, the outbox row for every attempt, demo clinics that send
nothing, reserved test domains refused. The boto3 client is replaced entirely: no AWS calls."""

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from clinically_anchored_api.core import mailer
from clinically_anchored_api.core.config import get_settings
from tests.fakes import CLINIC_A, PATIENT_A, FakeSupabase

CODE = "493817"


class FakeSes:
    def __init__(self):
        self.sent: list[dict] = []
        self.error: Exception | None = None

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        if self.error:
            raise self.error
        return {"MessageId": "ses-msg-1"}


@pytest.fixture
def ses(monkeypatch):
    fake = FakeSes()
    made: list[dict] = []

    def fake_client(service, **kwargs):
        made.append({"service": service, **kwargs})
        return fake

    monkeypatch.setattr(mailer.boto3, "client", fake_client)
    mailer._client.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "ses_from_address", "no-reply@clinic.example.org")
    monkeypatch.setattr(settings, "ses_from_name", "Lakeview Surgical")
    monkeypatch.setattr(settings, "ses_configuration_set", "")
    monkeypatch.setattr(settings, "web_base_url", "https://app.clinic.example.org")
    fake.clients_made = made
    yield fake
    mailer._client.cache_clear()


@pytest.fixture
def db():
    f = FakeSupabase()
    f.tables["clinics"][0]["name"] = "Lakeview Surgical"
    return f


def send(db, to="pat@mail.org", template="sign_in_code", clinic=CLINIC_A, **kw):
    kw.setdefault("code", CODE if template == "sign_in_code" else None)
    return mailer.send_email(db, clinic_id=clinic, patient_id=PATIENT_A, to=to,
                             template=template, **kw)


def outbox(db):
    return db.tables["email_outbox"]


# --- SES: region, credentials, request ---------------------------------------------


def test_uses_sesv2_in_ca_central_1_whatever_aws_region_says(ses, db, monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    send(db)
    made = ses.clients_made[0]
    assert made["service"] == "sesv2" and made["region_name"] == "ca-central-1"
    assert not {"aws_access_key_id", "aws_secret_access_key"} & made.keys()  # env chain only


def test_a_different_region_only_comes_from_ses_region_and_is_logged(ses, db, monkeypatch, caplog):
    monkeypatch.setenv("AWS_REGION", "eu-west-1")  # still ignored
    monkeypatch.setattr(get_settings(), "ses_region", "us-east-2")
    with caplog.at_level("WARNING"):
        send(db)
    assert ses.clients_made[0]["region_name"] == "us-east-2"
    assert "us-east-2" in caplog.text and "SES_REGION" in caplog.text
    mailer._client.cache_clear()
    caplog.clear()
    monkeypatch.setattr(get_settings(), "ses_region", "ca-central-1")
    send(db)
    assert ses.clients_made[-1]["region_name"] == "ca-central-1"
    assert "SES_REGION" not in caplog.text  # nothing to warn about in Canada


def test_ses_region_defaults_to_canada_and_must_look_like_a_region(monkeypatch):
    from pydantic import ValidationError

    from clinically_anchored_api.core.config import Settings

    assert Settings(_env_file=None).ses_region == "ca-central-1"
    monkeypatch.setenv("SES_REGION", " US-EAST-2 ")
    assert Settings(_env_file=None).ses_region == "us-east-2"
    monkeypatch.setenv("SES_REGION", "")
    assert Settings(_env_file=None).ses_region == "ca-central-1"
    for bad in ("ohio", "us east 2", "us-east-", "x"):
        monkeypatch.setenv("SES_REGION", bad)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


def test_client_is_created_once(ses, db):
    send(db)
    send(db)
    assert len(ses.clients_made) == 1


def test_request_shape(ses, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "ses_configuration_set", "trial")
    send(db, to="  Pat@Mail.ORG ")
    call = ses.sent[0]
    assert call["FromEmailAddress"] == "Lakeview Surgical <no-reply@clinic.example.org>"
    assert call["Destination"] == {"ToAddresses": ["pat@mail.org"]}
    assert call["ConfigurationSetName"] == "trial"
    content = call["Content"]["Simple"]
    assert CODE in content["Body"]["Text"]["Data"] and CODE in content["Body"]["Html"]["Data"]
    assert CODE not in content["Subject"]["Data"]


def test_sender_without_a_name_is_just_the_address(ses, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "ses_from_name", "")
    send(db)
    assert ses.sent[0]["FromEmailAddress"] == "no-reply@clinic.example.org"
    assert "ConfigurationSetName" not in ses.sent[0]


def test_invite_points_at_the_sign_in_page(ses, db):
    send(db, template="invite")
    text = ses.sent[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "https://app.clinic.example.org/sign-in" in text


def test_both_emails_link_the_patient_guide_on_the_web_apps_address(ses, db):
    send(db, template="invite")
    send(db, template="sign_in_code", code="123456")
    for sent in ses.sent:
        text = sent["Content"]["Simple"]["Body"]["Text"]["Data"]
        assert "New to this? Read the patient guide:" in text
        assert "https://app.clinic.example.org/guide" in text


def test_a_code_email_with_no_web_address_just_leaves_the_guide_line_out(ses, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "web_base_url", "")
    monkeypatch.setattr(get_settings(), "environment", "production")
    send(db, template="sign_in_code", code="123456")
    text = ses.sent[0]["Content"]["Simple"]["Body"]["Text"]["Data"]
    assert "123456" in text and "guide" not in text.lower()


# --- the outbox row ----------------------------------------------------------------


def test_a_sent_email_leaves_one_settled_row_without_the_body_or_the_code(ses, db):
    row = send(db)
    assert row["status"] == "sent" and row["provider_message_id"] == "ses-msg-1"
    assert row["sent_at"] is not None
    (stored,) = outbox(db)
    assert stored["status"] == "sent" and stored["recipient"] == "pat@mail.org"
    assert (stored["clinic_id"], stored["patient_id"], stored["template"]) == (
        CLINIC_A, PATIENT_A, "sign_in_code")
    assert not any(k.startswith("rendered_") and v for k, v in stored.items())
    assert CODE not in repr(stored)


def test_ses_errors_become_a_failed_row_with_a_code_and_no_exception(ses, db):
    ses.error = ClientError({"Error": {"Code": "MessageRejected", "Message": "pat@mail.org"}},
                            "SendEmail")
    row = send(db)
    assert row["status"] == "failed" and row["error"] == "MessageRejected"
    assert "pat@mail.org" not in repr(outbox(db)[0]["error"])  # the provider's text is not kept


def test_network_errors_are_recorded_by_class(ses, db):
    ses.error = EndpointConnectionError(endpoint_url="https://email.ca-central-1.amazonaws.com")
    assert send(db)["error"] == "EndpointConnectionError"


def test_no_sender_address_is_a_failed_row_not_a_crash(ses, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "ses_from_address", "")
    row = send(db)
    assert row["status"] == "failed" and row["error"] == "ses_not_configured"
    assert ses.sent == []


def test_an_invite_with_no_web_address_fails_cleanly(ses, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "web_base_url", "")
    monkeypatch.setattr(get_settings(), "environment", "production")
    assert send(db, template="invite")["error"] == "web_base_url_missing"
    assert ses.sent == []


def test_a_database_error_is_not_swallowed(ses, db):
    db.fail_insert["email_outbox"] = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        send(db)
    assert ses.sent == []  # nothing was sent that we cannot account for


def test_an_unknown_clinic_is_an_error(ses, db):
    with pytest.raises(LookupError):
        send(db, clinic="cccccccc-cccc-cccc-cccc-cccccccccccc")


# --- bad addresses and reserved domains --------------------------------------------


@pytest.mark.parametrize("to", [
    "", "nobody", "a@b", "a b@c.org", "a@b.org\nBcc: x@y.org", "<a@b.org>", "a@b@c.org",
    "a@test", "a@example", "x" * 250 + "@b.org",
])
def test_malformed_addresses_are_refused_and_nothing_is_sent(ses, db, to):
    row = send(db, to=to)
    assert row["status"] == "refused" and row["error"] == "invalid_address"
    assert ses.sent == []


@pytest.mark.parametrize("to", [
    "a@example.com", "a@EXAMPLE.org", "a@example.net", "a@mail.example.com", "a@x.test",
    "a@host.invalid", "a@foo.example", "a@a.b.test",
])
def test_reserved_test_domains_are_refused_in_a_normal_clinic(ses, db, to):
    row = send(db, to=to)
    assert row["status"] == "refused" and row["error"] == "reserved_domain"
    assert ses.sent == []
    assert CODE not in repr(outbox(db))


@pytest.mark.parametrize("to", ["a@gmail.com", "a@notexample.com", "a@example.com.au",
                                "a@contest.org", "a@clinic.example.org.uk"])
def test_ordinary_domains_that_merely_look_similar_are_sent(ses, db, to):
    assert send(db, to=to)["status"] == "sent"


# --- demo clinics ------------------------------------------------------------------


def test_a_demo_clinic_sends_nothing_and_keeps_the_email_including_the_code(ses, db):
    db.tables["clinics"][0]["is_demo"] = True
    row = send(db, to="pat@example.com")  # a reserved domain is fine: nothing is sent
    assert ses.sent == [] and ses.clients_made == []
    assert row["status"] == "demo"
    (stored,) = outbox(db)
    assert CODE in stored["rendered_text"] and CODE in stored["rendered_html"]
    assert "Lakeview Surgical" in stored["rendered_subject"]
    assert stored.get("provider_message_id") is None and stored.get("sent_at") is None


def test_a_normal_clinic_never_stores_a_rendered_email(ses, db):
    send(db)
    assert all(not r.get("rendered_text") for r in outbox(db))


# --- the smoke-test command --------------------------------------------------------


def test_the_smoke_test_sends_one_test_invite_and_touches_no_database(ses, capsys):
    assert mailer.main(["--to", "me@mail.org"]) == 0
    assert "ses-msg-1" in capsys.readouterr().out
    subject = ses.sent[0]["Content"]["Simple"]["Subject"]["Data"]
    assert subject.startswith("[TEST]")


def test_the_smoke_test_refuses_reserved_domains_and_supports_a_dry_run(ses, capsys):
    assert mailer.main(["--to", "me@example.com"]) == 2
    assert mailer.main(["--to", "me@mail.org", "--dry-run"]) == 0
    assert ses.sent == []
    assert "[TEST]" in capsys.readouterr().out


def test_the_smoke_test_reports_a_failure_without_a_traceback(ses, capsys):
    ses.error = ClientError({"Error": {"Code": "MessageRejected", "Message": "x"}}, "SendEmail")
    assert mailer.main(["--to", "me@mail.org"]) == 1
    assert "MessageRejected" in capsys.readouterr().err


def test_ses_settings_come_from_the_environment(monkeypatch):
    from clinically_anchored_api.core.config import Settings

    monkeypatch.setenv("SES_FROM_ADDRESS", "a@b.org")
    monkeypatch.setenv("SES_FROM_NAME", "Name")
    monkeypatch.setenv("SES_CONFIGURATION_SET", "cs")
    s = Settings(_env_file=None)
    assert (s.ses_from_address, s.ses_from_name, s.ses_configuration_set) == (
        "a@b.org", "Name", "cs")
    monkeypatch.delenv("SES_FROM_ADDRESS")
    assert Settings(_env_file=None).ses_from_address == ""
