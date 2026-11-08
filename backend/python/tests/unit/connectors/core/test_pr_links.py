"""PR -MODIFIES-> code file: written where the PR's file list is read, with its own cleanup."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.connectors.core.base.data_processor.pr_links import write_modifies
from app.models.blocks import FileChangeMetadata

pytestmark = pytest.mark.asyncio

RECORDS = CollectionNames.RECORDS.value
MODIFIES = RecordRelations.MODIFIES.value


def _store(code_files: dict[str, str]) -> MagicMock:
    """*code_files*: external id -> record id, in the PR's connector; one in another connector is planted too."""
    rows = [{"_key": rid, "externalRecordId": ext, "connectorId": "conn-1"} for ext, rid in code_files.items()]
    rows.append({"_key": "rec-foreign", "externalRecordId": "/1/blob/src/a.py", "connectorId": "conn-other"})
    store = MagicMock()
    store.get_nodes_by_field_in = AsyncMock(
        side_effect=lambda collection, field, values, return_fields=None, **_: [r for r in rows if r[field] in values])
    store.delete_edges_by_relationship_types = AsyncMock(return_value=0)
    store.batch_create_edges = AsyncMock()
    return store


PR = SimpleNamespace(id="pr-1", connector_id="conn-1", merged_at=1_700)
CHANGES = [FileChangeMetadata(path="src/a.py", status="modified", additions=3, deletions=1),
           FileChangeMetadata(path="src/new.py", previous_path="src/old.py", status="renamed", additions=0,
                              deletions=0),
           FileChangeMetadata(path="docs/not-indexed.md", status="added")]


def _ext(path: str) -> str:
    return f"/1/blob/{path}"


async def test_each_changed_file_that_is_a_code_record_gets_one_edge_with_the_change() -> None:
    store = _store({"/1/blob/src/a.py": "rec-a", "/1/blob/src/new.py": "rec-new"})
    assert await write_modifies(store, PR, CHANGES, _ext) == (2, 1), "docs/not-indexed.md matched no record"
    edges = store.batch_create_edges.await_args.args[0]
    # Written as the processor writes links; the providers store a link type on RECORD_LINK / recordLinks.
    assert store.batch_create_edges.await_args.kwargs == {"collection": CollectionNames.NODE_RELATIONS.value}
    by_to = {e["_to"]: e for e in edges}
    assert set(by_to) == {f"{RECORDS}/rec-a", f"{RECORDS}/rec-new"}, "a missing file is skipped, never a placeholder"
    a = by_to[f"{RECORDS}/rec-a"]
    assert (a["_from"], a["relationshipType"], a["status"], a["additions"], a["deletions"], a["mergedAt"]) == (
        f"{RECORDS}/pr-1", MODIFIES, "modified", 3, 1, 1_700)
    assert by_to[f"{RECORDS}/rec-new"]["previousPath"] == "src/old.py"


async def test_a_file_of_another_connector_is_never_linked() -> None:
    store = _store({})
    assert await write_modifies(store, PR, CHANGES[:1], _ext) == (0, 1)
    store.batch_create_edges.assert_not_awaited()


async def test_a_rewrite_replaces_only_this_prs_modifies_edges() -> None:
    """Idempotent per PR: its old MODIFIES edges go first; its other links are the processor's and stay."""
    store = _store({"/1/blob/src/a.py": "rec-a"})
    await write_modifies(store, PR, CHANGES[:1], _ext)
    await write_modifies(store, PR, CHANGES[:1], _ext)
    for call in store.delete_edges_by_relationship_types.await_args_list:
        assert call.kwargs == {"from_id": "pr-1", "from_collection": RECORDS,
                               "collection": CollectionNames.NODE_RELATIONS.value, "relationship_types": [MODIFIES]}
    assert store.delete_edges_by_relationship_types.await_count == 2


async def test_the_records_are_read_before_the_old_edges_go_and_a_failed_read_keeps_them() -> None:
    """Without explicit transactions the delete commits on its own: a lookup that failed after it would leave the
    PR with no edges, so the lookup runs first and raises."""
    store = _store({"/1/blob/src/a.py": "rec-a"})
    calls = MagicMock()
    calls.attach_mock(store.get_nodes_by_field_in, "lookup")
    calls.attach_mock(store.delete_edges_by_relationship_types, "delete")
    await write_modifies(store, PR, CHANGES[:1], _ext)
    assert [c[0] for c in calls.mock_calls] == ["lookup", "delete"]
    assert store.get_nodes_by_field_in.await_args.kwargs == {"raise_on_error": True}

    store.get_nodes_by_field_in.side_effect = RuntimeError("graph unavailable")
    store.delete_edges_by_relationship_types.reset_mock()
    with pytest.raises(RuntimeError):
        await write_modifies(store, PR, CHANGES[:1], _ext)
    store.delete_edges_by_relationship_types.assert_not_awaited()


async def test_a_pr_whose_files_are_gone_drops_its_old_edges() -> None:
    store = _store({})
    assert await write_modifies(store, PR, [], _ext) == (0, 0)
    store.delete_edges_by_relationship_types.assert_awaited_once()
    store.batch_create_edges.assert_not_awaited()


async def test_unmatched_files_are_logged_with_the_counts(caplog) -> None:
    from app.connectors.core.base.data_processor.pr_links import (
        Linked,
        link_changed_files,
    )
    from app.models.blocks import BlockGroup, GroupSubType, GroupType

    processor = MagicMock()
    processor.on_pr_file_changes = AsyncMock(return_value=Linked(1, 2))
    groups = [BlockGroup(index=i, type=GroupType.TEXT_SECTION, sub_type=GroupSubType.PR_FILE_CHANGE, data="x",
                         file_change_metadata=c) for i, c in enumerate(CHANGES)]
    with caplog.at_level("INFO"):
        assert await link_changed_files(processor, PR, groups, _ext) == (1, 2)
    assert "linked 1 of 3 changed files; 2 matched no code-file record" in caplog.text


async def test_modifies_is_not_a_link_type_the_processor_rewrites_on_every_pr_save() -> None:
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    assert MODIFIES not in DataSourceEntitiesProcessor.LINK_RELATION_TYPES
