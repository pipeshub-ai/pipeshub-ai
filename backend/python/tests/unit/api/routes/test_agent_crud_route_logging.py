"""Agent CRUD routes hand their failures to ``http.request.end`` (docs/logging.md, "HTTP requests (Python)").

The real ``agent.router`` behind ``install_request_logging`` and the shared in-memory graph
(``tests/support/agent_routes.py``). A failure is one line: the request's, with the agent's id, its class and no
driver text. A step the route swallows and goes on without is one ``agent.route.failed`` line.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.api.middlewares.request_log import install_request_logging
from app.config.constants.service import config_node_constants
from app.schema.node_validator import SchemaValidationError
from app.services.featureflag.config.config import CONFIG
from app.utils import error_class
from app.utils.logger import log_record_fields
from tests.support.agent_routes import (
    AGENTS,
    FakeConfigService,
    InMemoryGraph,
    as_user,
    make_client,
)
from tests.support.config_service import RIG_PLATFORM_FLAGS
from tests.support.log_capture import assert_not_logged, failure_line

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

EVENT = "http.request.end"
ROUTE_FAILED = "agent.route.failed"
BASE = "/api/v1/agent"
# Built at run time: a traceback prints source lines, so a literal there would show in the stack.
SENTINEL = "-".join(("SENTINEL", "driver-text")) + " alice@" + "example.com"

_TOOLSET_INSTANCES = {
    "/services/toolset-instances": [{"_id": "inst-1", "orgId": "org-1", "instanceName": "Slack", "toolsetType": "slack"}],
}
_MCP_INSTANCES = {
    f"/services/mcp/instances/{instance_id}": {"_id": instance_id, "orgId": "org-1", "name": name, "typeId": name}
    for instance_id, name in (("i1", "github"), ("i2", "linear"))
}


class _ConfigOutage(FakeConfigService):
    """The model configs cannot be read."""

    async def get_config(self, key: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - a JSON value
        if key == config_node_constants.AI_MODELS.value:
            raise ConnectionError(SENTINEL)
        return await super().get_config(key, *args, **kwargs)


@pytest.fixture
def graph() -> InMemoryGraph:
    g = InMemoryGraph()
    g.add_agent("private", "alice", description="alice only")
    g.add_agent("sa", "alice", share_with_org=True, isServiceAccount=True)
    g.add_template("tpl", "alice")
    return g


def _client(graph: InMemoryGraph, config: FakeConfigService | None = None) -> TestClient:
    client, _ = make_client(graph, config or FakeConfigService({**_TOOLSET_INSTANCES, **_MCP_INSTANCES}))
    install_request_logging(client.app)  # outside the test auth, as in query_main
    return client


@pytest.fixture(autouse=True)
def _labs_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes' own lines, on an org whose harness is off (ADR-OPS-08): what a release without the harness logs."""
    monkeypatch.setitem(RIG_PLATFORM_FLAGS, CONFIG.ENABLE_AGENT_HARNESS, False)


@pytest.fixture(autouse=True)
def capture(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """What the service writes: the fake graph's own lines are not the provider's. GraphDataStore's rollback ERROR is
    shared connector code, out of this change (P3c PLAN Q1)."""
    return [
        r for r in caplog.records
        if r.name != "test.agent_routes.graph" and not r.name.startswith("httpx") and r.filename != "graph_data_store.py"
    ]


def _loud(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in _records(caplog) if r.levelno >= logging.WARNING]


def _end(caplog: pytest.LogCaptureFixture) -> tuple[logging.LogRecord, dict[str, object]]:
    (record,) = [r for r in caplog.records if r.getMessage() == EVENT]
    return record, log_record_fields(record)


# --- which agent --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "path", "body"), [
    ("GET", "/private", None),
    ("PUT", "/private", {"name": "renamed"}),
    ("DELETE", "/private", None),
    ("GET", "/sa/internal/service-account", None),
])
def test_the_request_line_names_the_agent(graph: InMemoryGraph, caplog, method: str, path: str, body: dict | None) -> None:
    response = _client(graph).request(method, BASE + path, headers=as_user("alice"), json=body)

    assert response.status_code == 200
    _, fields = _end(caplog)
    assert fields.get("agent_id") == path.split("/")[1], f"{method} {path}: the request line has no agent_id: {fields}"


