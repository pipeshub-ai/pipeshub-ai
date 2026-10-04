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
from app.scripts.kg_taxonomy import (
    EXIT_FAILED,
    EXIT_INVALID,
    build_parser,
    execute,
    run,
)
from app.services.graph_db.taxonomy import TAXONOMY_COLLECTIONS

TOPICS = "topics"


def _consolidator() -> MagicMock:
    c = MagicMock()
    group = DuplicateGroup(TOPICS, "o", TaxonomyNode(TOPICS, "w", "Bug bash", "o"),
                           (TaxonomyNode(TOPICS, "l", "bug-bash", "o"),))
    c.duplicate_groups = AsyncMock(side_effect=lambda coll, org: [group] if coll == TOPICS else [])
    c.merge = AsyncMock(side_effect=lambda *a, dry_run: MergeResult(3, dry_run))
    c.unmerge = AsyncMock(side_effect=lambda *a, dry_run: MergeResult(2, dry_run))
    c.legacy_nodes = AsyncMock(side_effect=lambda coll, org: [LegacyNode("L", "Pricing", 4)] if coll == TOPICS else [])
    c.migrate_legacy = AsyncMock(side_effect=lambda *a, dry_run: MigrationResult("T", 4, dry_run))
    c.unmigrate_legacy = AsyncMock(side_effect=lambda *a, dry_run: MigrationResult("T", 4, dry_run))
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


@pytest.mark.parametrize(("argv", "result"), [
    (["unmerge", "--org", "o", "--collection", TOPICS, "--loser", "l", "--apply"],
     MergeResult(2, False, index_refreshed=False)),
    (["unmigrate-legacy", "--org", "o", "--collection", TOPICS, "--legacy", "L", "--target", "T", "--apply"],
     MigrationResult("T", 4, False, index_refreshed=False)),
])
async def test_an_undo_that_left_the_index_unrefreshed_is_partial(argv: list[str], result: object) -> None:
    consolidator, out = _consolidator(), io.StringIO()
    consolidator.unmerge = consolidator.unmigrate_legacy = AsyncMock(return_value=result)
    code = await run(build_parser().parse_args(argv), consolidator, out)
    (line,) = [json.loads(x) for x in out.getvalue().splitlines()]
    assert code == 1
    assert line["index_refreshed"] is False and line["dry_run"] is False


async def test_an_undo_reports_what_it_moved() -> None:
    _, lines, _ = await _run(["unmerge", "--org", "o", "--collection", TOPICS, "--loser", "l"])
    assert lines == [{"action": "unmerge", "collection": TOPICS, "loser": "l", "edges": 2,
                      "dry_run": True, "index_refreshed": True}]
    _, lines, _ = await _run(["unmigrate-legacy", "--org", "o", "--collection", TOPICS,
                              "--legacy", "L", "--target", "T"])
    assert lines == [{"action": "unmigrate-legacy", "collection": TOPICS, "legacy": "L", "target": "T",
                      "edges": 4, "dry_run": True, "index_refreshed": True}]


async def test_a_failed_listing_skips_that_collection_and_is_partial() -> None:
    consolidator, out = _consolidator(), io.StringIO()
    consolidator.legacy_nodes = AsyncMock(side_effect=lambda coll, org: (
        [LegacyNode("L", "Pricing", 4)] if coll == TOPICS else (_ for _ in ()).throw(RuntimeError("timeout"))
    ))
    code = await run(build_parser().parse_args(["migrate-legacy", "--org", "o", "--apply"]), consolidator, out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert code == 1
    assert consolidator.migrate_legacy.await_count == 1
    assert len([x for x in lines if "timeout" in x.get("error", "")]) == len(TAXONOMY_COLLECTIONS) - 1


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.ensure_schema = AsyncMock()
    provider.disconnect = AsyncMock()
    return provider


async def _execute(argv: list[str], consolidator: MagicMock, provider: MagicMock) -> tuple[int, str]:
    err = io.StringIO()
    code = await execute(
        build_parser().parse_args(argv), provider, lambda store: consolidator,
        AsyncMock(return_value=None), MagicMock(), io.StringIO(), err,
    )
    return code, err.getvalue()


@pytest.mark.parametrize(("argv", "applies"), [
    (["duplicates", "--org", "o"], False),
    (["legacy", "--org", "o"], False),
    (["consolidate", "--org", "o"], False),
    (["consolidate", "--org", "o", "--apply"], True),
])
async def test_the_schema_is_applied_only_by_a_command_that_writes(argv: list[str], applies: bool) -> None:
    """A dry run must work against a read-only graph user."""
    provider = _provider()
    code, _ = await _execute(argv, _consolidator(), provider)
    assert code == 0
    assert provider.ensure_schema.await_count == int(applies)
    provider.disconnect.assert_awaited_once()


async def test_an_invalid_command_exits_2_and_a_crash_exits_3() -> None:
    consolidator, provider = _consolidator(), _provider()
    consolidator.merge = AsyncMock(side_effect=ValueError("loser not found"))
    argv = ["merge", "--org", "o", "--collection", TOPICS, "--winner", "w", "--loser", "x"]
    code, err = await _execute(argv, consolidator, provider)
    assert (code, json.loads(err)) == (EXIT_INVALID, {"error": "loser not found"})
    consolidator.merge = AsyncMock(side_effect=RuntimeError("graph down"))
    code, err = await _execute(argv, consolidator, provider)
    assert code == EXIT_FAILED and "graph down" in err
    assert EXIT_FAILED not in (0, 1, EXIT_INVALID)
    assert provider.disconnect.await_count == 2


async def test_an_unreachable_graph_exits_3(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    from app.containers import indexing
    from app.scripts import kg_taxonomy

    container = MagicMock()
    container.graph_provider = AsyncMock(side_effect=ConnectionError("refused"))
    monkeypatch.setattr(indexing.IndexingAppContainer, "init", MagicMock(return_value=container))
    assert await kg_taxonomy._main(["duplicates", "--org", "o"]) == EXIT_FAILED
    assert "refused" in json.loads(capsys.readouterr().err)["error"]


async def test_a_failed_schema_step_stops_an_apply_before_any_write() -> None:
    """ArangoDB's strict edge schema rejects mergedFrom until the schema is
    applied, so writing anyway would fail every move partway."""
    consolidator, provider = _consolidator(), _provider()
    provider.ensure_schema = AsyncMock(return_value=False)
    code, err = await _execute(["consolidate", "--org", "o", "--apply"], consolidator, provider)
    assert code == EXIT_FAILED and "schema" in json.loads(err)["error"]
    consolidator.duplicate_groups.assert_not_awaited()
    consolidator.merge.assert_not_awaited()
    provider.disconnect.assert_awaited_once()
