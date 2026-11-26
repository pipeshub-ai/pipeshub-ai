"""WP-51 (C11.07): one org's agent names by key, for Node's usage report and budget notices; ADR-UI-03: with each
named agent's latest published version, for an admin list that pins versions."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.middlewares.auth import require_service_token
from app.api.routes.agent import AgentNamesRequest, get_agent_names_internal
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import TokenScopes
from app.harness.agent_names import MAX_AGENT_KEYS
from tests.support.bound_org import authed

AGENTS = CollectionNames.AGENT_INSTANCES.value
USERS = CollectionNames.USERS.value
VERSIONS = CollectionNames.AGENT_VERSIONS.value


class _Graph:
    def __init__(
        self,
        documents: dict[tuple[str, str], dict[str, Any]],
        *,
        failing: bool = False,
        versions: list[dict[str, Any]] | None = None,
        versions_failing: bool = False,
    ) -> None:
        self.documents = documents
        self.failing = failing
        self.versions = versions or []
        self.versions_failing = versions_failing
        self.calls: list[tuple[str, str, bool]] = []
        self.version_reads: list[dict[str, Any]] = []
        self.writes = 0

    async def get_nodes_by_filters(
        self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None, **_: object
    ) -> list[dict[str, Any]]:
        assert collection == VERSIONS
        self.version_reads.append(filters)
        if self.versions_failing:
            raise ConnectionError("graph down")
        return [v for v in self.versions if all(v.get(k) == want for k, want in filters.items())]

    async def ensure_nodes(self, *_: object, **__: object) -> None:
        self.writes += 1

    async def get_document(
        self, document_key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False
    ) -> dict[str, Any] | None:
        self.calls.append((document_key, collection, raise_on_error))
        if self.failing:
            raise ConnectionError("graph down")
        return self.documents.get((collection, document_key))


def _graph() -> _Graph:
    return _Graph(
        {
            (AGENTS, "sales"): {"_key": "sales", "name": " Sales research ", "createdBy": "u-1"},
            (AGENTS, "old"): {"_key": "old", "name": "Old helper", "createdBy": "u-1", "isDeleted": True},
            (AGENTS, "theirs"): {"_key": "theirs", "name": "Not yours", "createdBy": "u-2"},
            (AGENTS, "nameless"): {"_key": "nameless", "name": "  ", "createdBy": "u-1"},
            (AGENTS, "orphan"): {"_key": "orphan", "name": "No creator"},
            (USERS, "u-1"): {"_key": "u-1", "orgId": "org-1"},
            (USERS, "u-2"): {"_key": "u-2", "orgId": "org-2"},
        }
    )


def _body(*keys: str, include: list[str] | None = None) -> AgentNamesRequest:
    return AgentNamesRequest.model_validate(
        {"agentKeys": list(keys), **({"include": include} if include is not None else {})}
    )


async def test_wp51_names_only_the_tokens_orgs_agents_deleted_ones_included() -> None:
    graph = _graph()
    answer = await authed(get_agent_names_internal)(
        _body("sales", "old", "theirs", "nameless", "orphan", "unknown", "sales"),
        graph,  # type: ignore[arg-type]
        {"orgId": "org-1"},
    )
    assert answer == {"names": {"sales": "Sales research", "old": "Old helper"}}
    assert all(raise_on_error for _key, _collection, raise_on_error in graph.calls)
    assert sum(1 for key, collection, _ in graph.calls if collection == USERS and key == "u-1") == 1


async def test_wp51_a_graph_failure_is_a_503_never_a_partial_answer() -> None:
    with pytest.raises(HTTPException) as failed:
        await authed(get_agent_names_internal)(_body("sales"), _Graph({}, failing=True), {"orgId": "org-1"})  # type: ignore[arg-type]
    assert failed.value.status_code == 503


async def test_wp51_a_token_without_an_org_is_refused() -> None:
    with pytest.raises(HTTPException) as refused:
        await authed(get_agent_names_internal)(_body("sales"), _graph(), {})  # type: ignore[arg-type]
    assert refused.value.status_code == 400


def _versioned() -> _Graph:
    graph = _graph()
    graph.versions = [
        {"orgId": "org-1", "agentKey": "sales", "version": 1},
        {"orgId": "org-1", "agentKey": "sales", "version": 3},
        {"orgId": "org-1", "agentKey": "sales", "version": 2},
        {"orgId": "org-2", "agentKey": "theirs", "version": 9},
    ]
    return graph


async def test_adr_ui_03_latest_versions_come_with_the_names_for_the_orgs_agents_only() -> None:
    graph = _versioned()
    answer = await authed(get_agent_names_internal)(
        _body("sales", "old", "theirs", "unknown", include=["latestVersion"]),
        graph,  # type: ignore[arg-type]
        {"orgId": "org-1"},
    )
    # "old" has no version yet: absent, and the list never publishes one (no write on an admin read).
    assert answer == {
        "names": {"sales": "Sales research", "old": "Old helper"},
        "latestVersions": {"sales": 3},
    }
    assert graph.writes == 0
    assert sorted(f["agentKey"] for f in graph.version_reads) == ["old", "sales"]
    assert all(f["orgId"] == "org-1" for f in graph.version_reads)


async def test_adr_ui_03_without_include_the_answer_is_the_names_alone() -> None:
    graph = _versioned()
    answer = await authed(get_agent_names_internal)(_body("sales"), graph, {"orgId": "org-1"})  # type: ignore[arg-type]
    assert answer == {"names": {"sales": "Sales research"}}
    assert graph.version_reads == []


async def test_adr_ui_03_a_version_read_failure_is_a_503_never_names_without_versions() -> None:
    graph = _versioned()
    graph.versions_failing = True
    with pytest.raises(HTTPException) as failed:
        await authed(get_agent_names_internal)(
            _body("sales", include=["latestVersion"]), graph, {"orgId": "org-1"}  # type: ignore[arg-type]
        )
    assert failed.value.status_code == 503


def test_adr_ui_03_an_unknown_include_is_refused() -> None:
    with pytest.raises(ValidationError):
        _body("sales", include=["owner"])


def test_wp51_a_longer_list_than_nodes_batch_is_refused() -> None:
    with pytest.raises(ValidationError):
        _body(*[f"a{i}" for i in range(MAX_AGENT_KEYS + 1)])


def _token_request(user: dict[str, Any] | None) -> MagicMock:
    request = MagicMock()
    request.state.user = user
    return request


@pytest.mark.parametrize(
    "user",
    [
        None,
        {"token_type": "regular", "orgId": "org-1", "userId": "u1"},
        {"token_type": "scoped", "orgId": "org-1", "scopes": ["agent:erase"]},
        {"token_type": "scoped", "orgId": "org-1", "scopes": ["storage:token"]},
    ],
)
async def test_wp51_only_a_service_token_scoped_agent_names_passes(user: dict[str, Any] | None) -> None:
    check = require_service_token(TokenScopes.AGENT_NAMES)
    with pytest.raises(HTTPException) as refused:
        await check(_token_request(user))
    assert refused.value.status_code == (401 if user is None else 403)
    granted = {"token_type": "scoped", "orgId": "org-1", "scopes": ["agent:names"]}
    assert await check(_token_request(granted)) == granted
