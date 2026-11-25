"""WP-100 (C20.18 / G-17): an agent's environment for its engine runs. The agents API stores it as ``environment``
(the run spec's ``x.pipeshub.environment`` shape, every rule the worker applies checked at save), both graph providers
hold it as text, a rollback restores it, and the run definition carries it to Node, which pins it on engine runs.

- WP100-ENV-8: a create stores it as canonical text; an agent without one is stored as before.
- WP100-ENV-9: a definition the worker would refuse is a 400 naming why, and nothing is written.
- WP100-ENV-10: an update that omits it keeps it, an empty one clears it; a rollback restores the version's.
- WP100-ENV-11: the run definition carries it; a stored one that no longer reads is reported for Node to refuse.
- WP100-ENV-12: both graph backends write it as text and null to clear.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import jsonschema
import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (edition seams load before any router)
from app.agents.agent_environment import (
    parse_agent_environment,
    stored_agent_environment,
)
from app.schema.arango.documents import agent_schema
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.support.bound_org import bind_test_org
from tests.unit.api.routes.test_wp51_agent_gates import (
    _config,
    _create,
    _definition,
    _graph,
    _update,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """``authMiddleware`` binds the caller's org; these tests call the route functions directly, as that caller."""
    yield from bind_test_org("org-1")


ENVIRONMENT = {
    "environmentId": "python-data",
    "level": "agent",
    "setup": "pip install -r requirements.txt",
    "env": {"LANG": "C.UTF-8"},
    "cachePaths": ["/root/.cache/pip"],
}
STORED = json.dumps(ENVIRONMENT, sort_keys=True, separators=(",", ":"))
REPOSITORY = {"environmentId": "widgets", "level": "repository", "devcontainer": '{"postCreateCommand": "make"}'}

BAD = [
    pytest.param({**ENVIRONMENT, "env": {"GITHUB_TOKEN": "x"}}, "ENVIRONMENT_VARIABLE_SECRET", id="secret"),
    pytest.param({**ENVIRONMENT, "env": {"PATH": "/x"}}, "ENVIRONMENT_VARIABLE_RESERVED", id="reserved"),
    pytest.param({**ENVIRONMENT, "image": "python:3.12"}, "ENVIRONMENT_INVALID", id="a_base_image"),
    pytest.param({**ENVIRONMENT, "level": "image"}, "ENVIRONMENT_INVALID", id="unknown_level"),
    pytest.param({**REPOSITORY, "setup": "make"}, "ENVIRONMENT_INVALID", id="repository_with_a_script"),
    pytest.param({**REPOSITORY, "devcontainer": '{"containerEnv": {"H": "${localEnv:HOME}"}}'}, "DEVCONTAINER_INVALID",
                 id="devcontainer_substitution"),
    pytest.param(["setup"], "ENVIRONMENT_INVALID", id="not_an_object"),
]


