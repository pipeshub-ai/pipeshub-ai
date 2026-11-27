# ruff: noqa: A002
"""Metrics backend that records nothing. Same names as ``app.telemetry.backend``."""

from collections.abc import Sequence


class _Instrument:
    def inc(self, *labels: str, value: float = 1.0) -> None:
        pass

    def set(self, *labels: str, value: float) -> None:
        pass

    def clear(self) -> None:
        pass

    def observe(self, *labels: str, value: float) -> None:
        pass


NOOP = _Instrument()


class NoopBackend:
    def counter(self, name: str, help: str, labels: Sequence[str]) -> _Instrument:
        return NOOP

    def gauge(self, name: str, help: str, labels: Sequence[str]) -> _Instrument:
        return NOOP

    def histogram(
        self,
        name: str,
        help: str,
        labels: Sequence[str],
        buckets: Sequence[float],
    ) -> _Instrument:
        return NOOP

    def serialize(self) -> str:
        return ""

    def reset(self) -> None:
        pass


METRICS_BACKEND = NoopBackend()