def test_the_request_line_names_the_agent_it_created(graph: InMemoryGraph, caplog) -> None:
    response = _client(graph).post(f"{BASE}/create", headers=as_user("alice"), json={"name": "Helper"})

    assert response.status_code == 200
    _, fields = _end(caplog)
    assert fields.get("agent_id") == response.json()["agent"]["_key"], f"the create's line has no agent_id: {fields}"


@pytest.mark.parametrize(("method", "path", "body"), [
    ("DELETE", "/private", None),
    ("POST", "/create", {"name": "Helper"}),
])
def test_success_is_the_request_line_with_ids_in_fields(
    graph: InMemoryGraph, caplog, method: str, path: str, body: dict | None,
) -> None:
    response = _client(graph).request(method, BASE + path, headers=as_user("alice"), json=body)

    assert response.status_code == 200
    agent_id = "private" if method == "DELETE" else response.json()["agent"]["_key"]
    in_text = [r.getMessage() for r in _records(caplog) if agent_id in r.getMessage()]
    assert not in_text, f"ids belong in fields, not in the message: {in_text}"


# --- a failure is one line, classed, without the driver's text --------------------------------------------------

_OUTAGES = [
    ("GET", "/template/list", None, "get_all_agent_templates"),
    ("GET", "/template/tpl", None, "get_template"),
    ("POST", "/template/tpl/clone", None, "clone_agent_template"),
    ("DELETE", "/template/tpl", None, "delete_agent_template"),
    ("PUT", "/template/tpl", {"name": "T2"}, "update_agent_template"),
    ("POST", "/template/create", {"name": "T", "description": "d", "systemPrompt": "p"}, "batch_upsert_nodes"),
    ("POST", "/create", {"name": "Helper"}, "batch_upsert_nodes"),
    ("GET", "/sa/internal/service-account", None, "get_agent"),
    ("GET", "/web-search-usage/duckduckgo", None, "get_agents_by_web_search_provider"),
    ("GET", "/model-usage/m1", None, "get_agents_by_model_key"),
    ("GET", "/private", None, "check_agent_permission"),
    ("GET", "/", None, "get_all_agents"),
    ("PUT", "/private", {"name": "renamed"}, "update_agent"),
    ("DELETE", "/private", None, "delete_agent"),
]


@pytest.mark.parametrize(("method", "path", "body", "failing"), _OUTAGES, ids=[f"{m} {p}" for m, p, _, _ in _OUTAGES])
def test_a_graph_outage_is_one_classed_text_free_line(
    graph: InMemoryGraph, caplog, method: str, path: str, body: dict | None, failing: str,
) -> None:
    graph.fail(failing, ConnectionError(SENTINEL))

    response = _client(graph).request(method, BASE + path, headers=as_user("alice"), json=body)

    assert response.status_code >= 500, f"{method} {path}: a database outage answered {response.status_code}"
    assert _loud(caplog) == [EVENT], f"{method} {path}: the failure is logged by {_loud(caplog)}, not once by {EVENT}"
    fields = failure_line(_records(caplog), EVENT, "unavailable", logging.WARNING, SENTINEL)
    assert fields.get("cause_type") == "ConnectionError"
    assert_not_logged(_records(caplog), SENTINEL)


