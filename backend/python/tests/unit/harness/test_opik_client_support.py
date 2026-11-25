"""EG-2: an Opik client a test builds does not outlive it (H8)."""

from __future__ import annotations

import threading

import pytest
from opik.api_objects import opik_client

from tests.support.opik_client import opik_global_client_ended, opik_spans_offline

_OPIK_THREADS = ("QueueConsumerThread", "ReplayManager")


@pytest.fixture(autouse=True)
def _opik_server_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """The clients built here ping a closed local port, not Opik Cloud."""
    monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://127.0.0.1:9/api")


def _opik_threads() -> set[threading.Thread]:
    return {t for t in threading.enumerate() if t.is_alive() and t.name.startswith(_OPIK_THREADS)}


def test_a_client_built_inside_is_ended_and_the_earlier_one_put_back() -> None:
    earlier = opik_client.get_current_client_raw()
    threads_before = _opik_threads()
    with opik_global_client_ended():
        built = opik_client.get_global_client()
        assert built is not earlier
        assert _opik_threads() - threads_before
    assert opik_client.get_current_client_raw() is earlier
    assert not _opik_threads() - threads_before, "the client's threads must have exited when the block ends"


def test_a_client_that_was_there_before_is_kept() -> None:
    with opik_global_client_ended():
        kept = opik_client.get_global_client()
        with opik_global_client_ended():
            assert opik_client.get_global_client() is kept
        assert opik_client.get_current_client_raw() is kept


def test_offline_spans_build_no_client() -> None:
    import opik

    earlier = opik_client.get_current_client_raw()
    with opik_global_client_ended(), opik_spans_offline():
        with opik.start_as_current_span(name="s", type="general", input={}) as span:
            assert span is not None
        assert opik_client.get_current_client_raw() is earlier
