import logging
import re
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Runtime configuration, loaded from environment variables / .env.

    Nothing here should ever hold a real secret value by default -- only
    names and safe defaults. Real values come from Railway's environment
    variable store in deployed environments.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"

    # Supabase (ca-central-1 project)
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Per-patient request budgets for a signed-in patient (see core/ratelimit.py).
    # In-memory, so per instance; 0 or less turns a budget off.
    patient_reads_per_minute: int = 60
    patient_writes_per_minute: int = 10

    # Consent types (comma-separated slugs) that may be granted. Which consents
    # exist is a product/legal decision that isn't made yet, so empty means any
    # well-formed slug is accepted; set it once the list is settled to turn
    # typos and invented types into 422s.
    consent_types: str = ""

    # AI (core/ai.py): Bedrock model and the per-clinic daily call cap. Credentials
    # are not settings -- boto3 reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from
    # the real process environment. The cap is a cost guard, so 0 means "no AI calls
    # at all" (a kill switch), never "unlimited".
    bedrock_model_id: str = "anthropic.claude-haiku-4-5-20251001-v1:0"
    ai_daily_call_cap_per_clinic: int = 200

    # Ed25519 signing key for the audit log: base64 of the 32-byte private seed.
    # Must be stable -- a regenerated key orphans every row already signed.
    # Generate with `python -m clinically_anchored_api.core.audit`. Deployed
    # environments set it via Railway; move to a KMS before real patient data.
    audit_signing_key: str = ""
    audit_key_id: str = "dev-1"
    # Public keys of past/other signing keys, so old rows stay verifiable after a
    # rotation (or when environments share a database): JSON object of
    # {"<key_id>": "<base64 Ed25519 public key>"}. Public keys aren't secret.
    # The current key's public half is derived automatically.
    audit_public_keys: str = ""

    # Email (core/mailer.py): sent through Amazon SES, in ca-central-1 unless SES_REGION says
    # otherwise. The ambient AWS_REGION is never used. Moving email out of Canada is a deliberate,
    # temporary override (it is logged as a warning when the first email is sent); delete the
    # variable to go back. Credentials are not settings -- boto3 reads AWS_ACCESS_KEY_ID /
    # AWS_SECRET_ACCESS_KEY from the real process environment. Not required at startup: without
    # a sender address a send is recorded as failed ("ses_not_configured") instead of the
    # service refusing to boot.
    ses_region: str = "ca-central-1"
    ses_from_address: str = ""
    ses_from_name: str = ""
    ses_configuration_set: str = ""

    # Patient accounts: passwordless email sign-in (core/patient_auth.py, api/patient_auth.py).
    # The anon key is the public one the web app already has; the api uses it only to exchange a
    # sign-in code for a session on a throwaway client. Not required at startup: without it the
    # sign-in routes answer 503.
    supabase_anon_key: str = ""
    # Standard Webhooks secret of the Supabase "Send Email" auth hook (api/auth_hooks.py), which
    # swallows every email Supabase Auth would send. Looks like "v1,whsec_<base64>".
    send_email_hook_secret: str = ""
    patient_code_ttl_seconds: int = 600  # a sign-in code works for 10 minutes
    patient_code_max_attempts: int = 5  # tries per code
    patient_code_cooldown_seconds: int = 60  # between two codes for one email
    patient_codes_per_hour: int = 5  # codes per email per hour
    patient_identity_max_attempts: int = 3  # wrong surgery dates before the account locks
    patient_session_max_age_days: int = 30  # a signed-in device stays signed in this long
    patient_fallback_link_ttl_hours: int = 24
    # Per client address, per minute, for each of: request a code, enter a code, use a fallback
    # link. In-memory, per instance (core/ratelimit.py); 0 or less turns it off.
    patient_auth_per_ip_per_minute: int = 30
    # How many reverse proxies sit in front of the api (Railway's edge: 1). With 0 the client
    # address is the socket's, which behind a proxy is the proxy's -- so every patient shares one
    # per-address budget. With N the address is the Nth from the RIGHT of X-Forwarded-For, the
    # one our own proxy added; anything a client put further left is ignored, so it cannot be
    # forged. Set it only to the true number of hops.
    trusted_proxy_hops: int = 0

    # Where patients open their links (the deployed web app). Used to build the
    # URLs the "issue link" endpoint returns. Development falls back to
    # http://localhost:3000; elsewhere the endpoint refuses until it's set.
    web_base_url: str = ""

    # CORS: the web app's origin(s), comma-separated in the env var
    allowed_origins: str = "http://localhost:3000"

    @field_validator("ses_region")
    @classmethod
    def _ses_region_is_a_region(cls, value: str) -> str:
        value = value.strip().lower() or "ca-central-1"
        if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", value):
            raise ValueError(f"SES_REGION {value!r} is not an AWS region name (e.g. us-east-2)")
        return value

    @model_validator(mode="after")
    def _require_secrets_outside_development(self) -> "Settings":
        if self.environment == "development":
            return self
        required = {
            "AUDIT_SIGNING_KEY": self.audit_signing_key,
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(
                f"ENVIRONMENT={self.environment!r} requires these to be set: {', '.join(missing)}"
            )
        return self

    @property
    def consent_types_list(self) -> list[str]:
        return [t.strip() for t in self.consent_types.split(",") if t.strip()]

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
