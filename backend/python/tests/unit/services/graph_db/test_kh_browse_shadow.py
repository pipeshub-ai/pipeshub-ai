"""`KH_BROWSE_MODE=shadow`: answer with legacy and compare a sample of browses with split (docs/knowledge-hub-browse.md, Rollout).

The comparison runs detached, logs only counts (a row split listed may be one the viewer may not read), and adds a bounded
load to the database however busy the server is.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from app.services.graph_db.neo4j.neo4j_provider import (
    _KH_SPLIT_COMPARE,
    Neo4jProvider,
    _KhSplitUndecided,
)

LEGACY = {"rows": [{"id": "secret-a", "hasChildren": True}, {"id": "secret-b", "hasChildren": False}], "total": 2, "counts": {"r": 2}}


def _provider(split: dict[str, Any] | Exception, *, gate: asyncio.Event | None = None) -> tuple[Neo4jProvider, list[dict]]:
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_browse_shadow")
    asked: list[dict] = []

    async def page(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        asked.append(kwargs)
        if gate is not None:
            await gate.wait()
        if isinstance(split, Exception):
            raise split
        return split

    p.get_knowledge_hub_connector_page_v3 = page  # type: ignore[method-assign]
    return p, asked


@pytest.fixture(autouse=True)
def _nothing_running() -> None:
    Neo4jProvider._kh_shadow_running.clear()


@pytest.mark.parametrize(("raw", "sample"), [(None, 0.1), ("0.5", 0.5), ("2", 1.0), ("-1", 0.0), ("nan", 0.1), ("x", 0.1)])
def test_the_sample_is_a_fraction(monkeypatch: pytest.MonkeyPatch, raw: str | None, sample: float) -> None:
    if raw is None:
        monkeypatch.delenv("KH_BROWSE_SHADOW_SAMPLE", raising=False)
    else:
        monkeypatch.setenv("KH_BROWSE_SHADOW_SAMPLE", raw)
    assert Neo4jProvider._kh_shadow_sample() == sample


@pytest.mark.asyncio
async def test_pages_that_agree_are_logged_as_agreeing(caplog: pytest.LogCaptureFixture) -> None:
    p, asked = _provider(LEGACY)
    with caplog.at_level(logging.INFO):
        await p._kh_shadow_compare(LEGACY, {"app_id": "A", "grants_out": {}})
    assert "agree (2 rows)" in caplog.text
    # The split page is computed for the same request, and does not fill the caller's grants.
    assert asked == [{"app_id": "A", "grants_out": None, "browse_mode": _KH_SPLIT_COMPARE}]


@pytest.mark.asyncio
@pytest.mark.parametrize(("split", "expected"), [
    ({**LEGACY, "rows": LEGACY["rows"][:1]}, "only_legacy=1 only_split=0 order=False arrows=0 totals=False"),
    ({**LEGACY, "rows": LEGACY["rows"][::-1]}, "only_legacy=0 only_split=0 order=True arrows=0 totals=False"),
    ({**LEGACY, "rows": [{"id": "secret-a"}, {"id": "secret-b"}]}, "only_legacy=0 only_split=0 order=False arrows=1 totals=False"),
    ({**LEGACY, "total": 3}, "arrows=0 totals=True legacy_total=2 split_total=3"),
    # A row both pages hold is compared by its flags even when the other rows differ.
    ({**LEGACY, "rows": [{"id": "secret-a", "hasChildren": False}, {"id": "secret-c"}]}, "only_legacy=1 only_split=1 order=False arrows=1"),
])
async def test_a_difference_is_logged_by_its_counts_never_its_ids(caplog: pytest.LogCaptureFixture, split: dict, expected: str) -> None:
    p, _ = _provider(split)
    with caplog.at_level(logging.INFO):
        await p._kh_shadow_compare(LEGACY, {})
    assert "differ" in caplog.text and expected in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("cut", [{"totalCapped": True}, {"resume": {"id": "secret-b"}, "hasMore": True}])
async def test_a_legacy_page_cut_by_its_time_budget_is_not_compared(caplog: pytest.LogCaptureFixture, cut: dict) -> None:
    # Split has no budget, so the pages would differ by construction: no split browse is run and no difference is reported.
    p, asked = _provider(LEGACY)
    with caplog.at_level(logging.INFO):
        await p._kh_shadow_compare({**LEGACY, **cut}, {})
    assert asked == [] and "not compared" in caplog.text and "differ" not in caplog.text


@pytest.mark.asyncio
async def test_a_page_split_would_answer_with_legacy_is_not_compared(caplog: pytest.LogCaptureFixture) -> None:
    # Past split's bounds the page is legacy's: comparing legacy with itself would report agreement for a page split never made.
    p, _ = _provider(_KhSplitUndecided("the start has more children than the split browse reads"))
    with caplog.at_level(logging.INFO):
        await p._kh_shadow_compare(LEGACY, {})
    assert "not compared" in caplog.text and "agree" not in caplog.text and "split failed" not in caplog.text


@pytest.mark.asyncio
async def test_a_split_failure_is_logged_and_kept_from_the_viewer(caplog: pytest.LogCaptureFixture) -> None:
    p, _ = _provider(RuntimeError("secret-a is broken"))
    await p._kh_shadow_compare(LEGACY, {})
    assert "split failed (RuntimeError)" in caplog.text and "secret" not in caplog.text


@pytest.mark.asyncio
async def test_a_sampled_browse_is_compared_in_the_background_and_held_until_done(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KH_BROWSE_SHADOW_SAMPLE", "1")
    gate = asyncio.Event()
    p, asked = _provider(LEGACY, gate=gate)
    page = {"rows": [{"id": "a"}]}
    task = p._kh_shadow_start(page, {})
    assert task is not None and task in Neo4jProvider._kh_shadow_running
    page["rows"].clear()      # the viewer's page is theirs: the comparison holds a copy
    await asyncio.sleep(0)
    assert asked and not task.done()
    gate.set()
    await task
    await asyncio.sleep(0)
    assert task not in Neo4jProvider._kh_shadow_running


@pytest.mark.asyncio
async def test_an_unsampled_browse_is_not_compared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KH_BROWSE_SHADOW_SAMPLE", "0")
    p, asked = _provider(LEGACY)
    assert p._kh_shadow_start(LEGACY, {}) is None
    await asyncio.sleep(0)
    assert asked == []


@pytest.mark.asyncio
async def test_no_more_than_the_bound_are_compared_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KH_BROWSE_SHADOW_SAMPLE", "1")
    gate = asyncio.Event()
    p, _ = _provider(LEGACY, gate=gate)
    started = [p._kh_shadow_start(LEGACY, {}) for _ in range(Neo4jProvider._KH_SHADOW_MAX_RUNNING + 3)]
    running = [t for t in started if t is not None]
    assert len(running) == Neo4jProvider._KH_SHADOW_MAX_RUNNING
    gate.set()
    await asyncio.gather(*running)
    await asyncio.sleep(0)
    again = p._kh_shadow_start(LEGACY, {})      # a finished comparison frees its place
    assert again is not None
    await again
