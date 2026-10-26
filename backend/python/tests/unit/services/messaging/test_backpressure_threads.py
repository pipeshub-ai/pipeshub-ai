"""BackpressureCoordinator is signalled on the worker loop's thread and read on the main loop's."""

import threading
from unittest.mock import patch

from app.services.messaging.backpressure import BackpressureCoordinator


def test_signals_and_reads_from_two_threads_do_not_collide() -> None:
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
            thread.join(timeout=60)
    assert errors == []
