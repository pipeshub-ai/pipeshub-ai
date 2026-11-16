"""The contract script: its parser, its store detection, and its loop (batches, resume, exit codes) against a fake store."""

from __future__ import annotations

import io

import pytest

from app.modules.indexing.record_diagnostics import ALL_FIELDS
from app.scripts import record_diagnostics_contract as script


class FakeProvider:
    pass


class FakeArango(FakeProvider):
    __module__ = "app.services.graph_db.arango.fake"


def test_the_four_fields_are_the_ones_it_removes() -> None:
    assert set(ALL_FIELDS) == {"firstQueuedAt", "failedAt", "indexingAttempts", "pageCount"}


def test_an_unsupported_provider_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        script._store_of(FakeProvider())


@pytest.mark.parametrize("argv", [[], ["--org", "o"], ["--org", "o", "--check", "--apply"]])
def test_the_arguments_are_required(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        script.build_parser().parse_args(argv)


async def test_apply_runs_in_batches_until_a_short_one_and_ends_with_a_check(monkeypatch: pytest.MonkeyPatch) -> None:
    remaining = {"n": 25}

    async def remove(graph: object, org: str, limit: int | None) -> int:
        if limit is None:
            return remaining["n"]
        taken = min(limit, remaining["n"])
        remaining["n"] -= taken
        return taken

    monkeypatch.setitem(script._STORES, "arango", remove)
    out = io.StringIO()

    left = await script.apply(FakeArango(), "org", batch_size=10, out=out)

    assert left == 0
    lines = out.getvalue().splitlines()
    assert lines[:3] == ['{"removed": 10, "total": 10}', '{"removed": 10, "total": 20}', '{"removed": 5, "total": 25}']
    assert lines[-1] == '{"left": 0}'


async def test_apply_stops_when_a_batch_removes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"remove": 0}

    async def keeps_the_keys(graph: object, org: str, limit: int | None) -> int:
        if limit is None:
            return 7
        calls["remove"] += 1
        # a loop without the stall stop would never end: fail in an instant instead of waiting for a timeout
        assert calls["remove"] <= 3, "the script keeps running batches that remove nothing"
        return limit  # reports a full batch, changes nothing

    monkeypatch.setitem(script._STORES, "arango", keeps_the_keys)
    out = io.StringIO()

    assert await script.apply(FakeArango(), "org", batch_size=3, out=out) == 7
    assert calls["remove"] == 1
    assert '"stalled": true' in out.getvalue()


async def test_apply_reports_what_is_left(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stuck(graph: object, org: str, limit: int | None) -> int:
        return 3 if limit is None else 0

    monkeypatch.setitem(script._STORES, "arango", stuck)

    assert await script.apply(FakeArango(), "org", batch_size=10, out=io.StringIO()) == 3
