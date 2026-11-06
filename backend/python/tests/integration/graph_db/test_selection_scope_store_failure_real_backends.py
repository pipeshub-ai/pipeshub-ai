"""A connected app's limit on Neo4j and ArangoDB: a failed store read is an error, not "outside the limit", and a user
with grants on records only is inside a limit that names the connector.

Runs where the other real-backend tests run (NEO4J_IT_URI and ARANGO_IT_URL; local defaults in tests/integration/real_graph.py).
"""
from __future__ import annotations

import uuid

import pytest

from app.agents.actions.code_graph.repos import list_code_repos
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.config.constants.arangodb import CollectionNames
from app.modules.retrieval import selection_scope as scope
from tests.integration.graph_db.test_code_graph_real_backends import (  # noqa: F401
    _grant,
    _Repo,
    repo,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def down(*_: object, **__: object) -> None:
    raise ConnectionError("store down")


def fail_reads(monkeypatch: pytest.MonkeyPatch, repo: _Repo) -> None:  # noqa: F811
    if repo.backend == "neo4j":
        monkeypatch.setattr(repo.graph.client, "execute_query", down)
    else:
        monkeypatch.setattr(repo.graph.http_client, "execute_aql", down)


async def test_a_failed_store_read_raises_and_the_same_call_answers_once_the_store_is_back(
    repo: _Repo, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    await _grant(repo, f"u-{user_id}", user_id)
    file_id = next(iter(repo.record_ids.values()))
    limit = {"apps": [repo.connector_id]}
    with monkeypatch.context() as down_store:
        fail_reads(down_store, repo)
        with pytest.raises(PermissionVerificationUnavailableError):
            await scope.outside_connection(repo.graph, user_id, repo.org_id, [file_id], limit)
        with pytest.raises(PermissionVerificationUnavailableError):
            await scope.groups_outside_connection(repo.graph, user_id, repo.org_id, {repo.group_id: repo.connector_id}, limit)
    scope._memo.clear()
    # Back up: a file in the named connector is inside, one in another connector is outside, no limit asks nothing.
    assert await scope.outside_connection(repo.graph, user_id, repo.org_id, [file_id], limit) == set()
    assert await scope.outside_connection(repo.graph, user_id, repo.org_id, [file_id], {"apps": ["conn-other"]}) == {file_id}
    assert await scope.outside_connection(repo.graph, user_id, repo.org_id, [file_id], None) == set()


async def test_a_user_with_record_grants_only_is_inside_a_limit_that_names_the_connector(repo: _Repo) -> None:  # noqa: F811
    user_id = f"user-{uuid.uuid4().hex[:8]}"
    await _grant(repo, f"u-{user_id}", user_id)  # grants on the files, none on the record group
    group = {repo.group_id: repo.connector_id}
    assert await scope.groups_outside_connection(repo.graph, user_id, repo.org_id, group, {"apps": [repo.connector_id]}) == set()
    assert await scope.groups_outside_connection(repo.graph, user_id, repo.org_id, group, {"apps": ["conn-other"]}) == {repo.group_id}
    assert await scope.groups_outside_connection(repo.graph, user_id, repo.org_id, group, {"records": [next(iter(repo.record_ids.values()))]}) == {repo.group_id}


async def test_a_repo_with_no_readable_file_stays_hidden_under_a_connector_limit(repo: _Repo) -> None:  # noqa: F811
    """The limit judges a group by where it sits, so the repo list is what keeps out a repo the user cannot read."""
    reader, stranger = (f"user-{uuid.uuid4().hex[:8]}" for _ in range(2))
    await _grant(repo, f"u-{reader}", reader)
    await repo.graph.batch_upsert_nodes(
        [{"id": f"u-{stranger}", "userId": stranger, "orgId": repo.org_id, "email": f"{stranger}@example.com", "isActive": True}],
        collection=CollectionNames.USERS.value,
    )
    await repo.graph.batch_upsert_nodes(
        [{"id": repo.group_id, "orgId": repo.org_id, "connectorId": repo.connector_id, "groupName": "acme/shop", "connectorName": "GITHUB",
          "groupType": "REPOSITORY", "externalGroupId": "1-code-repository", "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
        collection=CollectionNames.RECORD_GROUPS.value,
    )
    await repo.graph.batch_upsert_nodes(
        [{"id": repo.org_id, "accountType": "enterprise", "name": "Acme", "isActive": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
        collection=CollectionNames.ORGS.value,
    )
    def edge(f: str, fc: str, t: str, tc: str) -> dict:
        return {"from_id": f, "from_collection": fc, "to_id": t, "to_collection": tc, "createdAtTimestamp": 1}

    await repo.graph.batch_create_edges(
        [edge(repo.org_id, CollectionNames.ORGS.value, repo.connector_id, CollectionNames.APPS.value)],
        collection=CollectionNames.ORG_APP_RELATION.value,
    )
    await repo.graph.batch_create_edges(
        [edge(rid, CollectionNames.RECORDS.value, repo.group_id, CollectionNames.RECORD_GROUPS.value) for rid in repo.record_ids.values()],
        collection=CollectionNames.BELONGS_TO.value,
    )
    limit = {"apps": [repo.connector_id]}

    async def inside_limit(user_id: str) -> set[str]:
        groups = {r.group_id: r.connector_id for r in await list_code_repos(repo.graph, repo.org_id, user_id)}
        return set(groups) - await scope.groups_outside_connection(repo.graph, user_id, repo.org_id, groups, limit)

    assert await inside_limit(reader) == {repo.group_id}
    assert await inside_limit(stranger) == set()
