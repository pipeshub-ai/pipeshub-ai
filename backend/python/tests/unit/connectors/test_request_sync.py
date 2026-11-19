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
from app.core.org_context import current, for_org

SOURCES = Path(connector_service.__file__).resolve().parents[3] / "sources"
_SYNC_RUNS = {"run_sync", "run_incremental_sync", "_run_incremental_sync"}
# The sync methods themselves (Box's run_sync body is _run_sync): only they may call one another.
_SYNC_METHODS = {"run_sync", "run_incremental_sync", "_run_sync"}


def _direct_sync_calls(source: str) -> list[str]:
    return [
        f"{call.lineno} {fn.name}"
        for fn in ast.walk(ast.parse(source))
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name not in _SYNC_METHODS
        for call in ast.walk(fn)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in _SYNC_RUNS
    ]


def test_no_connector_starts_a_sync_outside_the_sync_start_path() -> None:
    """Anything but a connector's own sync methods asks with ``request_sync``."""
    offenders = [
        f"{path.relative_to(SOURCES)}:{site}"
        for path in sorted(SOURCES.rglob("*.py")) if path.name != "example.py"
        for site in _direct_sync_calls(path.read_text())
    ]
    assert offenders == []


def test_the_scan_finds_a_webhook_that_starts_a_sync_through_a_helper() -> None:
    planted = (
        "class C:\n"
        "    def handle_webhook_notification(self, n):\n        self._trigger_sync()\n"
        "    async def _trigger_sync(self):\n        await self.run_sync()\n"
        "    async def run_incremental_sync(self):\n        await self.run_sync()\n"
    )
    assert _direct_sync_calls(planted) == ["5 _trigger_sync"]


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
    dispatcher.submit.assert_awaited_once()
    (spec,), _ = dispatcher.submit.call_args
    assert (spec.connector_id, spec.connector_name, spec.org_id, spec.full_sync) == ("c1", Connectors.DROPBOX.value, "o1", False)


async def test_a_request_declined_while_a_sync_runs_is_kept_for_after_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_dispatcher._Registry, "dispatcher", SimpleNamespace(
        submit=AsyncMock(return_value=SubmitResult.DECLINED_RUNNING), is_running=AsyncMock(return_value=True),
    ))
    connector = _connector(DropboxConnector, None)
    await connector.request_sync()
    connector.data_store_provider.graph_provider.update_node.assert_awaited_once_with(
        "c1", CollectionNames.APPS.value, {ConnectorStateKeys.PENDING_RESYNC: True},
    )


async def test_a_declined_request_whose_sync_ended_before_the_flag_landed_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = SimpleNamespace(
        submit=AsyncMock(side_effect=[SubmitResult.DECLINED_RUNNING, SubmitResult.ACCEPTED]),
        is_running=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(sync_dispatcher._Registry, "dispatcher", dispatcher)
    await _connector(DropboxConnector, None).request_sync()
    assert dispatcher.submit.await_count == 2


async def test_a_request_runs_in_the_callers_org_context(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    async def submit(spec: object) -> SubmitResult:
        seen.append(current().org_id)
        return SubmitResult.ACCEPTED

    monkeypatch.setattr(sync_dispatcher._Registry, "dispatcher", SimpleNamespace(submit=submit))
    connector = _connector(DropboxConnector, None)
    with for_org("o1", job="test"):
        task = connector.request_sync()
    assert task in connector_service._SYNC_REQUESTS
    await task
    assert seen == ["o1"]


async def test_a_connector_whose_processor_has_no_org_asks_with_the_callers_org(monkeypatch: pytest.MonkeyPatch) -> None:
    dispatcher = SimpleNamespace(submit=AsyncMock(return_value=SubmitResult.ACCEPTED))
    monkeypatch.setattr(sync_dispatcher._Registry, "dispatcher", dispatcher)
    connector = _connector(DropboxConnector, None)
    connector.data_entities_processor.org_id = None
    with for_org("o9", job="test"):
        task = connector.request_sync()
    await task
    (spec,), _ = dispatcher.submit.call_args
    assert spec.org_id == "o9"


async def test_a_request_with_no_org_anywhere_is_kept_for_the_next_sync_not_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """The start handler refuses an event without an org, and the broker would deliver it again and again."""
    dispatcher = SimpleNamespace(submit=AsyncMock(return_value=SubmitResult.ACCEPTED))
    monkeypatch.setattr(sync_dispatcher._Registry, "dispatcher", dispatcher)
    connector = _connector(DropboxConnector, None)
    connector.data_entities_processor.org_id = None
    await connector.request_sync()  # no org context bound either
    dispatcher.submit.assert_not_awaited()
    connector.data_store_provider.graph_provider.update_node.assert_awaited_once_with(
        "c1", CollectionNames.APPS.value, {ConnectorStateKeys.PENDING_RESYNC: True},
    )
