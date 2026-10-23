from functools import lru_cache

import httpx
from supabase import Client, create_client

from clinically_anchored_api.core.config import get_settings


@lru_cache
def get_supabase() -> Client:
    """Server-side Supabase client, using the service role key.

    This bypasses row-level security by design -- this service is what's
    responsible for authorization the database's RLS can't express (e.g.
    validating a patient's signed check-in link token). Never expose this
    client or its key to apps/web.
    """
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set. "
            "Copy .env.example to .env and fill in the project's values."
        )
    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    _use_http1(client)
    return client


def _use_http1(client: Client) -> None:
    """Make the database client talk HTTP/1.1.

    This one client is shared by every request, and FastAPI runs the (sync) endpoints on a thread
    pool, so many threads use it at once. postgrest-py builds its session with HTTP/2, which
    multiplexes every thread over a single connection, and httpcore's sync HTTP/2 connection
    breaks under that load: the server logged `KeyError` in `http2._response_closed`,
    `ConnectionTerminated` and `Broken pipe` and answered 500 to whichever requests were in flight
    (a patient page fires six at once, then again every 10 seconds). An HTTP/1.1 connection pool
    is thread-safe, at the cost of a few more connections."""
    rest = client.postgrest
    old = rest.session
    rest.session = httpx.Client(
        base_url=old.base_url,
        headers=old.headers,
        timeout=old.timeout,
        follow_redirects=True,
        http2=False,
    )
    old.close()
