"""The shared database client talks HTTP/1.1 (core/db.py `_use_http1`): HTTP/2 over one connection
shared by the thread pool broke under load in production."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from supabase import create_client

from clinically_anchored_api.core.db import _use_http1

# A well-formed (unsigned) JWT, which is all supabase-py checks the shape of.
KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2ln"


@pytest.fixture
def server():
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 (http.server's name)
            seen.append({"path": self.path, "apikey": self.headers.get("apikey")})
            body = json.dumps([{"id": "1"}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", seen
    httpd.shutdown()
    httpd.server_close()


def test_the_default_session_is_http2_and_ours_is_not():
    client = create_client("https://example.supabase.co", KEY)
    assert client.postgrest.session._transport._pool._http2 is True  # the library's default
    _use_http1(client)
    pool = client.postgrest.session._transport._pool
    assert pool._http2 is False and pool._http1 is True


def test_the_swapped_session_keeps_the_address_and_credentials(server):
    url, seen = server
    client = create_client(url, KEY)
    _use_http1(client)
    assert client.table("patients").select("id").execute().data == [{"id": "1"}]
    assert seen[0]["path"].startswith("/rest/v1/patients") and seen[0]["apikey"] == KEY


def test_many_threads_can_share_it(server):
    url, seen = server
    client = create_client(url, KEY)
    _use_http1(client)

    def one(_):
        return client.table("patients").select("id").execute().data

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(one, range(64)))
    assert all(r == [{"id": "1"}] for r in results) and len(seen) == 64
