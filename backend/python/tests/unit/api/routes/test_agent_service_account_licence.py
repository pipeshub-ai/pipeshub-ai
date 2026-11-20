"""Without a licence key, an agent that searches as its creator may be given only open sources."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes.agent import InvalidRequestError, _assert_service_account_knowledge

APPS = [
    {"_key": "drive", "name": "Drive", "scope": "team", "type": "DRIVE WORKSPACE", "createdBy": "admin"},
    {"_key": "s3", "name": "Bucket", "scope": "team", "type": "S3", "createdBy": "admin"},
    {"_key": "kb", "name": "KB", "scope": "team", "type": "KB", "createdBy": "admin"},
]


def _graph(ids: list[str]) -> MagicMock:
    graph = MagicMock()
    graph.get_org_apps = AsyncMock(return_value=[a for a in APPS if a["_key"] in ids])
    return graph


async def _check(ids: list[str], *, bound: bool, licensed: bool) -> None:
    await _assert_service_account_knowledge(
        ids, _graph(ids), "org", "admin", bound=bound, licensed=AsyncMock(return_value=licensed),
    )


async def test_unlicensed_unbound_agent_is_refused_a_gated_connector() -> None:
    with pytest.raises(InvalidRequestError, match="Without a licence key.*Drive"):
        await _check(["drive", "s3"], bound=False, licensed=False)


async def test_unlicensed_unbound_agent_may_use_open_connectors() -> None:
    await _check(["s3"], bound=False, licensed=False)


@pytest.mark.parametrize(("bound", "licensed"), [(True, False), (False, True), (True, True)])
async def test_a_bound_or_licensed_agent_keeps_gated_connectors(bound: bool, licensed: bool) -> None:
    await _check(["drive", "s3"], bound=bound, licensed=licensed)


async def test_unlicensed_bound_agent_keeps_collections() -> None:
    await _check(["kb", "drive"], bound=True, licensed=False)


async def test_the_licence_is_read_only_when_a_gated_source_is_attached() -> None:
    licensed = AsyncMock(return_value=False)
    await _assert_service_account_knowledge(["s3"], _graph(["s3"]), "org", "admin", bound=False, licensed=licensed)
    licensed.assert_not_awaited()
