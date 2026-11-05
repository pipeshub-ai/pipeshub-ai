"""``lookup_record`` answers a placeholder record as it answers one the user cannot read: no readable match.

One test per way an identifier is resolved (record id, address, issue key), over the real providers with their
database clients faked as in ``test_lookup_exact_issue_key``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.unit.agents.actions.knowledge_graph.test_lookup_exact_issue_key import (
    PP_8,
    SITE,
    MakeProvider,
    _arango,
    _neo4j,
    _resolver,
    record_passthrough,  # noqa: F401  (autouse fixture)
)

PROVIDERS = [_neo4j, _arango]
RECORD_ID = "0b6f1a52-3f0c-4a6e-9a55-6a2f5c0e7d11"


def _node_access(placeholder: bool) -> AsyncMock:
    async def access(node_id: str, **_: object) -> dict[str, Any]:
        return {"id": node_id, "nodeType": "record", "isPlaceholder": placeholder}

    return AsyncMock(side_effect=access)


def _identifier_and_rows(path: str) -> tuple[str, list[dict[str, Any]]]:
    if path == "record id":
        return RECORD_ID, [{**PP_8, "_key": RECORD_ID}]
    return ("PP-8" if path == "issue key" else f"{SITE}/browse/PP-8"), [PP_8]


def _provider(make_provider: MakeProvider, path: str, *, placeholder: bool):  # noqa: ANN202
    identifier, rows = _identifier_and_rows(path)
    provider = make_provider(rows)
    provider.get_knowledge_hub_node_access = _node_access(placeholder)
    if path == "record id":
        provider.get_record_by_id = AsyncMock(return_value=rows[0])
    return identifier, provider


@pytest.mark.parametrize("make_provider", PROVIDERS, ids=["neo4j", "arango"])
@pytest.mark.parametrize("path", ["record id", "address", "issue key"])
async def test_a_placeholder_is_no_readable_match(make_provider: MakeProvider, path: str) -> None:
    identifier, provider = _provider(make_provider, path, placeholder=True)

    result = await _resolver(provider).resolve_many([identifier])

    assert result.matches == []
    assert result.not_found_identifiers == [identifier]


@pytest.mark.parametrize("make_provider", PROVIDERS, ids=["neo4j", "arango"])
@pytest.mark.parametrize("path", ["record id", "address", "issue key"])
async def test_a_real_record_is_still_a_match(make_provider: MakeProvider, path: str) -> None:
    identifier, provider = _provider(make_provider, path, placeholder=False)

    result = await _resolver(provider).resolve_many([identifier])

    assert [m.name for m in result.matches] == ["PP-8 summary"]
