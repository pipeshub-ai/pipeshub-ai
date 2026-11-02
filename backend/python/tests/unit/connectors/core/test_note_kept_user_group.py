"""DataSourceEntitiesProcessor.note_kept_user_group had no test over the real processor."""

from typing import Any, Never

import pytest

from app.config.constants.arangodb import CollectionNames

from .test_has_stored_permissions import FakeStore, processor_over

GROUPS = CollectionNames.GROUPS.value


class CopiesStore(FakeStore):
    """Group nodes by (connector, external id), as get_nodes_by_filters returns them."""

    def __init__(self, copies: list[dict[str, Any]]) -> None:
        super().__init__([])
        self.copies = copies

    async def get_nodes_by_filters(self, collection: str, filters: dict, *, raise_on_error: bool = False) -> list[dict]:
        assert raise_on_error, "a failed group lookup must raise"
        assert collection == GROUPS
        return [c for c in self.copies if c["externalGroupId"] == filters["externalGroupId"]]


async def test_a_kept_user_group_is_noted_as_seen_by_the_full_sync_in_progress() -> None:
    processor = processor_over(CopiesStore([{"_key": "n1", "externalGroupId": "ext-1"}]))
    await processor.note_kept_user_group("c1", "ext-1")  # no full sync running: nothing to note
    run = processor.begin_full_sync_run("c1")
    await processor.note_kept_user_group("c1", "ext-1")
    assert run.has(GROUPS, "n1")


async def test_every_stored_copy_of_a_kept_group_is_noted() -> None:
    copies = [{"_key": "n1", "externalGroupId": "ext-1"}, {"_key": "n2", "externalGroupId": "ext-1"}]
    processor = processor_over(CopiesStore(copies))
    run = processor.begin_full_sync_run("c1")
    await processor.note_kept_user_group("c1", "ext-1")
    assert run.has(GROUPS, "n1") and run.has(GROUPS, "n2")


async def test_an_unknown_kept_user_group_is_not_noted() -> None:
    processor = processor_over(CopiesStore([{"_key": "n1", "externalGroupId": "ext-1"}]))
    run = processor.begin_full_sync_run("c1")
    await processor.note_kept_user_group("c1", "missing")
    assert not run.has(GROUPS, "n1")


async def test_a_failed_group_lookup_raises() -> None:
    store = CopiesStore([])

    async def boom(*_: object, **__: object) -> Never:
        raise RuntimeError("lookup failed")

    store.get_nodes_by_filters = boom  # type: ignore[method-assign]
    processor = processor_over(store)
    processor.begin_full_sync_run("c1")
    with pytest.raises(RuntimeError):
        await processor.note_kept_user_group("c1", "ext-1")
