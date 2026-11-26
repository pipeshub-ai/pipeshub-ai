"""The Kafka parallel-partition default: on under ``PARSE_ADMISSION=classes`` with leases and fair scheduling."""
from __future__ import annotations

import pytest

from app.services.messaging.config import messaging_env

ENVS = ("FAIR_SCHEDULING_PARALLEL_PARTITIONS", "PARSE_ADMISSION", "DISTRIBUTED_INDEXING_CONCURRENCY", "FAIR_SCHEDULING_ENABLED")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVS:
        monkeypatch.delenv(name, raising=False)


def test_off_by_default() -> None:
    assert messaging_env.fair_scheduling_parallel_partitions is False


def test_on_with_classes_leases_and_fair_scheduling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    assert messaging_env.fair_scheduling_parallel_partitions is True


def test_off_without_leases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.setenv("DISTRIBUTED_INDEXING_CONCURRENCY", "false")
    assert messaging_env.fair_scheduling_parallel_partitions is False


def test_off_without_fair_scheduling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.setenv("FAIR_SCHEDULING_ENABLED", "false")
    assert messaging_env.fair_scheduling_parallel_partitions is False


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("true", True), ("FALSE", False)])
def test_an_explicit_value_wins_over_the_default(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.setenv("FAIR_SCHEDULING_PARALLEL_PARTITIONS", value)
    assert messaging_env.fair_scheduling_parallel_partitions is expected


def test_legacy_with_an_explicit_true_is_still_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAIR_SCHEDULING_PARALLEL_PARTITIONS", "true")
    assert messaging_env.fair_scheduling_parallel_partitions is True
