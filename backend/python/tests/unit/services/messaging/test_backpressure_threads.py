"""BackpressureCoordinator is signalled on the worker loop's thread and read on the main loop's."""

import logging
import threading
from unittest.mock import patch

import pytest

from app.services.messaging.backpressure import BackpressureCoordinator


def test_signals_and_reads_from_two_threads_do_not_collide(caplog: pytest.LogCaptureFixture) -> None:
    # Every signal here extends a pause and logs at INFO. With the root logger at INFO (tests/conftest.py imports
    # app.utils.logger) the 20,000 records, each written while the reader spins on the GIL, took over 30s and
    # tripped the suite timeout, so the race under test never got to finish.
    caplog.set_level(logging.WARNING, logger="app.services.messaging.backpressure")
    coordinator = BackpressureCoordinator()
    errors: list[BaseException] = []
    done = threading.Event()

    def signal_many() -> None:
        try:
            for i in range(20_000):
                coordinator.signal(f"svc-{i % 200}", 0.0001)
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    def read_many() -> None:
        try:
            while not done.is_set():
                coordinator.pause_remaining()
                _ = coordinator.paused_services
        except BaseException as exc:
            errors.append(exc)

    with patch("app.services.messaging.backpressure.get_default_downstream_feedback"):
        threads = [threading.Thread(target=signal_many), threading.Thread(target=read_many)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