def test_a_failed_toolset_replace_is_logged_once(graph: InMemoryGraph, caplog) -> None:
    """``_create_toolset_edges`` logged and re-raised, then the route logged it again, then the request line."""
    real = graph.batch_upsert_nodes

    async def tools_fail(nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        if collection == "agentTools":
            raise ConnectionError(SENTINEL)
        return await real(nodes, collection, transaction)
    graph.batch_upsert_nodes = tools_fail

    response = _client(graph).put(f"{BASE}/private", headers=as_user("alice"), json={"toolsets": [
        {"name": "Slack", "instanceId": "inst-1", "tools": [{"name": "send"}]},
    ]})

    assert response.status_code == 500
    assert _loud(caplog) == [EVENT], f"one failure, logged by {_loud(caplog)}"
    failure_line(_records(caplog), EVENT, "unavailable", logging.WARNING, SENTINEL)
    assert _end(caplog)[1].get("agent_id") == "private"


def test_a_template_the_schema_refuses_is_the_callers_mistake_logged_once(graph: InMemoryGraph, caplog) -> None:
    graph.fail("batch_upsert_nodes", SchemaValidationError("agentTemplates", "at 'tags': not an array"))

    response = _client(graph).post(f"{BASE}/template/create", headers=as_user("alice"), json={
        "name": "T", "description": "d", "systemPrompt": "p", "tags": "x",
    })

    assert response.status_code == 400
    assert _loud(caplog) == [], f"a 400 is the caller's mistake, not an ERROR: {_loud(caplog)}"
    record, fields = _end(caplog)
    assert (record.levelno, fields.get("error_class"), fields.get("reason")) == (logging.INFO, "bad_request", "schema_rejected")


# --- a step the route swallows is one agent.route.failed line ---------------------------------------------------

def test_an_mcp_server_that_fails_to_attach_on_create_is_one_line_with_its_id(graph: InMemoryGraph, caplog) -> None:
    real = graph.batch_upsert_nodes

    async def github_fails(nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        if collection == "agentMcpServers" and nodes[0]["instanceId"] == "i1":
            raise ConnectionError(SENTINEL)
        return await real(nodes, collection, transaction)
    graph.batch_upsert_nodes = github_fails

    response = _client(graph).post(f"{BASE}/create", headers=as_user("alice"), json={"name": "Helper", "mcpServers": [
        {"instanceId": "i1", "name": "github", "tools": [{"name": "issues"}]},
        {"instanceId": "i2", "name": "linear", "tools": [{"name": "search"}]},
    ]})

    assert response.status_code == 200 and response.json()["status"] == "partial_success"
    assert _loud(caplog) == [ROUTE_FAILED], f"one failed attachment, logged by {_loud(caplog)}"
    fields = failure_line(_records(caplog), ROUTE_FAILED, "unavailable", logging.WARNING, SENTINEL)
    assert (fields.get("op"), fields.get("mcp_server_id")) == ("attach_mcp_server", "i1")
    assert fields.get("agent_id") == response.json()["agent"]["_key"]


def test_models_that_cannot_be_enriched_are_one_line(graph: InMemoryGraph, caplog) -> None:
    graph.nodes[AGENTS]["private"]["models"] = ["m1_gpt"]

    response = _client(graph, _ConfigOutage()).get(f"{BASE}/private", headers=as_user("alice"))

    assert response.status_code == 200
    fields = failure_line(_records(caplog), ROUTE_FAILED, "unavailable", logging.WARNING, SENTINEL)
    assert (fields.get("op"), fields.get("agent_id")) == ("enrich_models", "private")


# --- the caller's mistakes stay 400 ---------------------------------------------------------------------------------

def _rejection() -> SchemaValidationError:
    return SchemaValidationError("agentInstances", "at 'tags': not an array")


@pytest.mark.parametrize(("method", "path", "body", "failing"), [
    ("PUT", "/private", {"tags": "x"}, "update_agent"),  # Postgres `@_soft` and Neo4j both hand it to the route as is
    ("PUT", "/template/tpl", {"tags": "x"}, "update_agent_template"),
    # The new agent's document carries the caller's values, so its rejection is a 400 (one of a field we compute
    # there would be our drift: a known limitation).
    ("POST", "/create", {"name": "Helper"}, "batch_upsert_nodes"),
])
@pytest.mark.parametrize("wrapped", [False, True], ids=["raised", "wrapped"])
def test_a_schema_rejection_is_a_counted_quiet_400(
    graph: InMemoryGraph, caplog, method: str, path: str, body: dict, failing: str, wrapped: bool,
) -> None:
    rejection = _rejection()
    if wrapped:
        try:
            raise RuntimeError("write failed") from rejection
        except RuntimeError as outer:
            graph.fail(failing, outer)
    else:
        graph.fail(failing, rejection)

    with patch("app.api.routes.agent.record_schema_rejection") as counted:
        response = _client(graph).request(method, BASE + path, headers=as_user("alice"), json=body)

    assert response.status_code == 400
    assert response.json()["detail"].startswith("We couldn't")
    counted.assert_called_once_with("agentInstances")
    assert _loud(caplog) == [], f"a 400 is not an alert: {_loud(caplog)}"
    record, fields = _end(caplog)
    assert (record.levelno, fields.get("error_class"), fields.get("reason")) == (logging.INFO, "bad_request", "schema_rejected")


_TEMPLATE = {"name": "T", "description": "d", "systemPrompt": "p"}
_NOT_TEXT = [
    *[("POST", "/template/create", {**_TEMPLATE, field: 123}) for field in ("name", "description", "startMessage", "systemPrompt")],
    *[("POST", "/create", {"name": "H", field: 123}) for field in ("description", "startMessage", "systemPrompt", "instructions")],
    ("POST", "/create", {"name": 123}),
    ("POST", "/create", {"name": "H", "toolsets": [{"name": 5}]}),
    ("POST", "/create", {"name": "H", "knowledge": [{"connectorId": 5}]}),
    ("PUT", "/private", {"toolsets": [{"name": 5}]}),
    ("PUT", "/private", {"knowledge": [{"connectorId": 5}]}),
]


@pytest.mark.parametrize(("method", "path", "body"), _NOT_TEXT, ids=[
    f"{m} {p} {next(k for k, v in b.items() if not isinstance(v, str))}" for m, p, b in _NOT_TEXT
])
def test_a_text_field_that_is_not_text_is_the_callers_mistake(
    graph: InMemoryGraph, caplog, method: str, path: str, body: dict,
) -> None:
    response = _client(graph).request(method, BASE + path, headers=as_user("alice"), json=body)

    assert response.status_code == 400
    assert response.json()["detail"].endswith("must be text")
    assert _loud(caplog) == [], f"a 400 is the caller's mistake, not an ERROR: {_loud(caplog)}"
    record, fields = _end(caplog)
    assert (record.levelno, fields.get("error_class")) == (logging.INFO, "bad_request")


def test_an_optional_text_field_sent_as_null_takes_its_default(graph: InMemoryGraph) -> None:
    response = _client(graph).post(f"{BASE}/create", headers=as_user("alice"), json={
        "name": "H", "description": None, "startMessage": None, "systemPrompt": None, "instructions": None,
        "toolsets": [{"name": None}], "knowledge": [{"connectorId": None}],
    })

    assert response.status_code == 200
    agent = response.json()["agent"]
    assert (agent["description"], agent["instructions"]) == ("AI agent for task automation", None)


def test_a_store_data_error_is_a_400_without_its_text(graph: InMemoryGraph, caplog) -> None:
    """A Postgres 22xxx or Neo4j data exception, as the database client stamps it."""
    data_error = RuntimeError(SENTINEL)
    error_class.mark_error(data_error, "bad_request", hide_text=True)
    graph.fail("update_agent", data_error)

    response = _client(graph).put(f"{BASE}/private", headers=as_user("alice"), json={"name": "x"})

    assert response.status_code == 400
    assert _loud(caplog) == []
    record, fields = _end(caplog)
    assert (record.levelno, fields.get("error_class"), fields.get("agent_id")) == (logging.INFO, "bad_request", "private")
    assert_not_logged(_records(caplog), SENTINEL)


@pytest.mark.parametrize(("method", "path", "body", "who", "status", "cls"), [
    ("GET", "/nope", None, "alice", 404, "not_found"),
    ("DELETE", "/shared", None, "bob", 403, "forbidden"),
    ("PUT", "/private", {"models": [{"modelKey": "m1", "isReasoning": False}]}, "alice", 400, "bad_request"),
])
def test_a_refusal_is_one_quiet_line_with_the_agent(
    graph: InMemoryGraph, caplog, method: str, path: str, body: dict | None, who: str, status: int, cls: str,
) -> None:
    graph.add_agent("shared", "alice", share_with_org=True)

    response = _client(graph).request(method, BASE + path, headers=as_user(who), json=body)

    assert response.status_code == status
    assert _loud(caplog) == []
    record, fields = _end(caplog)
    assert (record.levelno, fields.get("error_class"), fields.get("agent_id")) == (logging.INFO, cls, path.strip("/"))


# --- outages that reach the route on a real store -------------------------------------------------------------------

def test_a_user_lookup_outage_is_a_text_free_503(graph: InMemoryGraph, caplog) -> None:
    graph.fail("get_user_by_user_id", ConnectionError(SENTINEL))

    response = _client(graph).get(f"{BASE}/", headers=as_user("alice"))

    assert response.status_code == 503
    assert _loud(caplog) == [EVENT]
    failure_line(_records(caplog), EVENT, "unavailable", logging.WARNING, SENTINEL)


def test_service_account_knowledge_that_cannot_be_checked_is_a_text_free_503(graph: InMemoryGraph, caplog) -> None:
    graph.fail("get_org_apps", ConnectionError(SENTINEL))

    response = _client(graph).post(f"{BASE}/create", headers=as_user("alice"), json={
        "name": "Helpdesk", "isServiceAccount": True, "knowledge": [{"connectorId": "drive-team"}],
    })

    assert response.status_code == 503
    assert _loud(caplog) == [EVENT]
    failure_line(_records(caplog), EVENT, "unavailable", logging.WARNING, SENTINEL)


def test_an_mcp_write_failing_on_update_keeps_its_cause(graph: InMemoryGraph, caplog) -> None:
    real = graph.batch_upsert_nodes

    async def servers_fail(nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        if collection == "agentMcpServers":
            raise ConnectionError(SENTINEL)
        return await real(nodes, collection, transaction)
    graph.batch_upsert_nodes = servers_fail

    response = _client(graph).put(f"{BASE}/private", headers=as_user("alice"), json={"mcpServers": [
        {"instanceId": "i1", "name": "github", "tools": [{"name": "issues"}]},
    ]})

    assert response.status_code == 500
    assert _loud(caplog) == [EVENT]
    fields = failure_line(_records(caplog), EVENT, "unavailable", logging.WARNING, SENTINEL)
    assert fields.get("cause_type") == "ConnectionError"


def test_a_bug_is_one_error_line_with_its_stack_once(graph: InMemoryGraph, caplog) -> None:
    with patch("app.api.routes.agent._mark_deprecated_tools", side_effect=KeyError(SENTINEL)):
        response = _client(graph).get(f"{BASE}/private", headers=as_user("alice"))

    assert response.status_code == 500
    assert _loud(caplog) == [EVENT]
    record = next(r for r in _records(caplog) if r.getMessage() == EVENT)
    failure_line(_records(caplog), EVENT, "internal", logging.ERROR, SENTINEL)
    assert record.exc_text and "KeyError: <message omitted>" in record.exc_text


# --- failures after a failure, and steps the route goes on without -----------------------------------------------

def _tools_fail(graph: InMemoryGraph) -> None:
    real = graph.batch_upsert_nodes

    async def tools_fail(nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        if collection == "agentTools":
            raise ConnectionError(SENTINEL)
        return await real(nodes, collection, transaction)
    graph.batch_upsert_nodes = tools_fail


_SLACK = {"toolsets": [{"name": "Slack", "instanceId": "inst-1", "tools": [{"name": "send"}]}]}


def test_a_rollback_that_fails_after_a_failed_save_is_its_own_line(graph: InMemoryGraph, caplog) -> None:
    _tools_fail(graph)
    graph.fail("rollback_transaction", ConnectionError(SENTINEL))

    response = _client(graph).put(f"{BASE}/private", headers=as_user("alice"), json=_SLACK)

    assert response.status_code == 500
    assert sorted(_loud(caplog)) == [ROUTE_FAILED, EVENT]
    fields = failure_line(_records(caplog), ROUTE_FAILED, "unavailable", logging.WARNING, SENTINEL)
    assert (fields.get("op"), fields.get("agent_id")) == ("rollback", "private")


def test_a_cleanup_that_fails_after_a_failed_save_is_its_own_line(graph: InMemoryGraph, caplog) -> None:
    _tools_fail(graph)
    graph.fail("delete_nodes", ConnectionError(SENTINEL))

    response = _client(graph).put(f"{BASE}/private", headers=as_user("alice"), json=_SLACK)

    assert response.status_code == 500
    cleanups = [r for r in _records(caplog) if r.getMessage() == ROUTE_FAILED]
    assert cleanups and set(_loud(caplog)) == {ROUTE_FAILED, EVENT}
    for record in cleanups:
        fields = log_record_fields(record)
        assert (fields.get("op"), fields.get("error_class")) == ("cleanup", "unavailable")
        assert isinstance(fields.get("nodes"), int) or fields.get("nodes") is None
    assert_not_logged(_records(caplog), SENTINEL)


def test_a_refused_delete_is_rolled_back_once(graph: InMemoryGraph, caplog) -> None:
    async def refuse(*_a: object, **_k: object) -> bool:
        return False
    graph.delete_agent = refuse

    response = _client(graph).delete(f"{BASE}/private", headers=as_user("alice"))

    assert response.status_code == 500
    assert len(graph.calls_to("rollback_transaction")) == 1
    assert _loud(caplog) == [EVENT]


def test_a_refused_delete_whose_rollback_fails_is_rolled_back_once(graph: InMemoryGraph, caplog) -> None:
    async def refuse(*_a: object, **_k: object) -> bool:
        return False
    graph.delete_agent = refuse
    graph.fail("rollback_transaction", ConnectionError(SENTINEL))

    response = _client(graph).delete(f"{BASE}/private", headers=as_user("alice"))

    assert response.status_code == 500
    assert len(graph.calls_to("rollback_transaction")) == 1
    assert sorted(_loud(caplog)) == [ROUTE_FAILED, EVENT]
    fields = failure_line(_records(caplog), ROUTE_FAILED, "unavailable", logging.WARNING, SENTINEL)
    assert (fields.get("op"), fields.get("agent_id")) == ("rollback", "private")


def test_a_service_agents_credentials_that_cannot_be_removed_leave_the_delete_done(graph: InMemoryGraph, caplog) -> None:
    with patch("app.agents.mcp.lifecycle.remove_owner_credentials", new=AsyncMock(side_effect=ConnectionError(SENTINEL))):
        response = _client(graph).delete(f"{BASE}/sa", headers=as_user("alice"))

    assert response.status_code == 200
    fields = failure_line(_records(caplog), ROUTE_FAILED, "unavailable", logging.WARNING, SENTINEL)
    assert (fields.get("op"), fields.get("agent_id")) == ("remove_mcp_credentials", "sa")


def test_a_knowledge_limit_that_cannot_be_sized_does_not_block_the_create(graph: InMemoryGraph, caplog) -> None:
    graph.get_selection_nodes = AsyncMock(side_effect=ConnectionError(SENTINEL))

    response = _client(graph).post(f"{BASE}/create", headers=as_user("alice"), json={
        "name": "Helper", "knowledge": [{"connectorId": "c1", "filters": {"records": ["r1"]}}],
    })

    assert response.status_code == 200
    fields = failure_line(_records(caplog), ROUTE_FAILED, "unavailable", logging.WARNING, SENTINEL)
    assert fields.get("op") == "size_knowledge_limit"
    # the org is named only by the context field every line carries (docs/logging.md, "Context and fields"),
    # which the request's bound org context sets; the failure itself names it nowhere else
    assert [k for k, v in fields.items() if "org-1" in str(v) and k != "org_id"] == []


# --- what the line says on success ----------------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "path", "body", "shared"), [
    ("PUT", "/private", {"shareWithOrg": True}, True),
    ("PUT", "/shared", {"shareWithOrg": False}, False),
    ("PUT", "/private", {"name": "renamed"}, None),
    ("POST", "/create", {"name": "Helper", "shareWithOrg": True}, True),
    ("POST", "/create", {"name": "Helper"}, None),
])
def test_sharing_is_on_the_request_line(
    graph: InMemoryGraph, caplog, method: str, path: str, body: dict, shared: bool | None,
) -> None:
    graph.add_agent("shared", "alice", share_with_org=True)

    response = _client(graph).request(method, BASE + path, headers=as_user("alice"), json=body)

    assert response.status_code == 200
    assert _end(caplog)[1].get("shared_with_org") is shared


def test_the_create_binds_its_agent_for_its_own_lines(graph: InMemoryGraph, caplog) -> None:
    response = _client(graph).post(f"{BASE}/create", headers=as_user("alice"), json={"name": "Helper"})

    key = response.json()["agent"]["_key"]
    inside = [r for r in caplog.records if r.filename == "graph_data_store.py"]
    assert inside and all(log_record_fields(r).get("agent_id") == key for r in inside)


@pytest.mark.parametrize(("method", "path", "body"), [
    ("GET", "/template/tpl", None),
    ("PUT", "/template/tpl", {"name": "T2"}),
    ("DELETE", "/template/tpl", None),
    ("POST", "/template/tpl/clone", None),
])
def test_template_routes_name_the_template(graph: InMemoryGraph, caplog, method: str, path: str, body: dict | None) -> None:
    response = _client(graph).request(method, BASE + path, headers=as_user("alice"), json=body)

    assert response.status_code == 200
    assert _end(caplog)[1].get("template_id") == "tpl"


def test_a_created_template_is_named(graph: InMemoryGraph, caplog) -> None:
    response = _client(graph).post(f"{BASE}/template/create", headers=as_user("alice"), json={
        "name": "T", "description": "d", "systemPrompt": "p",
    })

    assert _end(caplog)[1].get("template_id") == response.json()["template"]["_key"]


def test_a_skipped_skill_is_named_without_a_graph_key(graph: InMemoryGraph, caplog) -> None:
    graph.add_node("agentSkills", {"_key": "org-1_bobs", "orgId": "org-1", "createdBy": "k-bob", "source": "user"})

    response = _client(graph).post(f"{BASE}/create", headers=as_user("alice"), json={"name": "H", "skills": ["bobs"]})

    assert response.status_code == 200
    (skipped,) = [log_record_fields(r) for r in _records(caplog) if r.getMessage() == "agent.skill.skipped"]
    assert (skipped.get("reason"), skipped.get("skill")) == ("not_owned", "bobs")
    assert_not_logged(_records(caplog), "k-alice")


def test_chat_routes_do_not_bind_the_agent_id() -> None:
    """The assistant's chat names `agentIdPlaceholder`; P3b owns the chat lines."""
    from app.api.routes.agent import _AGENT_ID_LOG, router

    for route in router.routes:
        if "/chat" in route.path:
            assert _AGENT_ID_LOG not in route.dependencies, route.path


def test_a_schema_rejection_of_a_document_we_built_is_our_error(graph: InMemoryGraph, caplog) -> None:
    """The permission edge holds nothing the caller sent: its rejection is our drift, an ERROR, and still counted."""
    graph.fail("batch_create_edges", SchemaValidationError("permission", "at 'role': not one of the roles"))

    with patch("app.api.routes.agent.record_schema_rejection") as counted:
        response = _client(graph).post(f"{BASE}/template/create", headers=as_user("alice"), json={
            "name": "T", "description": "d", "systemPrompt": "p",
        })

    assert response.status_code == 500
    counted.assert_called_once_with("permission")
    fields = failure_line(_records(caplog), EVENT, "internal", logging.ERROR)
    assert fields.get("reason") == "schema_rejected"


def test_a_template_copy_the_schema_refuses_is_our_error(graph: InMemoryGraph, caplog) -> None:
    """A clone writes only the stored template: the Postgres and Neo4j providers hand its rejection to the route as
    is, and it is our drift (a stored template that no longer fits the schema), not the caller's mistake."""
    graph.fail("clone_agent_template", SchemaValidationError("agentTemplates", "at 'tags': not an array"))

    with patch("app.api.routes.agent.record_schema_rejection") as counted:
        response = _client(graph).post(f"{BASE}/template/tpl/clone", headers=as_user("alice"))

    assert response.status_code == 500
    counted.assert_called_once_with("agentTemplates")
    assert _loud(caplog) == [EVENT]
    fields = failure_line(_records(caplog), EVENT, "internal", logging.ERROR)
    assert (fields.get("reason"), fields.get("template_id")) == ("schema_rejected", "tpl")


class _AttachmentsUnreadable(FakeConfigService):
    """The personal-instance lookup of attachment ``attached-1`` fails."""

    async def get_config(self, key: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - a JSON value
        if key.endswith("/attached-1"):
            raise ConnectionError(SENTINEL)
        return await super().get_config(key, *args, **kwargs)


def _attach_mcp_server(graph: InMemoryGraph) -> None:
    graph.add_node("agentMcpServers", {"_key": "mcp-1", "instanceId": "attached-1"})
    graph.add_edge("agentHasMcpServer", {"_from": f"{AGENTS}/private", "_to": "agentMcpServers/mcp-1"})


def _attach_toolset(graph: InMemoryGraph) -> None:
    graph.add_node("agentToolsets", {"_key": "ts-1", "name": "jira", "instanceId": "attached-1"})
    graph.add_edge("agentHasToolset", {"_from": f"{AGENTS}/private", "_to": "agentToolsets/ts-1"})


@pytest.mark.parametrize("attach", [_attach_mcp_server, _attach_toolset], ids=["mcp_server", "toolset"])
def test_attachments_that_cannot_be_checked_for_sharing_are_a_text_free_503(graph: InMemoryGraph, caplog, attach) -> None:
    attach(graph)
    config = _AttachmentsUnreadable({"/services/mcp/user-instances/org-1/k-alice/attached-1": {}})

    response = _client(graph, config).put(f"{BASE}/private", headers=as_user("alice"), json={"shareWithOrg": True})

    assert response.status_code == 503
    assert _loud(caplog) == [EVENT]
    failure_line(_records(caplog), EVENT, "unavailable", logging.WARNING, SENTINEL)
