"""Unit tests run without the network (tests/unit/conftest.py, tests/support/network_guard.py).

A transformers test downloaded the PDF table model from the Hub in a worker thread. When the download stalled, the
thread outlived pytest-timeout and the pytest worker could not exit, so the whole shard ran into the job's limit.
"""

import socket
import time

import numpy as np
import pytest

from app.modules.parsers.pdf import table_detector
from tests.support import network_guard


@pytest.fixture
def refusals() -> list[str]:
    """What this test was refused; taken back out, or the conftest's check would fail the test for trying."""
    before = len(network_guard.blocked)
    seen: list[str] = []
    yield seen
    seen.extend(network_guard.blocked[before:])
    del network_guard.blocked[before:]


def test_a_connection_beyond_this_machine_is_refused_at_once(refusals: list[str]) -> None:
    started = time.monotonic()
    with pytest.raises(network_guard.NetworkBlockedError, match="192.0.2.1:443"):
        socket.create_connection(("192.0.2.1", 443), timeout=30)
    with pytest.raises(socket.gaierror, match="huggingface.co"):
        socket.getaddrinfo("huggingface.co", 443)
    assert time.monotonic() - started < 1
    assert network_guard.blocked[-2:] == ["connect 192.0.2.1:443", "resolve huggingface.co"]


def test_this_machine_is_still_reachable() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
        with socket.create_connection(("localhost", port), timeout=5):
            pass
    assert socket.getaddrinfo("10.0.0.5", 80)  # a numeric address needs no lookup
    assert socket.getaddrinfo("2130706433", 80)[0][4][0] == "127.0.0.1"  # nor does a legacy form of one


def test_a_documentation_name_resolves_but_is_not_reached(refusals: list[str]) -> None:
    [(_family, _type, _proto, _name, (address, port))] = socket.getaddrinfo("feeds.example.com", 443)
    assert port == 443 and address == "93.184.216.34"
    with pytest.raises(network_guard.NetworkBlockedError):
        socket.create_connection(("feeds.example.com", 443), timeout=30)
    assert network_guard.blocked[-1:] == ["connect 93.184.216.34:443"]


def test_a_pdf_page_outside_the_parser_tests_never_reaches_the_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    downloads: list[str] = []

    def hf_hub_download(repo_id: str, *_args: object, **_kwargs: object) -> str:
        downloads.append(repo_id)
        raise OSError("no network in unit tests")

    monkeypatch.setattr("huggingface_hub.hf_hub_download", hf_hub_download)
    monkeypatch.setattr(table_detector, "_sessions", {})

    assert table_detector.detect_layout_boxes(np.zeros((32, 32, 3), np.uint8)) is None
    assert downloads == []
