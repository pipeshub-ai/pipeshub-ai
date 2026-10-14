"""The web-fixtures service's fault modes, driven through the test client.

The Web and RSS fault tests trust that a fault set on a page fires exactly as
asked and nowhere else. These run the real server in a thread, so they need no
stack.
"""

from __future__ import annotations

import http.client
import importlib.util
import sys
import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

from helper.web_fixtures import WebFixtures

# Loaded by path: helper/ is on sys.path in this suite, so the name
# ``web_fixtures`` already means the client module there.
_SERVER = Path(__file__).resolve().parents[1] / "web_fixtures" / "server.py"
_spec = importlib.util.spec_from_file_location("web_fixtures_server", _SERVER)
assert _spec and _spec.loader
server_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server_module
_spec.loader.exec_module(server_module)

PAGE = "<html><head><title>Guide</title></head><body>" + "guide text " * 50 + "</body></html>"


@pytest.fixture
def fixtures(tmp_path: Path) -> Iterator[WebFixtures]:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "index.html").write_text("<html>home</html>")
    (tmp_path / "docs" / "guide.html").write_text(PAGE)
    handler = server_module.make_handler(server_module.Site(tmp_path), "http://fixtures")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield WebFixtures(test_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=5)
    finally:
        server.shutdown()
        server.server_close()


def _get(fixtures: WebFixtures, path: str, **kwargs: object) -> requests.Response:
    return requests.get(f"{fixtures.test_url}/{path}", timeout=kwargs.pop("timeout", 5), **kwargs)


def test_rate_limit_answers_429_with_retry_after_then_the_page(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", status=429, retry_after=3, times=2)

    for _ in range(2):
        response = _get(fixtures, "docs/guide.html")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "3"
    assert _get(fixtures, "docs/guide.html").text == PAGE
    assert fixtures.fault_hits("docs/guide.html") == 2


def test_a_fault_touches_only_its_own_path(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", status=503)

    assert _get(fixtures, "docs/").status_code == 200
    assert _get(fixtures, "docs/guide.html").status_code == 503
    assert fixtures.fault_hits("docs/index.html") == 0


def test_a_folder_url_and_its_index_page_share_a_fault(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/", status=500, times=1)

    assert _get(fixtures, "docs/index.html").status_code == 500
    assert _get(fixtures, "docs/").status_code == 200
    assert fixtures.fault_hits("docs/index.html") == 1


def test_without_times_the_fault_lasts_until_removed(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", status=502)
    assert [_get(fixtures, "docs/guide.html").status_code for _ in range(3)] == [502, 502, 502]

    fixtures.remove_fault("docs/guide.html")
    assert _get(fixtures, "docs/guide.html").status_code == 200
    assert fixtures.fault_hits("docs/guide.html") == 0


def test_a_head_size_check_leaves_the_fault_for_the_download(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", status=429, times=1)

    assert requests.head(f"{fixtures.test_url}/docs/guide.html", timeout=5).status_code == 200
    assert _get(fixtures, "docs/guide.html").status_code == 429


def test_delay_holds_the_answer_back(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", delay=0.5, times=1)

    started = time.monotonic()
    response = _get(fixtures, "docs/guide.html")
    assert time.monotonic() - started >= 0.5
    assert response.text == PAGE

    started = time.monotonic()
    _get(fixtures, "docs/guide.html")
    assert time.monotonic() - started < 0.5


def test_a_client_that_gives_up_on_a_slow_page_does_not_break_the_server(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", delay=1.0, times=1)

    with pytest.raises(requests.exceptions.ReadTimeout):
        _get(fixtures, "docs/guide.html", timeout=0.2)
    time.sleep(1.0)
    assert _get(fixtures, "docs/guide.html").text == PAGE


def test_truncate_drops_the_connection_short_of_content_length(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", truncate=True, times=1)

    host, port = fixtures.test_url.removeprefix("http://").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=5)
    connection.request("GET", "/docs/guide.html")
    response = connection.getresponse()
    assert int(response.headers["Content-Length"]) == len(PAGE)
    with pytest.raises(http.client.IncompleteRead) as cut:
        response.read()
    assert len(cut.value.partial) == len(PAGE) // 2
    connection.close()

    assert _get(fixtures, "docs/guide.html").text == PAGE


def test_partial_serves_half_the_page_as_if_it_were_whole(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", partial=True, times=1)

    response = _get(fixtures, "docs/guide.html")
    assert response.status_code == 200
    assert response.text == PAGE[: len(PAGE) // 2]
    assert int(response.headers["Content-Length"]) == len(PAGE) // 2


def test_reset_drops_every_fault(fixtures: WebFixtures) -> None:
    fixtures.add_fault("docs/guide.html", status=500)
    fixtures.reset()

    assert _get(fixtures, "docs/guide.html").status_code == 200
    assert fixtures.fault_hits("docs/guide.html") == 0


@pytest.mark.parametrize(
    "spec",
    [
        {"status": 200},
        {"status": 500, "truncate": True},
        {"times": 0, "status": 500},
        {"status": 500, "delay": -1},
        {},
    ],
)
def test_a_fault_that_cannot_happen_is_refused(fixtures: WebFixtures, spec: dict) -> None:
    with pytest.raises(ValueError):
        fixtures.add_fault("docs/guide.html", **spec)
    assert _get(fixtures, "docs/guide.html").status_code == 200
