"""Setting and clearing a connector's access limit (``AccessLimit``) through the processor.

One write puts ``accessLimit`` and ``permissionModel`` on the app document, and three things make it seen: the connector's
structure generation moves in the same transaction, the knowledge-hub scope stamps are marked changed, and the search
cache's access lists are dropped once the write has ended. A write that fails raises.

The processor runs over the real ``GraphDataStore`` here, so the store's own classification of the write is under test too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AccessLimit, CollectionNames, PermissionModel
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import (
    GraphDataStore,
    GraphWriteError,
)
from app.services.permissions.structure_generation import (
    GENERATION_KEY,
    ScopeGenerationError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

APPS = CollectionNames.APPS.value
CONNECTOR = "conn-1"
ORG = "org-1"
_NOTIFY = (
    "app.connectors.core.base.data_processor.data_source_entities_processor."
    "notify_connector_permissions_changed"
)


@pytest.fixture(autouse=True)
def _scope_fields_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The structure generation is kept only while the scope fields are on."""
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


@pytest.fixture
def order() -> list:
    return []


@pytest.fixture(autouse=True)
def _notify(order: list) -> Iterator[None]:
    async def drop(connector_id: str, org_id: str | None = None) -> None:
        order.append(("drop", connector_id, org_id))

    with patch(_NOTIFY, new=AsyncMock(side_effect=drop)):
        yield


def _provider(order: list) -> MagicMock:
    """A graph provider that notes each call the limit makes, in the order it arrives."""
    provider = MagicMock()
    provider.logger = MagicMock()
    provider.begin_transaction = AsyncMock(return_value="t")
    provider.opens_explicit_transaction = MagicMock(return_value=True)
    provider.is_write_conflict = MagicMock(return_value=False)
    provider.is_transient_error = MagicMock(return_value=False)

    def note(name: str, result: object = None) -> AsyncMock:
        async def call(*args: object, **kwargs: object) -> object:
            order.append((name, *args, *(v for _, v in sorted(kwargs.items()))))
            return result

        return AsyncMock(side_effect=call)

    provider.update_node = note("write", True)
    provider.increment_node_field = note("bump", 7)
    provider.commit_transaction = note("commit")
    provider.rollback_transaction = note("rollback")
    provider.kh_scope_mark_changed = note("mark")
    return provider


def _processor(provider: MagicMock) -> DataSourceEntitiesProcessor:
    processor = DataSourceEntitiesProcessor(MagicMock(), GraphDataStore(MagicMock(), provider), AsyncMock())
    processor.org_id = ORG
    return processor


def _refreshed_after(order: list, ended: str) -> None:
    """The stamps are marked and the cache dropped only once the transaction has ended."""
    names = [step[0] for step in order]
    assert names.index(ended) < names.index("mark") < names.index("drop")
    assert ("mark", CONNECTOR) in order
    assert ("drop", CONNECTOR, ORG) in order


@pytest.mark.parametrize("limit", list(AccessLimit))
async def test_setting_a_limit_writes_it_with_app_level_in_one_write(order: list, limit: AccessLimit) -> None:
    provider = _provider(order)

    await _processor(provider).set_access_limit(CONNECTOR, limit)

    provider.update_node.assert_awaited_once_with(
        CONNECTOR, APPS, {"accessLimit": limit.value, "permissionModel": "APP_LEVEL"}, transaction="t",
    )
    provider.begin_transaction.assert_awaited_once()
    assert provider.begin_transaction.await_args.kwargs["explicit"] is True
    # the write, then the generation in the same transaction, then the commit, then what runs after it
    assert [step[0] for step in order] == ["write", "bump", "commit", "mark", "drop"]
    assert ("bump", CONNECTOR, APPS, GENERATION_KEY, "t") in order
    _refreshed_after(order, "commit")


