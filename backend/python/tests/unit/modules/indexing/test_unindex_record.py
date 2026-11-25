"""``unindex_record`` against a recording fake: what it removes, in which order, and that a failed run can be repeated."""
from __future__ import annotations

import pytest

from tests.support.unindex_rig import Rig

STEPS = ["links", "classification", "mentions", "fields", "holders", "stage_states", "release", "taxonomy_points"]


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
    assert rig.calls[:1] == ["links"] and rig.calls[-1] == "taxonomy_points"
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
    # The retry runs every step again. Once the edges went, it reads no links, so it names no points (see the module
    # docstring of ``unindex``).
    assert rig.calls[:4] == ["links", "classification", "mentions", "fields"] and rig.calls[-1] in ("release", "taxonomy_points")


@pytest.mark.asyncio
async def test_the_refs_are_not_saved_on_the_record() -> None:
    """The retry of a run that failed before the edges went still names the points; none of it is stored on the record."""
    rig = Rig()
    await rig.run()
    rig.graph.update_node.assert_awaited_once()
    assert "pendingTaxonomyRefs" not in rig.graph.update_node.await_args.args[2]


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
    assert rig.calls == ["links", "classification", "mentions", "fields", "taxonomy_points"]


@pytest.mark.asyncio
async def test_a_record_with_no_taxonomy_links_has_no_point_cleanup() -> None:
    rig = Rig(links=[])
    await rig.run()
    assert "taxonomy_points" not in rig.calls


@pytest.mark.asyncio
async def test_a_retry_after_the_detach_releases_the_virtual_record_id_the_event_carries() -> None:
    """The detach is written before the points and stored content go: a record read again has no id, the event still does."""
    rig = Rig()
    await rig.run({"_key": "rec-1", "orgId": "org-1"}, fallback_vrid="v-old")
    assert "release" in rig.calls
    assert rig.processor._release_skipped_content.await_args.args == ("org-1", "rec-1", "v-old")


@pytest.mark.asyncio
async def test_the_records_own_virtual_record_id_wins_over_the_events() -> None:
    rig = Rig()
    await rig.run({"_key": "rec-1", "orgId": "org-1", "virtualRecordId": "v-1"}, fallback_vrid="v-old")
    assert rig.processor._release_skipped_content.await_args.args == ("org-1", "rec-1", "v-1")


@pytest.mark.asyncio
async def test_a_twin_that_still_holds_the_events_id_keeps_the_stage_states() -> None:
    rig = Rig(holders=["twin-1"])
    await rig.run({"_key": "rec-1", "orgId": "org-1"}, fallback_vrid="v-old")
    assert "stage_states" not in rig.calls