class TestSave:
    async def test_wp100_env_8_create_stores_it_as_canonical_text(self) -> None:
        graph = _graph()
        answer = await _create({"environment": ENVIRONMENT}, graph)
        document = graph.batch_upsert_nodes.await_args.args[0][0]
        assert document["environment"] == STORED
        jsonschema.validate({k: v for k, v in document.items() if k != "_key"}, agent_schema["rule"])
        assert answer["agent"]["environment"] == ENVIRONMENT

    async def test_wp100_env_8_a_repository_environment_is_stored_too(self) -> None:
        graph = _graph()
        _ = await _create({"environment": REPOSITORY}, graph)
        assert json.loads(graph.batch_upsert_nodes.await_args.args[0][0]["environment"]) == REPOSITORY

    @pytest.mark.parametrize("body", [{}, {"environment": {}}, {"environment": None}], ids=["absent", "empty", "null"])
    async def test_wp100_env_8_an_agent_without_one_is_stored_as_before(self, body: dict[str, Any]) -> None:
        plain, graph = _graph(), _graph()
        _ = await _create({}, plain)
        answer = await _create(body, graph)

        def written(g: AsyncMock) -> dict[str, Any]:
            document = dict(g.batch_upsert_nodes.await_args.args[0][0])
            return {k: v for k, v in document.items() if k not in ("_key", "createdAtTimestamp", "updatedAtTimestamp")}

        assert written(graph) == written(plain)
        assert "environment" not in answer["agent"]

    @pytest.mark.parametrize(("environment", "code"), BAD)
    async def test_wp100_env_9_a_refused_save_is_400_with_the_reason(self, environment: object, code: str) -> None:
        created, updated = _graph(), _graph({"environment": ENVIRONMENT})
        with pytest.raises(HTTPException) as refused_create:
            _ = await _create({"environment": environment}, created)
        with pytest.raises(HTTPException) as refused_update:
            _ = await _update({"environment": environment}, updated)
        for refused in (refused_create, refused_update):
            assert refused.value.status_code == 400
            assert f"[{code}]" in str(refused.value.detail)
        created.batch_upsert_nodes.assert_not_awaited()
        updated.update_agent.assert_not_awaited()

    async def test_wp100_env_10_an_update_keeps_or_clears_it(self) -> None:
        assert "environment" not in await _update({"name": "B"}, _graph({"environment": ENVIRONMENT}))
        assert (await _update({"environment": {}}, _graph({"environment": ENVIRONMENT})))["environment"] is None
        assert (await _update({"environment": ENVIRONMENT}, _graph()))["environment"] == ENVIRONMENT

    async def test_wp100_env_10_a_rollback_restores_the_versions_environment(self) -> None:
        from app.api.routes.agent import (
            _draft_body,  # pyright: ignore[reportPrivateUsage]
        )

        services = {"config_service": _config(), "logger": MagicMock()}
        restored = await _draft_body(services, {"name": "A", "environment": ENVIRONMENT}, {"name": "A"})
        cleared = await _draft_body(services, {"name": "A"}, {"name": "A", "environment": ENVIRONMENT})
        assert restored["environment"] == ENVIRONMENT
        assert cleared["environment"] == {}


class TestRunDefinition:
    def test_wp100_env_11_the_run_definition_carries_it(self) -> None:
        assert _definition({"environment": ENVIRONMENT})["environment"] == ENVIRONMENT
        assert "environment" not in _definition({})
        assert _definition({"environment": {}}) == _definition({})

    @pytest.mark.parametrize("stored", [["setup"], {"environmentId": "x", "level": "agent", "env": {"API_KEY": "1"}}])
    def test_wp100_env_11_a_stored_one_that_no_longer_reads_is_reported_invalid(self, stored: object) -> None:
        assert _definition({"environment": stored})["environment"] == {"invalid": True}
        assert stored_agent_environment(stored) == {"invalid": True}

    def test_wp100_env_11_the_stored_shape_is_the_pins(self) -> None:
        assert parse_agent_environment(ENVIRONMENT) == ENVIRONMENT
        assert parse_agent_environment(None) == {}


class TestBothGraphBackends:
    async def test_wp100_env_12_both_providers_write_it_as_text_and_null_to_clear(self) -> None:
        neo4j = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        neo4j.client = AsyncMock()
        neo4j.check_agent_permission = AsyncMock(return_value={"can_edit": True})  # type: ignore[method-assign]
        arango = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
        arango.http_client = AsyncMock()
        arango.check_agent_permission = AsyncMock(return_value={"can_edit": True})  # type: ignore[method-assign]

        async def neo(updates: dict[str, Any]) -> dict[str, Any]:
            neo4j.client.execute_query = AsyncMock(return_value=[{"n": {"id": "a1"}}])
            assert await neo4j.update_agent("a1", updates, "u1", "o1") is True
            return neo4j.client.execute_query.await_args.kwargs["parameters"]["updates"]

        async def aran(updates: dict[str, Any]) -> dict[str, Any]:
            arango.http_client.update_document = AsyncMock(return_value={"_key": "a1"})
            assert await arango.update_agent("a1", updates, "u1", "o1") is True
            return arango.http_client.update_document.await_args.args[2]

        for written in (await neo({"environment": ENVIRONMENT}), await aran({"environment": ENVIRONMENT})):
            assert written["environment"] == STORED
        for written in (await neo({"environment": None}), await aran({"environment": None})):
            assert written["environment"] is None