@pytest.mark.parametrize("model", [PermissionModel.RECORD_LEVEL, PermissionModel.APP_LEVEL])
async def test_clearing_removes_the_limit_and_puts_the_types_model_back(order: list, model: PermissionModel) -> None:
    provider = _provider(order)

    await _processor(provider).clear_access_limit(CONNECTOR, model)

    provider.update_node.assert_awaited_once_with(
        CONNECTOR, APPS, {"accessLimit": None, "permissionModel": model.value}, transaction="t",
    )
    assert [step[0] for step in order] == ["write", "bump", "commit", "mark", "drop"]
    _refreshed_after(order, "commit")


async def test_what_the_helpers_write_passes_the_app_schema() -> None:
    """Neo4j and PostgreSQL check every app write against the schema; ArangoDB's collection enforces it."""
    from app.schema.node_validator import NodeSchemaValidator

    seen: list[dict] = []
    for act in (
        lambda p: p.set_access_limit(CONNECTOR, AccessLimit.CREATOR),
        lambda p: p.clear_access_limit(CONNECTOR, PermissionModel.RECORD_LEVEL),
    ):
        provider = _provider([])
        await act(_processor(provider))
        seen.append(provider.update_node.await_args.args[2])
    for fields in seen:
        NodeSchemaValidator().validate_node_update(APPS, fields)


@pytest.mark.parametrize("act", [
    lambda p: p.set_access_limit(CONNECTOR, AccessLimit.CREATOR),
    lambda p: p.clear_access_limit(CONNECTOR, PermissionModel.RECORD_LEVEL),
], ids=["set", "clear"])
async def test_a_write_the_store_did_not_take_raises_and_rolls_back(order: list, act) -> None:
    """A store may answer False instead of raising (no such app document): that write did not land either."""
    provider = _provider(order)
    provider.update_node = AsyncMock(return_value=False)

    with pytest.raises(GraphWriteError):
        await act(_processor(provider))

    provider.commit_transaction.assert_not_awaited()
    provider.increment_node_field.assert_not_awaited()
    # the commit of a failed block may still have landed, so both refreshes run anyway
    _refreshed_after(order, "rollback")


async def test_a_write_that_raises_reaches_the_caller(order: list) -> None:
    provider = _provider(order)
    provider.update_node = AsyncMock(side_effect=RuntimeError("the store is down"))

    with pytest.raises(RuntimeError, match="the store is down"):
        await _processor(provider).set_access_limit(CONNECTOR, AccessLimit.CREATOR)

    provider.commit_transaction.assert_not_awaited()
    _refreshed_after(order, "rollback")


async def test_a_generation_that_cannot_move_fails_the_whole_write(order: list) -> None:
    """The bump is in the transaction of the write: without it a search stamp made before the limit would read as current."""
    provider = _provider(order)
    provider.increment_node_field = AsyncMock(return_value=None)  # no such connector

    with pytest.raises(ScopeGenerationError):
        await _processor(provider).set_access_limit(CONNECTOR, AccessLimit.CREATOR)

    provider.commit_transaction.assert_not_awaited()
    provider.rollback_transaction.assert_awaited()


async def test_stamps_that_cannot_be_marked_raise_and_the_cache_is_still_dropped(order: list) -> None:
    provider = _provider(order)
    provider.kh_scope_mark_changed = AsyncMock(side_effect=RuntimeError("no mark"))

    with pytest.raises(RuntimeError, match="no mark"):
        await _processor(provider).set_access_limit(CONNECTOR, AccessLimit.ORG)

    assert order[-1] == ("drop", CONNECTOR, ORG)


async def test_with_the_scope_fields_off_there_is_no_generation_to_move(order: list, monkeypatch: pytest.MonkeyPatch) -> None:
    """Like every structural write: no stamp exists while the scope fields are off, so nothing is bumped. The write and the
    other two refreshes are the same."""
    monkeypatch.delenv("ACL_SCOPE_FIELDS")
    monkeypatch.delenv("ACL_FILTER_MODE", raising=False)
    provider = _provider(order)

    await _processor(provider).set_access_limit(CONNECTOR, AccessLimit.CREATOR)

    assert [step[0] for step in order] == ["write", "commit", "mark", "drop"]
