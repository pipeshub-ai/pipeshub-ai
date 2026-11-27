"""A 4xx detail never quotes a path value that matched nothing.

Node passes a Python ``detail`` (or a graph provider's ``reason``) on as the error message: the
client shows it and Node's ``http.request.error`` line logs it. A path value that matched no
record can be anything a client typed, an email included, so the refusal names the kind of thing
that was not found and leaves the value out (the client knows what it sent). Each test sends
EMAIL as the path value and pins the exact text, plus the log line raised beside it where there is one.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.agent_loop_lib.core.exceptions import RegistryError
from app.api.routes import agent, ai_models_registry, mcp_servers, skills, toolsets
from app.connectors.api import router
from app.connectors.sources.localKB.api import knowledge_hub_router
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres import kb as postgres_kb

EMAIL = "ada.lovelace@example.com"
KB_NOT_FOUND = "Knowledge base not found"
FOLDER_NOT_FOUND = (
    "The folder was not found in knowledge base 'Docs' (kb1). "
    "The folder may not exist or may belong to a different knowledge base."
)


async def _refusal(call) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        await call
    return caught.value


def _logged(logger: MagicMock) -> str:
    return " ".join(str(c) for c in logger.mock_calls)


# --- route details -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("perm, agent_doc", [(None, None), ({"role": "OWNER"}, None)])
async def test_an_unknown_agent_is_not_named(monkeypatch: pytest.MonkeyPatch, perm, agent_doc) -> None:
    graph = SimpleNamespace(check_agent_permission=AsyncMock(return_value=perm),
                            get_agent=AsyncMock(return_value=agent_doc))
    monkeypatch.setattr(toolsets, "_get_user_context", lambda _r: {"user_id": "u1", "org_id": "o1"})
    monkeypatch.setattr(toolsets, "_get_graph_provider", lambda _r: graph)
    monkeypatch.setattr(toolsets, "get_user_or_503", AsyncMock(return_value={"_key": "u1"}))

    refused = await _refusal(toolsets._resolve_agent_with_permission(EMAIL, MagicMock()))

    assert (refused.status_code, refused.detail) == (404, "Agent not found.")


@pytest.mark.parametrize("route", ["update_toolset_oauth_config", "delete_toolset_oauth_config"])
async def test_an_unknown_toolset_oauth_config_is_not_named(monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    monkeypatch.setattr(toolsets, "_get_user_context", lambda _r: {"user_id": "u1", "org_id": "o1"})
    monkeypatch.setattr(toolsets, "_check_user_is_admin", AsyncMock(return_value=True))
    monkeypatch.setattr(toolsets, "_get_oauth_config_by_id", AsyncMock(return_value=None))
    request = MagicMock(body=AsyncMock(return_value=b"{}"))

    refused = await _refusal(getattr(toolsets, route)("jira", EMAIL, request, config_service=MagicMock()))

    assert (refused.status_code, refused.detail) == (404, "OAuth configuration not found.")


@pytest.mark.parametrize("metadata", [None, {"isInternal": True}])
def test_an_unknown_toolset_type_is_not_named(metadata) -> None:
    registry = SimpleNamespace(get_toolset_metadata=lambda *_a, **_k: metadata)

    with pytest.raises(HTTPException) as caught:
        toolsets._get_toolset_metadata(registry, EMAIL)

    assert (caught.value.status_code, caught.value.detail) == (404, "Toolset not found in registry")


def test_an_unknown_agent_template_is_not_named() -> None:
    refused = agent.AgentTemplateNotFoundError(EMAIL)

    assert (refused.status_code, refused.detail) == (404, "Agent template not found or you don't have access to it")


async def test_an_unknown_mcp_server_type_is_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_servers, "_get_user_context", lambda _r: {"user_id": "u1", "org_id": "o1"})
    monkeypatch.setattr(mcp_servers, "_get_mcp_registry", lambda _r: SimpleNamespace(get_template=lambda _t: None))

    refused = await _refusal(mcp_servers.get_catalog_template(MagicMock(), EMAIL))

    assert (refused.status_code, refused.detail) == (404, "Unknown MCP server type.")


async def test_an_unknown_ai_model_provider_is_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_models_registry.ai_model_registry, "get_provider_schema", lambda *_a: None)

    refused = await _refusal(ai_models_registry.get_provider_schema(EMAIL, None))

    assert (refused.status_code, refused.detail) == (404, "Provider not found")


def _skills_manager(monkeypatch: pytest.MonkeyPatch, **methods) -> None:
    manager = SimpleNamespace(**{name: AsyncMock(**spec) for name, spec in methods.items()})
    monkeypatch.setattr(skills, "_build_manager", AsyncMock(return_value=(manager, {})))
    monkeypatch.setattr(skills, "_reject_if_builtin_skill", AsyncMock())


@pytest.mark.parametrize("route, args, methods, detail", [
    ("deprecate_skill", (EMAIL, SimpleNamespace(reason="r", replaced_by=None)),
     {"deprecate": {"return_value": False}}, "Skill not found."),
    # A delete may read the skill first, to decide who may delete it.
    ("delete_skill", (EMAIL,),
     {"get_skill": {"side_effect": RegistryError(f"Skill {EMAIL!r} not found")}, "delete": {"return_value": False}},
     "Skill not found."),
    ("get_version", (EMAIL, EMAIL), {"get_version": {"return_value": None}}, "Skill version not found."),
    ("write_resource", (EMAIL, SimpleNamespace(path="a.md", content="x")),
     {"write_resource": {"return_value": False}}, "Skill not found."),
    ("remove_resource", (EMAIL, EMAIL), {"remove_resource": {"return_value": False}},
     "Resource not found for this skill."),
])
async def test_an_unknown_skill_is_not_named(monkeypatch: pytest.MonkeyPatch, route, args, methods, detail) -> None:
    _skills_manager(monkeypatch, **methods)
    kwargs = {"detach": False} if route == "delete_skill" else {}

    refused = await _refusal(getattr(skills, route)(MagicMock(), *args, **kwargs))

    assert (refused.status_code, refused.detail) == (404, detail)


@pytest.mark.parametrize("store_text, detail", [
    (f"Skill {EMAIL!r} not found", "Skill not found."),
    (f"Skill {'Resource x'!r} not found", "Skill not found."),  # the first word decides, not a word in the name
    (f"Version {EMAIL!r} of skill {EMAIL!r} not found", "Skill version not found."),
    (f"Resource {EMAIL!r} not found for skill {EMAIL!r}", "Resource not found for this skill."),
    (f"Candidate {EMAIL!r} not found", "Candidate not found."),
])
async def test_a_skill_store_miss_is_not_named(monkeypatch: pytest.MonkeyPatch, store_text, detail) -> None:
    _skills_manager(monkeypatch, load_resource={"side_effect": RegistryError(store_text)})

    refused = await _refusal(skills.get_resource(MagicMock(), EMAIL, path=EMAIL))

    assert (refused.status_code, refused.detail) == (404, detail)


def test_a_skill_store_conflict_keeps_its_text() -> None:
    refused = skills._handle_registry_error(RegistryError("Skill 'x' already exists"))

    assert (refused.status_code, refused.detail) == (409, "Skill 'x' already exists")


def test_a_malformed_parent_id_is_not_quoted() -> None:
    with pytest.raises(HTTPException) as caught:
        knowledge_hub_router._validate_uuid_format(EMAIL, "parent_id")

    assert (caught.value.status_code, caught.value.detail) == (400, "Invalid UUID format for parent_id")


# --- connectors router: the detail and the log line raised beside it ----------------------------------------------

def _router_request(monkeypatch: pytest.MonkeyPatch, registry: object = None) -> tuple[MagicMock, MagicMock]:
    logger = MagicMock()
    request = MagicMock()
    request.app.container.logger = MagicMock(return_value=logger)
    request.app.state.connector_registry = registry
    request.state.user = {"userId": "u1", "orgId": "o1"}
    monkeypatch.setattr(router, "check_beta_connector_access", AsyncMock())
    monkeypatch.setattr(router, "_get_user_context",
                        lambda _r: {"user_id": "u1", "org_id": "o1", "is_admin": True})
    return request, logger


async def test_an_unknown_connector_type_schema_is_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    request, logger = _router_request(
        monkeypatch, SimpleNamespace(get_connector_metadata=AsyncMock(return_value=None)))

    refused = await _refusal(router.get_connector_schema(EMAIL, request))

    assert (refused.status_code, refused.detail) == (404, "Connector type not found")
    assert EMAIL not in _logged(logger)
    logger.error.assert_any_call("Connector type not found")


async def test_an_unknown_connector_type_check_is_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    request, _ = _router_request(monkeypatch, SimpleNamespace(get_connector_metadata=AsyncMock(return_value=None)))

    refused = await _refusal(router.check_connector_connection(EMAIL, request))

    assert (refused.status_code, refused.detail) == (404, "Connector type not found in registry")


async def test_an_unknown_oauth_registry_type_is_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.connectors.core.registry import oauth_config_registry

    request, logger = _router_request(monkeypatch)
    monkeypatch.setattr(oauth_config_registry, "get_oauth_config_registry",
                        lambda: SimpleNamespace(get_connector_registry_info=lambda _t: None))

    refused = await _refusal(router.get_oauth_config_registry_by_type(EMAIL, request))

    assert (refused.status_code, refused.detail) == (404, "OAuth configuration not found for this connector type")
    assert EMAIL not in _logged(logger)


@pytest.mark.parametrize("route", ["get_oauth_config_by_id", "update_oauth_config", "delete_oauth_config"])
async def test_an_unknown_oauth_config_is_not_named(monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    request, logger = _router_request(monkeypatch)
    request.json = AsyncMock(return_value={})
    monkeypatch.setattr(router, "resolve_oauth_config", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "_get_oauth_configs_from_etcd", AsyncMock(return_value=[]))
    monkeypatch.setattr(router, "forbid_inherited_oauth_mutation", AsyncMock())

    refused = await _refusal(getattr(router, route)("jira", EMAIL, request, config_service=AsyncMock()))

    assert (refused.status_code, refused.detail) == (404, "OAuth config not found or access denied")
    assert EMAIL not in _logged(logger)
    logger.error.assert_any_call("OAuth config not found or access denied")


async def test_an_unknown_filter_field_is_not_named(monkeypatch: pytest.MonkeyPatch) -> None:
    request, _ = _router_request(
        monkeypatch, SimpleNamespace(get_connector_metadata=AsyncMock(return_value={"config": {}})))
    monkeypatch.setattr(router, "_get_and_validate_connector_instance",
                        AsyncMock(return_value={"type": "JIRA", "authType": "API_TOKEN"}))
    monkeypatch.setattr(router, "_validate_connector_permissions", lambda *_a, **_k: None)
    monkeypatch.setattr(router, "_find_filter_field_config", lambda *_a: None)

    refused = await _refusal(router.get_filter_field_options("c1", EMAIL, request))

    assert (refused.status_code, refused.detail) == (404, "Filter field not found for connector JIRA")


# --- graph providers: the reason Node shows -------------------------------------------------------------------------

def _provider(cls: type, *, kb: bool = True, role: str | None = "OWNER") -> object:
    provider = object.__new__(cls)
    provider.logger = MagicMock()
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1", "_key": "u1"})
    provider.kb_exists = AsyncMock(return_value=kb)
    provider.get_user_kb_permission = AsyncMock(return_value=role)
    provider.get_and_validate_folder_in_kb = AsyncMock(return_value=None)
    provider._fetch_kb_name = AsyncMock(return_value="Docs")
    return provider


PROVIDERS = [postgres_kb.KbMixin, ArangoHTTPProvider, Neo4jProvider]


@pytest.fixture(autouse=True)
def _postgres_org(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(postgres_kb, "current_org_id", lambda: "o1")


@pytest.mark.parametrize("cls", PROVIDERS)
@pytest.mark.parametrize("kb, role", [(False, None), (True, None)])
@pytest.mark.parametrize("check", ["upload", "folder"])
async def test_an_unknown_or_hidden_knowledge_base_is_not_named(cls: type, kb: bool, role, check: str) -> None:
    provider = _provider(cls, kb=kb, role=role)

    result = await (provider._validate_upload_context(EMAIL, "u1", "o1") if check == "upload"
                    else provider._validate_folder_creation(EMAIL, "u1"))

    assert (result["valid"], result["code"], result["reason"]) == (False, 404, KB_NOT_FOUND)
    if not kb:  # a knowledge base that exists was matched: its id may be logged
        assert EMAIL not in _logged(provider.logger)


@pytest.mark.parametrize("cls", PROVIDERS)
async def test_an_unknown_upload_folder_is_not_named(cls: type) -> None:
    provider = _provider(cls)

    result = await provider._validate_upload_context("kb1", "u1", "o1", parent_folder_id=EMAIL)

    assert (result["valid"], result["code"], result["reason"]) == (False, 404, FOLDER_NOT_FOUND)


# --- kb_service: a destination or record that is not in the knowledge base ---------------------------------------

def _kb_service(**graph) -> KnowledgeBaseService:
    provider = AsyncMock()
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "u1"})
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.get_document = AsyncMock(return_value={})  # the KB is live, not being deleted
    provider.validate_folder_in_kb = AsyncMock(return_value=False)  # not a folder of it, live or in the trash
    for name, value in graph.items():
        setattr(provider, name, AsyncMock(return_value=value))
    return KnowledgeBaseService(MagicMock(), provider, AsyncMock(), processor_for_kb=AsyncMock())


async def test_an_unknown_parent_folder_is_not_named() -> None:
    service = _kb_service(_validate_folder_creation={"valid": True})

    result = await service.create_nested_folder("kb1", EMAIL, "Sub", "u1", "o1")

    assert (result["code"], result["reason"]) == (404, "The parent folder was not found in this knowledge base.")


@pytest.mark.parametrize("kb_context", [None, {"kb_id": "other-kb"}])
async def test_a_record_outside_the_knowledge_base_is_not_named(kb_context) -> None:
    service = _kb_service(_get_kb_context_for_record=kb_context)

    result = await service.move_record("kb1", EMAIL, None, "u1")

    assert (result["code"], result["reason"]) == (404, "The record was not found in this knowledge base.")
