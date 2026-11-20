"""``unindex_record`` against a recording fake: what it removes, in which order, and that a failed run can be repeated."""
from __future__ import annotations

import pytest

from tests.support.unindex_rig import Rig

STEPS = [
    "links", "refs_saved", "classification", "mentions", "fields", "holders", "stage_states", "release", "taxonomy_points",
    "refs_cleared",
]


@pytest.mark.asyncio
async def test_every_step_runs_in_order_and_the_title_point_is_kept() -> None:
    rig = Rig()
    await rig.run()
    assert rig.calls == STEPS
    assert "title_deleted" not in rig.calls


@pytest.mark.asyncio
async def test_the_taxonomy_refs_are_read_before_any_edge_is_cleared_and_reach_the_point_cleanup() -> None:
    rig = Rig()
    await rig.run()
    assert rig.calls.index("links") < rig.calls.index("classification")
    refs = rig.points.await_args.args[-1]
    assert {r["id"] for r in refs} == {"cat-1", "top-1"}


@pytest.mark.asyncio
async def test_a_failed_taxonomy_read_deletes_nothing_and_the_retry_finds_the_refs() -> None:
    rig = Rig()
    rig.fail_at = "links"
    with pytest.raises(RuntimeError):
        await rig.run()
    assert rig.calls == ["links"]
    rig.calls.clear()
    await rig.run()
    assert rig.calls[:1] == ["links"] and rig.calls[-1] == "refs_cleared"
    assert rig.points.await_args.args[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("step", STEPS)
async def test_a_run_that_fails_at_any_step_is_completed_by_the_retry(step: str) -> None:
    rig = Rig()
    rig.fail_at = step
    with pytest.raises(RuntimeError):
        await rig.run()
    rig.calls.clear()
    await rig.run()
    assert rig.calls[-2:] == ["taxonomy_points", "refs_cleared"]
    refs = rig.points.await_args.args[-1]
    assert {r["id"] for r in refs} == {"cat-1", "top-1"}, "the retry must still know the links the first run cleared"


@pytest.mark.asyncio
async def test_a_twin_keeps_the_stage_states_but_the_vectors_and_content_release_runs() -> None:
    rig = Rig(holders=["rec-1", "twin-1"])
    await rig.run()
    assert "stage_states" not in rig.calls
    assert "release" in rig.calls


@pytest.mark.asyncio
async def test_a_record_with_no_vrid_skips_the_content_steps_only() -> None:
    rig = Rig()
    await rig.run({"_key": "rec-1", "orgId": "org-1"})
    assert rig.calls == ["links", "refs_saved", "classification", "mentions", "fields", "taxonomy_points", "refs_cleared"]


@pytest.mark.asyncio
async def test_a_record_with_no_taxonomy_links_has_no_point_cleanup() -> None:
    rig = Rig(links=[])
    await rig.run()
    assert "taxonomy_points" not in rig.calls and "refs_saved" not in rig.calls
