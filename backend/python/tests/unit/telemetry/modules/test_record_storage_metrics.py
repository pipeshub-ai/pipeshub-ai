"""Unit tests for app.telemetry.modules.record_storage_metrics."""

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.record_storage_metrics import record_rejected_too_large

_NAME = "pipeshub_record_storage_rejected_too_large_total"


def _value() -> float:
    for line in METRICS_BACKEND.serialize().splitlines():
        if line.startswith(_NAME + '{status="413"} '):
            return float(line.split()[-1])
    return 0.0


def test_each_call_counts_one_record() -> None:
    before = _value()
    record_rejected_too_large(413)
    record_rejected_too_large(413)
    assert _value() == before + 2
