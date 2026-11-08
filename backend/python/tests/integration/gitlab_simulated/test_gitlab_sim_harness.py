"""The harness lets the connector reach the simulator's loopback address and nothing else on loopback.

Needs no graph store."""

import http.server
import threading
from collections.abc import Iterator

import pytest
import requests

from app.sources.client.http.address_guard import (
    AddressGuardAdapter,
    AddressNotAllowedError,
    guarded_async_client,
)
from tests.integration.gitlab_simulated.glsim.server import HOST
from tests.integration.gitlab_simulated.helpers import allow_the_simulator

# Loopback in other spellings: IPv4-mapped, 6to4 (2002:7f00:1::/48) and Teredo, whose client
# field is 127.0.0.1 stored inverted (0x80fffffe).
_LOOPBACK_IN_DISGUISE = ["[::ffff:127.0.0.1]", "[2002:7f00:1::1]", "[2001:0:808:808::80ff:fffe]", "localhost", "127.0.0.2"]


@pytest.fixture
def simulator_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer((HOST, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    allow_the_simulator(monkeypatch)
    yield f"http://{HOST}:{server.server_port}"
    server.shutdown()


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("http://", AddressGuardAdapter())
    return session


async def test_the_simulator_is_reached_through_the_guard(simulator_url: str) -> None:
    assert _session().get(f"{simulator_url}/api/v4/version").status_code == 200
    async with guarded_async_client() as client:
        assert (await client.get(f"{simulator_url}/api/v4/version")).status_code == 200


@pytest.mark.parametrize("host", _LOOPBACK_IN_DISGUISE)
async def test_loopback_in_any_other_spelling_stays_refused(simulator_url: str, host: str) -> None:
    port = simulator_url.rsplit(":", 1)[1]
    url = f"http://{host}:{port}/api/v4/version"
    with pytest.raises(AddressNotAllowedError):
        _session().get(url)
    async with guarded_async_client() as client:
        with pytest.raises(AddressNotAllowedError):
            await client.get(url)


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data/", "http://[fd00:ec2::254]/"])
async def test_cloud_metadata_stays_refused(simulator_url: str, url: str) -> None:
    del simulator_url
    with pytest.raises(AddressNotAllowedError):
        _session().get(url)
