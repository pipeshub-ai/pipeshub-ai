"""N1: at start-up the connectors tell the Node licence check whether this install uses what a licence key pays for.

Affected: a company-wide connector that copies access from its source, or more people than the personal connector cap
with their own connector to one source (per org, inactive ones included, as the cap counts them).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_processor.permission_sync_gate import (
    INSTALL_AFFECTED_KEY,
    PERSONAL_CONNECTOR_CAP,
    install_affected,
)
from tests.unit.connectors.core.test_connector_registry import (
    _make_graph_provider,
    _make_registry,
)


def _personal(owner: str, type_: str = "JIRA", **extra: object) -> dict:
    return {"type": type_, "scope": "personal", "createdBy": owner, **extra}


def _people(n: int, type_: str = "JIRA") -> list[dict]:
    return [_personal(f"u{i}", type_) for i in range(n)]


@pytest.mark.parametrize(("orgs", "expected"), [
    ([[]], {"affected": False, "teamConnectors": 0, "personalSourcesOverCap": []}),
    ([[{"type": "JIRA", "scope": "team"}]], {"affected": True, "teamConnectors": 1, "personalSourcesOverCap": []}),
    # Storage and database connectors copy no access, and collections are not a source.
    ([[{"type": "S3", "scope": "team"}, {"type": "KB", "scope": "team"}]],
     {"affected": False, "teamConnectors": 0, "personalSourcesOverCap": []}),
    ([_people(PERSONAL_CONNECTOR_CAP)], {"affected": False, "teamConnectors": 0, "personalSourcesOverCap": []}),
    ([_people(PERSONAL_CONNECTOR_CAP + 1)],
     {"affected": True, "teamConnectors": 0, "personalSourcesOverCap": ["JIRA"]}),
    # Inactive connectors count; one person with two connectors counts once.
    ([[*_people(PERSONAL_CONNECTOR_CAP), _personal("u-late", isActive=False), _personal("u0")]],
     {"affected": True, "teamConnectors": 0, "personalSourcesOverCap": ["JIRA"]}),
    # Local folders bring in only the owner's own files: no cap.
    ([_people(PERSONAL_CONNECTOR_CAP + 1, "LOCAL FS")],
     {"affected": False, "teamConnectors": 0, "personalSourcesOverCap": []}),
    # The cap is per org: two orgs under it are not over it together.
    ([_people(PERSONAL_CONNECTOR_CAP), _people(PERSONAL_CONNECTOR_CAP)],
     {"affected": False, "teamConnectors": 0, "personalSourcesOverCap": []}),
])
def test_install_affected(orgs: list[list[dict]], expected: dict) -> None:
    assert install_affected(orgs) == expected


def _registry_with(apps: list[dict] | Exception) -> tuple:
    registry, container = _make_registry()
    gp = _make_graph_provider()
    gp.get_all_documents.return_value = []
    if isinstance(apps, Exception):
        gp.get_org_apps.side_effect = apps
    else:
        gp.get_org_apps.return_value = apps
    data_store = MagicMock()
    data_store.graph_provider = gp
    container.data_store = AsyncMock(return_value=data_store)
    container.config_service.return_value.set_config = AsyncMock(return_value=True)
    # A report from an earlier start is stored: a start that cannot write a new one must remove it.
    container.config_service.return_value.delete_config = AsyncMock(return_value=True)
    return registry, gp, container.config_service.return_value.set_config


def _forgotten(registry: object) -> bool:
    delete = registry.container.config_service.return_value.delete_config  # type: ignore[attr-defined]
    return delete.await_args is not None and delete.await_args.args == (INSTALL_AFFECTED_KEY,)


@pytest.mark.asyncio
async def test_the_startup_sync_reports_it() -> None:
    registry, gp, set_config = _registry_with([{"_key": "c1", "type": "Jira", "scope": "team", "isActive": True}])
    assert await registry.sync_with_database() is True
    key, facts = set_config.await_args.args
    assert key == INSTALL_AFFECTED_KEY
    assert facts["affected"] is True and facts["teamConnectors"] == 1
    assert isinstance(facts["checkedAt"], str)
    # Arango and Neo4j answer a failed read with no rows unless asked to raise: both reads ask.
    assert gp.get_all_orgs.await_args.kwargs.get("raise_on_error") is True
    assert gp.get_org_apps.await_args.kwargs == {"active_only": False, "raise_on_error": True}


@pytest.mark.asyncio
async def test_nothing_is_reported_when_an_orgs_connectors_could_not_be_read() -> None:
    """A partial answer could call an affected install unaffected; with none, Node gives the grace."""
    registry, _, set_config = _registry_with(RuntimeError("store down"))
    await registry.sync_with_database()
    set_config.assert_not_awaited()
    assert _forgotten(registry), "a stored affected: false must not outlive a start that could not count"


@pytest.mark.asyncio
async def test_nothing_is_reported_when_the_org_list_could_not_be_read() -> None:
    """As Arango and Neo4j do: a failed org list is no orgs, unless the read asks to raise."""
    registry, gp, set_config = _registry_with([])

    async def orgs(**kwargs: object) -> list[dict]:
        if kwargs.get("raise_on_error"):
            raise RuntimeError("store down")
        return []

    gp.get_all_orgs.side_effect = orgs
    assert await registry.sync_with_database() is True
    set_config.assert_not_awaited()
    assert _forgotten(registry)


@pytest.mark.asyncio
async def test_a_failed_report_does_not_fail_the_sync() -> None:
    registry, _, set_config = _registry_with([])
    set_config.side_effect = RuntimeError("store down")
    assert await registry.sync_with_database() is True


@pytest.mark.asyncio
async def test_a_report_the_store_refused_removes_the_last_one() -> None:
    """set_config logs a store error and answers False rather than raising."""
    registry, _, set_config = _registry_with([])
    set_config.return_value = False
    assert await registry.sync_with_database() is True
    assert _forgotten(registry)


@pytest.mark.asyncio
async def test_a_report_that_raised_removes_the_last_one() -> None:
    registry, _, set_config = _registry_with([])
    set_config.side_effect = RuntimeError("store down")
    assert await registry.sync_with_database() is True
    assert _forgotten(registry)


@pytest.mark.asyncio
async def test_a_registry_sync_that_failed_removes_the_last_report() -> None:
    """The service still starts after a failed registry sync; the report it never wrote must not stay behind."""
    registry, gp, set_config = _registry_with([])
    gp.get_all_orgs.side_effect = RuntimeError("store down")
    assert await registry.sync_with_database() is False
    set_config.assert_not_awaited()
    assert _forgotten(registry)


@pytest.mark.asyncio
async def test_a_written_report_is_not_removed() -> None:
    registry, _, set_config = _registry_with([])
    assert await registry.sync_with_database() is True
    set_config.assert_awaited_once()
    assert not _forgotten(registry)
