"""``indexing_worker_count`` reads the knob itself, so a blank or padded value means one worker even
before ``runtime_threads`` has deleted the blank knobs."""

from __future__ import annotations

import pytest

from app.utils.indexing_workers import indexing_worker_count


@pytest.mark.parametrize("raw,expected", [("", 1), ("   ", 1), ("3", 3), (" 4 ", 4), ("0", 1), ("-2", 1)])
def test_the_worker_count(monkeypatch: pytest.MonkeyPatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("INDEXING_UVICORN_WORKERS", raw)
    assert indexing_worker_count() == expected


def test_unset_is_one_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INDEXING_UVICORN_WORKERS", raising=False)
    assert indexing_worker_count() == 1
