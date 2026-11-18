"""A sync a connector starts itself (a webhook) goes through the sync start path, which writes the start time and status and
takes the lock a running reindex checks before it writes (see test_reindex_sync_ordering.py). No connector runs one directly."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.connector import connector_service
from app.connectors.core.constants import ConnectorStateKeys
from app.connectors.core.sync import sync_dispatcher
from app.connectors.core.sync.sync_dispatcher import SubmitResult
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.connectors.sources.box.connector import BoxConnector
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
)
from app.connectors.sources.salesforce.connector import SalesforceConnector

SOURCES = Path(connector_service.__file__).resolve().parents[3] / "sources"
_SYNC_RUNS = {"run_sync", "run_incremental_sync", "_run_incremental_sync"}


def test_no_connector_starts_a_sync_outside_the_sync_start_path() -> None:
    """A connector's sync method may call another; anything else asks with ``request_sync``."""
    offenders = [
        f"{path.relative_to(SOURCES)}:{call.lineno} {fn.name}"
        for path in sorted(SOURCES.rglob("*.py")) if path.name != "example.py"
        for fn in ast.walk(ast.parse(path.read_text()))
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and "sync" not in fn.name
        for call in ast.walk(fn)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in _SYNC_RUNS
    ]
    assert offenders == []


def _connector(cls: type, dispatcher: object) -> object:
    connector = object.__new__(cls)
    connector.logger = MagicMock()
    connector.connector_id = "c1"
    connector.connector_name = Connectors.DROPBOX
    connector.data_entities_processor = SimpleNamespace(org_id="o1")
    connector.data_store_provider = SimpleNamespace(graph_provider=AsyncMock())
    for name in _SYNC_RUNS:
        setattr(connector, name, AsyncMock(side_effect=AssertionError(f"{name} called outside the sync start path")))
    return connector


@pytest.mark.parametrize("cls", [DropboxConnector, DropboxIndividualConnector, BookStackConnector, SalesforceConnector, BoxConnector])
async def test_a_webhook_asks_the_dispatcher_for_a_sync(cls: type, monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = SimpleNamespace(submit=AsyncMock(return_value=SubmitResult.ACCEPTED))
    monkeypatch.setattr(sync_dispatcher._Registry, "dispatcher", dispatcher)
    connector = _connector(cls, dispatcher)
    connector.handle_webhook_notification({})
    await asyncio.gather(*connector_service._SYNC_REQUESTS)
    (spec,), _ = dispatcher.submit.call_args
    assert (spec.connector_id, spec.connector_name, spec.org_id, spec.full_sync) == ("c1", Connectors.DROPBOX.value, "o1", False)


async def test_a_request_declined_while_a_sync_runs_is_kept_for_after_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_dispatcher._Registry, "dispatcher", SimpleNamespace(submit=AsyncMock(return_value=SubmitResult.DECLINED_RUNNING)))
    connector = _connector(DropboxConnector, None)
    await connector.request_sync()
    connector.data_store_provider.graph_provider.update_node.assert_awaited_once_with(
        "c1", CollectionNames.APPS.value, {ConnectorStateKeys.PENDING_RESYNC: True},
    )
