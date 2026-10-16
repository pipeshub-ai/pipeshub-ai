"""Metrics backend abstraction.

This is the ONLY module that knows about ``prometheus_client``; the rest of the
telemetry package (and every call site) works against these interfaces, so the
underlying metrics library can be swapped without touching call sites.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, Sequence

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from app.telemetry.flag import telemetry_enabled


class CounterHandle(Protocol):
    def inc(self, *labels: str, value: float = 1.0) -> None: ...


class GaugeHandle(Protocol):
    def set(self, *labels: str, value: float) -> None: ...
    def clear(self) -> None: ...


class HistogramHandle(Protocol):
    def observe(self, *labels: str, value: float) -> None: ...


class MetricsBackend(ABC):
    """A metrics registry capable of creating instruments and serializing them."""

    @abstractmethod
    def counter(self, name: str, help: str, labels: Sequence[str]) -> CounterHandle: ...

    @abstractmethod
    def gauge(self, name: str, help: str, labels: Sequence[str]) -> GaugeHandle: ...

    @abstractmethod
    def histogram(
        self, name: str, help: str, labels: Sequence[str], buckets: Sequence[float]
    ) -> HistogramHandle: ...

    @abstractmethod
    def serialize(self) -> str:
        """Serialize all registered metrics in the backend's exposition format."""
        ...

    @abstractmethod
    def reset(self) -> None:
        """Drop all recorded series."""
        ...


class _PromCounter:
    def __init__(self, metric: Counter) -> None:
        self._metric = metric

    def inc(self, *labels: str, value: float = 1.0) -> None:
        self._metric.labels(*labels).inc(value)


class _PromGauge:
    def __init__(self, metric: Gauge) -> None:
        self._metric = metric

    def set(self, *labels: str, value: float) -> None:
        self._metric.labels(*labels).set(value)

    def clear(self) -> None:
        self._metric.clear()


class _PromHistogram:
    def __init__(self, metric: Histogram) -> None:
        self._metric = metric

    def observe(self, *labels: str, value: float) -> None:
        self._metric.labels(*labels).observe(value)


class PrometheusBackend(MetricsBackend):
    """``prometheus_client`` implementation of :class:`MetricsBackend`."""

    def __init__(self) -> None:
        # Dedicated registry (not the global default) so the pusher controls
        # exactly what is exported and a double import can't raise
        # duplicate-registration errors.
        self._registry = CollectorRegistry()
        self._instruments: list[Counter | Gauge | Histogram] = []

    def counter(self, name: str, help: str, labels: Sequence[str]) -> CounterHandle:
        metric = Counter(name, help, list(labels), registry=self._registry)
        self._instruments.append(metric)
        return _PromCounter(metric)

    def gauge(self, name: str, help: str, labels: Sequence[str]) -> GaugeHandle:
        metric = Gauge(name, help, list(labels), registry=self._registry)
        self._instruments.append(metric)
        return _PromGauge(metric)

    def histogram(
        self, name: str, help: str, labels: Sequence[str], buckets: Sequence[float]
    ) -> HistogramHandle:
        metric = Histogram(name, help, list(labels), registry=self._registry, buckets=tuple(buckets))
        self._instruments.append(metric)
        return _PromHistogram(metric)

    def serialize(self) -> str:
        return generate_latest(self._registry).decode("utf-8")

    def reset(self) -> None:
        for metric in self._instruments:
            # clear() raises on unlabelled metrics
            if getattr(metric, "_labelnames", ()):
                metric.clear()


class _NoopCounter:
    def inc(self, *labels: str, value: float = 1.0) -> None:
        pass


class _NoopGauge:
    def set(self, *labels: str, value: float) -> None:
        pass

    def clear(self) -> None:
        pass


class _NoopHistogram:
    def observe(self, *labels: str, value: float) -> None:
        pass


class NoopBackend(MetricsBackend):
    """Backend used when telemetry is switched off: instruments accept every
    call and record nothing, so call sites need no flag checks of their own."""

    def counter(self, name: str, help: str, labels: Sequence[str]) -> CounterHandle:  # noqa: A002 - the MetricsBackend interface names it
        return _NoopCounter()

    def gauge(self, name: str, help: str, labels: Sequence[str]) -> GaugeHandle:  # noqa: A002 - the MetricsBackend interface names it
        return _NoopGauge()

    def histogram(
        self, name: str, help: str, labels: Sequence[str], buckets: Sequence[float]  # noqa: A002
    ) -> HistogramHandle:
        return _NoopHistogram()

    def serialize(self) -> str:
        return ""

    def reset(self) -> None:
        pass


# One backend per process, owned by the telemetry layer. Chosen at import because
# every metric module creates its instruments at import.
METRICS_BACKEND: MetricsBackend = PrometheusBackend() if telemetry_enabled() else NoopBackend()
