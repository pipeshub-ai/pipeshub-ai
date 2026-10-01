"""The taxonomy consolidation command: dry run unless ``--apply``, every
taxonomy collection when none is named, one JSON line per action."""
from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.entity_resolution.consolidation import (
    DuplicateGroup,
    LegacyNode,
    MergeResult,
    MigrationResult,
    TaxonomyNode,
)
from app.scripts.kg_taxonomy import build_parser, run
from app.services.graph_db.taxonomy import TAXONOMY_COLLECTIONS

TOPICS = "topics"


def _consolidator() -> MagicMock:
    c = MagicMock()
    group = DuplicateGroup(TOPICS, "o", TaxonomyNode(TOPICS, "w", "Bug bash", "o"),
                           (TaxonomyNode(TOPICS, "l", "bug-bash", "o"),))
    c.duplicate_groups = AsyncMock(side_effect=lambda coll, org: [group] if coll == TOPICS else [])
    c.merge = AsyncMock(side_effect=lambda *a, dry_run: MergeResult(3, dry_run))
    c.unmerge = AsyncMock(return_value=2)
    c.legacy_nodes = AsyncMock(side_effect=lambda coll, org: [LegacyNode("L", "Pricing", 4)] if coll == TOPICS else [])
    c.migrate_legacy = AsyncMock(side_effect=lambda *a, dry_run: MigrationResult("T", 4, dry_run))
    c.unmigrate_legacy = AsyncMock(return_value=4)
    return c


async def _run(argv: list[str]) -> tuple[int, list[dict], MagicMock]:
    consolidator, out = _consolidator(), io.StringIO()
    code = await run(build_parser().parse_args(argv), consolidator, out)
    return code, [json.loads(line) for line in out.getvalue().splitlines()], consolidator


async def test_consolidate_is_a_dry_run_by_default() -> None:
    code, lines, c = await _run(["consolidate", "--org", "o"])
    assert code == 0
    assert lines == [{"action": "merge", "collection": TOPICS, "winner": "w", "loser": "l", "edges": 3,
                      "dry_run": True, "index_refreshed": True}]
    assert c.merge.await_args.kwargs["dry_run"] is True
    assert c.duplicate_groups.await_count == len(TAXONOMY_COLLECTIONS)


async def test_apply_writes() -> None:
    _, lines, c = await _run(["consolidate", "--org", "o", "--collection", TOPICS, "--apply"])
    assert c.merge.await_args.kwargs["dry_run"] is False and lines[0]["dry_run"] is False
    assert c.duplicate_groups.await_count == 1


async def test_listing_commands_never_write() -> None:
    _, dupes, c = await _run(["duplicates", "--org", "o"])
    assert dupes == [{"collection": TOPICS, "winner": "w", "winner_name": "Bug bash",
                      "loser": "l", "loser_name": "bug-bash"}]
    c.merge.assert_not_awaited()
    _, legacy, c = await _run(["legacy", "--org", "o"])
    assert legacy == [{"collection": TOPICS, "legacy": "L", "name": "Pricing", "records": 4}]
    c.migrate_legacy.assert_not_awaited()


async def test_migrate_and_undo() -> None:
    _, lines, c = await _run(["migrate-legacy", "--org", "o", "--apply"])
    assert lines[0]["target"] == "T" and lines[0]["dry_run"] is False
    _, lines, c = await _run(["unmigrate-legacy", "--org", "o", "--collection", TOPICS,
                              "--legacy", "L", "--target", "T"])
    c.unmigrate_legacy.assert_awaited_once_with(TOPICS, "o", "L", "T", dry_run=True)


async def test_merge_and_unmerge_need_a_collection() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["merge", "--org", "o", "--winner", "w", "--loser", "l"])
    _, _, c = await _run(["unmerge", "--org", "o", "--collection", TOPICS, "--loser", "l", "--apply"])
    c.unmerge.assert_awaited_once_with(TOPICS, "o", "l", dry_run=False)


def test_unknown_collection_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["duplicates", "--org", "o", "--collection", "records"])


async def test_a_failed_merge_is_reported_and_the_rest_carry_on() -> None:
    consolidator, out = _consolidator(), io.StringIO()
    group = DuplicateGroup(TOPICS, "o", TaxonomyNode(TOPICS, "w", "Bug bash", "o"),
                           (TaxonomyNode(TOPICS, "l1", "bug-bash", "o"), TaxonomyNode(TOPICS, "l2", "BUG BASH", "o")))
    consolidator.duplicate_groups = AsyncMock(side_effect=lambda coll, org: [group] if coll == TOPICS else [])
    consolidator.merge = AsyncMock(side_effect=[RuntimeError("graph down"), MergeResult(1, False)])
    code = await run(build_parser().parse_args(["consolidate", "--org", "o", "--apply"]), consolidator, out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert code == 1
    assert lines[0]["loser"] == "l1" and "graph down" in lines[0]["error"]
    assert lines[1]["loser"] == "l2" and lines[1]["edges"] == 1


async def test_an_unrefreshed_index_is_a_partial_failure() -> None:
    consolidator, out = _consolidator(), io.StringIO()
    consolidator.merge = AsyncMock(return_value=MergeResult(2, False, index_refreshed=False))
    code = await run(build_parser().parse_args(
        ["merge", "--org", "o", "--collection", TOPICS, "--winner", "w", "--loser", "l", "--apply"],
    ), consolidator, out)
    assert code == 1


async def test_an_invalid_single_merge_is_an_invalid_command() -> None:
    """It reaches _main, which exits 2; bulk commands carry on instead."""
    consolidator = _consolidator()
    consolidator.merge = AsyncMock(side_effect=ValueError("loser not found"))
    with pytest.raises(ValueError, match="not found"):
        await run(build_parser().parse_args(
            ["merge", "--org", "o", "--collection", TOPICS, "--winner", "w", "--loser", "x"],
        ), consolidator, io.StringIO())
