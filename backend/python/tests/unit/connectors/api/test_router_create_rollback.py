"""A connector create that fails after its instance write takes back what it wrote, so a same-name retry works.

The real create route, ConnectorRegistry (name check and instance write), OAuth app helpers, ``_prepare_connector_config``
and ConfigurationService run on an in-memory key-value store whose chosen reads and writes raise. The graph is a dict that
keeps the apps nodes and org edges, checks names as the providers do and deletes a connector by id as every provider's
``delete_connector_instance`` does (a real-store check of that delete: tests/integration/graph_db).
"""
from __future__ import annotations

import copy
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.api import router as routes
from app.connectors.core.base.token_service.oauth_service import (
    OAuthStoreUnavailableError,
)
from app.connectors.core.registry import connector_registry as registry_module
from app.connectors.core.registry.connector_registry import (
    ConnectorInstanceNotDeletedError,
    ConnectorRegistry,
)

# Registers Drive's OAuth fields (clientId, secret clientSecret) under its type name.
from app.connectors.sources.google.drive.individual import connector as _drive  # noqa: F401
from app.utils.error_class import classify_error

LOGGER = "app.test.create_rollback"
LOG = logging.getLogger(LOGGER)
ORG, USER = "org-1", "user-1"
APPS_PATH = "/services/oauth/drive"
NAME = "My Drive"
APPS, ORGS, USERS = CollectionNames.APPS.value, CollectionNames.ORGS.value, CollectionNames.USERS.value
SHARED_APP = {"_id": "app-1", "orgId": ORG, "connectorType": "Drive", "oauthInstanceName": "Shared Drive app",
              "createdBy": USER, "config": {"clientId": "old-client", "clientSecret": "old-secret"}}
# Built at run time: a traceback prints source lines, so a literal would show in a stack.
SECRET = "-".join(("SECRET", "MARKER"))
NEW_CREDS = {"clientId": "new-client.apps.googleusercontent.com", "clientSecret": SECRET}
ROLLED_BACK = "The connector's settings could not be saved, so it was not created. Try again."
KEPT_APP = ("The connector's settings could not be saved, so it was not created. "
            "Its OAuth app was saved: pick that app when you try again.")
KEPT_APP_LATE = "The connector could not be created. Its OAuth app was saved: pick that app when you try again."
MAYBE_KEPT_APP = ("The connector could not be created. "
                  "Its OAuth app may have been saved: check the app list before you try again.")
FINISH_IT = "The connector was created, but its settings were not saved. Open it to finish setting it up, or delete it."
StoreRule = Callable[[str, int], bool]


def _never(_key: str, _n: int) -> bool:
    return False


def _is_connector_config(key: str) -> bool:
    return key.startswith("/services/connectors/") and key.endswith("/config")


class KV(InMemoryKeyValueStore):
    """Copies like Redis or etcd. ``fail_read``/``fail_write`` say whether the n-th read or write (from 1) of a key
    raises; ``before_read``/``before_write`` run first, as another request would at that moment."""

    def __init__(self) -> None:
        super().__init__(LOG)
        self.reads: dict[str, int] = {}
        self.writes: dict[str, int] = {}
        self.fail_read: StoreRule = _never
        self.fail_write: StoreRule = _never
        self.lost_reply: StoreRule = _never  # the write lands, then its answer is lost (it raises)
        self.before_read: Callable[[str, int], Awaitable[None]] | None = None
        self.before_write: Callable[[str, int], Awaitable[None]] | None = None

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        self.reads[key] = n = self.reads.get(key, 0) + 1
        if self.before_read:
            await self.before_read(key, n)
        if self.fail_read(key, n):
            raise ConnectionError("kv down")
        return copy.deepcopy(await super().get_key(key))

    async def create_key(self, key: str, value: Any, overwrite: bool = True, ttl: int | None = None) -> bool:  # noqa: ANN401
        self.writes[key] = n = self.writes.get(key, 0) + 1
        if self.before_write:
            await self.before_write(key, n)
        if self.fail_write(key, n):
            raise ConnectionError("kv down")
        if self.lost_reply(key, n):
            await super().create_key(key, copy.deepcopy(value), overwrite, ttl)
            raise ConnectionError("kv down")
        return await super().create_key(key, copy.deepcopy(value), overwrite, ttl)

    async def delete_key(self, key: str) -> bool:
        if self.fail_write(key, self.writes.get(key, 0) + 1):
            raise ConnectionError("kv down")
        return await super().delete_key(key)

    def saved(self, key: str) -> Any:  # noqa: ANN401
        item = self.store.get(key)
        return copy.deepcopy(item.value) if item else None


class Graph:
    """The apps nodes and org edges a create writes, the providers' name check, and their delete by id."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, dict[str, Any]]] = {ORGS: {ORG: {"_key": ORG}}, USERS: {USER: {"_key": USER}},
                                                             APPS: {}}
        self.edges: list[dict[str, Any]] = []
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.edge_writes_fail = False
        self.edge_writes_raise: Exception | None = None
        self.delete_answer: dict[str, Any] | Exception = {"success": True}
        # ArangoDB removes a connector's edges before the transaction that removes its node.
        self.delete_drops_edges_first = False

    async def get_document(self, key: str, collection: str, **_: Any) -> Any:  # noqa: ANN401
        return copy.deepcopy(self.nodes.get(collection, {}).get(key))

    async def get_account_type(self, _org_id: str) -> str:
        return "individual"

    async def check_connector_name_exists(self, collection: str, instance_name: str, scope: str, org_id: str,
                                          user_id: str, exclude_connector_id: str | None = None) -> bool:
        return any(
            doc["name"].strip().lower() == instance_name.strip().lower() and doc["orgId"] == org_id
            and doc["scope"] == scope and (scope == "team" or doc["createdBy"] == user_id) and key != exclude_connector_id
            for key, doc in self.nodes[collection].items()
        )

    async def batch_upsert_nodes(self, docs: list[dict[str, Any]], collection: str, **_: Any) -> bool:  # noqa: ANN401
        for doc in docs:
            self.nodes.setdefault(collection, {})[doc["_key"]] = copy.deepcopy(doc)
            self.created.append(doc["_key"])
        return True

    async def batch_create_edges(self, edges: list[dict[str, Any]], collection: str, **_: Any) -> bool:  # noqa: ANN401
        if self.edge_writes_fail:
            return False
        if self.edge_writes_raise is not None:
            raise self.edge_writes_raise
        self.edges.extend(copy.deepcopy(edges))
        return True

    async def delete_connector_instance(self, connector_id: str, org_id: str, transaction: str | None = None) -> dict[str, Any]:
        self.deleted.append(connector_id)
        if self.delete_drops_edges_first:
            self.edges = [e for e in self.edges if e["to_id"] != connector_id]
        if isinstance(self.delete_answer, Exception):
            raise self.delete_answer
        if not self.delete_answer.get("success"):
            return self.delete_answer
        self.nodes[APPS].pop(connector_id, None)
        self.edges = [e for e in self.edges if e["to_id"] != connector_id]
        return {"success": True}

    def add_connector(self, key: str, name: str = NAME) -> None:
        """A same-name connector another request wrote: past the name check at the same time as this one."""
        self.nodes[APPS][key] = {"_key": key, "name": name, "orgId": ORG, "scope": "personal", "createdBy": USER}
        self.edges.append({"from_id": ORG, "to_id": key})

    def connectors(self) -> list[dict[str, Any]]:
        return list(self.nodes[APPS].values())


def _metadata() -> dict[str, Any]:
    return {
        "scope": ["personal", "team"], "supportedAuthTypes": ["OAUTH", "NONE"],
        "config": {"auth": {
            "schemas": {"OAUTH": {"redirectUri": "connectors/oauth/callback/Drive", "fields": []}},
            "oauthConfigs": {"OAUTH": {"authorizeUrl": "https://accounts.google.com/o/oauth2/v2/auth",
                                       "tokenUrl": "https://oauth2.googleapis.com/token", "scopes": []}},
        }},
    }


@pytest.fixture
async def kv(monkeypatch: pytest.MonkeyPatch) -> KV:
    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))  # a hardened install refuses a key under 32 characters
    store = KV()
    await store.create_key("/services/endpoints", {"frontend": {"publicEndpoint": "https://pipeshub.example"}})
    return store


@pytest.fixture
async def config(kv: KV) -> ConfigurationService:
    service = ConfigurationService(LOG, kv)
    yield service
    await service.close()


@pytest.fixture
def graph() -> Graph:
    return Graph()


def _registry(graph: Graph) -> ConnectorRegistry:
    container = MagicMock()
    container.logger = MagicMock(return_value=LOG)
    registry = ConnectorRegistry(container)
    registry._graph_provider = graph  # type: ignore[assignment]
    registry._connectors["Drive"] = {"appGroup": "Google Workspace", "supportedAuthTypes": ["OAUTH", "NONE"], "config": {}}
    registry.get_connector_metadata = AsyncMock(return_value=_metadata())  # type: ignore[method-assign]
    return registry


def _body(auth: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"connectorType": "Drive", "instanceName": NAME, "scope": "personal", "authType": "OAUTH",
            "config": {"auth": dict(NEW_CREDS) if auth is None else auth}, **extra}


LINK_ONLY = {"connectorScope": "personal"}


async def _create(
    config: ConfigurationService, graph: Graph, body: dict[str, Any] | None = None, *, action_items: AsyncMock | None = None,
) -> dict[str, Any] | HTTPException:
    """The route's answer: its dict, or the HTTPException it raised."""
    request = MagicMock()
    request.state.user = {"userId": USER, "orgId": ORG, "role": "admin"}
    request.json = AsyncMock(return_value=copy.deepcopy(body or _body()))
    request.app.container.logger = MagicMock(return_value=LOG)
    request.app.container.connectors_map = {}
    request.app.state.connector_registry = _registry(graph)
    with (
        patch.object(routes, "resolve_config_service", return_value=config),
        patch.object(routes, "check_beta_connector_access", AsyncMock(return_value=None)),
        patch.object(routes, "_store_config_action_items", action_items or AsyncMock(return_value=None)),
    ):
        try:
            return await routes.create_connector_instance(request, graph)  # type: ignore[arg-type]
        except HTTPException as e:
            return e


def _refused(answer: object, status: int, detail: str | None = None) -> HTTPException:
    assert isinstance(answer, HTTPException), answer
    assert answer.status_code == status, (answer.status_code, answer.detail)
    if detail is not None:
        assert answer.detail == detail
    return answer


async def _retry_succeeds(config: ConfigurationService, graph: Graph, body: dict[str, Any] | None = None) -> None:
    answer = await _create(config, graph, body)
    assert isinstance(answer, dict), (answer.status_code, answer.detail)
    assert [c["name"] for c in graph.connectors()] == [NAME]


def _first_connector_config_write() -> StoreRule:
    """Fails the first write of any connector's config: the retry writes a new connector's key."""
    seen: list[str] = []

    def fail(key: str, _n: int) -> bool:
        if _is_connector_config(key):
            seen.append(key)
            return len(seen) == 1
        return False
    return fail


def _rollback_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event_name", None) == "connector.route.failed"]


# ---- every failure after the instance write leaves no connector, and the retry works

async def test_a_named_app_that_does_not_exist_leaves_no_connector(config: ConfigurationService, graph: Graph) -> None:
    _refused(await _create(config, graph, _body(LINK_ONLY, oauthConfigId="no-such-app")), 404)
    assert graph.connectors() == [] and graph.edges == []
    await _retry_succeeds(config, graph, _body(LINK_ONLY))


async def test_an_app_save_that_fails_leaves_no_connector(kv: KV, config: ConfigurationService, graph: Graph) -> None:
    kv.fail_write = lambda key, n: key == APPS_PATH and n == 1
    _refused(await _create(config, graph), 500, MAYBE_KEPT_APP)
    assert graph.connectors() == [] and graph.edges == []
    assert kv.saved(APPS_PATH) is None
    await _retry_succeeds(config, graph)


async def test_an_app_list_read_that_fails_after_the_instance_leaves_no_connector(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    # Read 1 is the check before the instance; read 2 is the app write's own, after it.
    kv.fail_read = lambda key, n: key == APPS_PATH and n == 2
    _refused(await _create(config, graph), 500)
    assert graph.connectors() == []
    assert kv.saved(APPS_PATH) is None
    await _retry_succeeds(config, graph)


async def test_an_app_name_taken_after_the_check_answers_409_and_leaves_no_connector(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    other = {**SHARED_APP, "_id": "app-2", "oauthInstanceName": NAME}

    async def another_admin_saves_an_app(key: str, n: int) -> None:
        if key == APPS_PATH and n == 2:
            await kv.create_key(APPS_PATH, [other])

    kv.before_read = another_admin_saves_an_app
    _refused(await _create(config, graph), 409)
    assert graph.connectors() == []
    assert kv.saved(APPS_PATH) == [other]


async def test_a_config_save_that_fails_removes_the_connector_and_keeps_the_app_it_created(
    kv: KV, config: ConfigurationService, graph: Graph, caplog: pytest.LogCaptureFixture,
) -> None:
    kv.fail_write = _first_connector_config_write()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        answer = _refused(await _create(config, graph), 500, KEPT_APP)
    assert isinstance(answer.__cause__, OAuthStoreUnavailableError)
    assert classify_error(answer) == "unavailable"
    assert graph.connectors() == [] and graph.edges == []
    (app,) = kv.saved(APPS_PATH)
    assert app["oauthInstanceName"] == NAME and app["config"]["clientSecret"] == SECRET
    assert not [key for key in kv.store if _is_connector_config(key)]
    assert _rollback_lines(caplog) == []
    assert SECRET not in caplog.text
    _refused(await _create(config, graph), 409)  # the same app name again, as the answer says
    await _retry_succeeds(config, graph, _body(LINK_ONLY, oauthConfigId=app["_id"]))


async def test_a_connector_that_links_the_new_app_before_the_rollback_keeps_a_working_link(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    # Once the app is in the list, another create can name it before this one fails and rolls back.
    first_config: list[str] = []
    other: list[object] = []

    async def another_admin_links_the_new_app(key: str, _n: int) -> None:
        if _is_connector_config(key) and not first_config:
            first_config.append(key)
            (app,) = kv.saved(APPS_PATH)
            other.append(await _create(config, graph, {**_body(LINK_ONLY, oauthConfigId=app["_id"]), "instanceName": "Other"}))

    kv.before_write = another_admin_links_the_new_app
    kv.fail_write = lambda key, _n: key in first_config
    _refused(await _create(config, graph), 500, KEPT_APP)
    assert isinstance(other[0], dict) and other[0]["success"] is True
    (linked,) = graph.connectors()
    linked_auth = kv.saved(f"/services/connectors/{linked['_key']}/config")["auth"]
    assert [app["_id"] for app in kv.saved(APPS_PATH)] == [linked_auth["oauthConfigId"]]


async def test_a_config_write_that_landed_before_its_failed_answer_is_deleted(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    landed: list[str] = []

    async def store_then_lose_the_reply(key: str, _n: int) -> None:
        if _is_connector_config(key) and not landed:
            landed.append(key)
            await InMemoryKeyValueStore.create_key(kv, key, {"auth": {}})

    kv.before_write = store_then_lose_the_reply
    kv.fail_write = _first_connector_config_write()
    _refused(await _create(config, graph), 500, KEPT_APP)
    assert kv.saved(landed[0]) is None
    assert graph.connectors() == []


async def test_a_failure_after_the_app_write_removes_the_connector_and_keeps_the_app(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    real = routes.resolve_oauth_base_url

    async def fails_once_the_app_is_saved(*args: Any) -> str:  # noqa: ANN401
        if kv.writes.get(APPS_PATH):
            raise ConnectionError("kv down")
        return await real(*args)

    with patch.object(routes, "resolve_oauth_base_url", fails_once_the_app_is_saved):
        answer = _refused(await _create(config, graph), 500,
                          "The connector could not be created. Its OAuth app was saved: pick that app when you try again.")
    assert isinstance(answer.__cause__, ConnectionError)
    assert graph.connectors() == []
    (app,) = kv.saved(APPS_PATH)
    await _retry_succeeds(config, graph, _body(LINK_ONLY, oauthConfigId=app["_id"]))


async def test_a_failure_after_the_config_save_does_not_say_the_settings_were_not_saved(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    # Only the config write's own failure is "settings could not be saved"; here the settings were saved, then taken back.
    failing = AsyncMock(side_effect=RuntimeError("action items"))
    _refused(await _create(config, graph, action_items=failing), 500, KEPT_APP_LATE)
    assert graph.connectors() == []
    assert not [key for key in kv.store if _is_connector_config(key)]


async def test_a_config_save_that_fails_leaves_an_app_it_updated_as_it_is(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    # As on main: without compare-and-set in the store, putting the app back could overwrite a concurrent edit.
    same_org = {**SHARED_APP, "_id": "app-2", "oauthInstanceName": "Other app", "config": {"clientId": "c2", "clientSecret": "s2"}}
    other_org = {**SHARED_APP, "orgId": "org-2", "config": {"clientId": "c3", "clientSecret": "s3"}}
    await kv.create_key(APPS_PATH, [other_org, same_org, SHARED_APP])
    kv.fail_write = _first_connector_config_write()
    _refused(await _create(config, graph, _body(oauthConfigId="app-1")), 500, ROLLED_BACK)
    assert graph.connectors() == []
    theirs, ours, updated = kv.saved(APPS_PATH)
    assert (theirs, ours) == (other_org, same_org)
    assert updated["_id"] == "app-1" and updated["config"]["clientSecret"] == SECRET
    assert kv.writes[APPS_PATH] == 2  # the seed and the update: the rollback writes no app


async def test_a_named_app_of_another_org_is_never_touched(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    theirs = {**SHARED_APP, "orgId": "org-2"}
    await kv.create_key(APPS_PATH, [theirs])
    kv.fail_write = _first_connector_config_write()
    _refused(await _create(config, graph, _body(oauthConfigId="app-1")), 500, KEPT_APP)  # a new app was made instead
    first, new = kv.saved(APPS_PATH)
    assert first == theirs and new["orgId"] == ORG and new["_id"] != "app-1"
    assert graph.connectors() == []


@pytest.mark.parametrize("update", ["applied-but-answered-failure", "refused"])
async def test_a_failed_update_of_a_named_app_removes_the_connector_and_writes_no_app(
    kv: KV, config: ConfigurationService, graph: Graph, update: str,
) -> None:
    await kv.create_key(APPS_PATH, [SHARED_APP])
    if update == "refused":
        kv.fail_write = lambda key, n: key == APPS_PATH and n == 2  # write 1 is the seed, write 2 the update
    else:
        kv.lost_reply = lambda key, n: key == APPS_PATH and n == 2
    _refused(await _create(config, graph, _body(oauthConfigId="app-1")), 500, "Failed to create/update OAuth configuration")
    assert kv.writes[APPS_PATH] == 2
    assert (kv.saved(APPS_PATH) == [SHARED_APP]) is (update == "refused")  # an applied update stays, as on main
    assert graph.connectors() == []


@pytest.mark.parametrize("save", ["landed-but-answered-failure", "refused"])
async def test_a_new_app_whose_save_answered_failure_may_be_saved_and_the_answer_says_to_check(
    kv: KV, config: ConfigurationService, graph: Graph, save: str,
) -> None:
    # The two look the same to the create, so it claims neither.
    if save == "refused":
        kv.fail_write = lambda key, n: key == APPS_PATH and n == 1
    else:
        kv.lost_reply = lambda key, n: key == APPS_PATH and n == 1
    answer = _refused(await _create(config, graph), 500, MAYBE_KEPT_APP)
    assert isinstance(answer.__cause__, HTTPException) and answer.__cause__.detail == "Failed to create/update OAuth configuration"
    assert graph.connectors() == []
    if save == "refused":
        assert kv.saved(APPS_PATH) is None
        await _retry_succeeds(config, graph)
    else:
        (app,) = kv.saved(APPS_PATH)
        await _retry_succeeds(config, graph, _body(LINK_ONLY, oauthConfigId=app["_id"]))


async def test_a_config_save_that_fails_never_touches_an_app_it_only_linked(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    await kv.create_key(APPS_PATH, [SHARED_APP])
    kv.fail_write = _first_connector_config_write()
    _refused(await _create(config, graph, _body(LINK_ONLY, oauthConfigId="app-1")), 500, ROLLED_BACK)
    assert kv.saved(APPS_PATH) == [SHARED_APP]
    assert kv.writes[APPS_PATH] == 1  # only the seed
    assert graph.connectors() == []


# ---- concurrency: the rollback removes this request's instance by its id, never another's

async def test_a_same_name_create_during_the_failing_one_is_refused_then_succeeds_after_it(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    kv.fail_write = _first_connector_config_write()
    during: list[object] = []

    async def other_request(key: str, _n: int) -> None:
        if _is_connector_config(key) and not during:
            during.append(None)
            during[0] = await _create(config, graph)

    kv.before_write = other_request
    _refused(await _create(config, graph), 500, KEPT_APP)
    # Refused while the first create runs: 409 for its app's name (checked first), else 400 for the instance's.
    assert isinstance(during[0], HTTPException) and during[0].status_code in (400, 409)
    assert len(graph.created) == 1 and graph.deleted == graph.created
    (app,) = kv.saved(APPS_PATH)
    await _retry_succeeds(config, graph, _body(LINK_ONLY, oauthConfigId=app["_id"]))


async def test_a_same_name_connector_that_passed_the_check_alongside_survives_the_rollback(
    kv: KV, config: ConfigurationService, graph: Graph,
) -> None:
    kv.fail_write = _first_connector_config_write()

    async def other_request_lands(key: str, _n: int) -> None:
        if _is_connector_config(key) and "other" not in graph.nodes[APPS]:
            graph.add_connector("other")

    kv.before_write = other_request_lands
    _refused(await _create(config, graph), 500, KEPT_APP)
    (mine,) = graph.created
    assert graph.deleted == [mine]
    assert [c["_key"] for c in graph.connectors()] == ["other"]
    assert graph.edges == [{"from_id": ORG, "to_id": "other"}]


# ---- a rollback that cannot finish stops, logs once and keeps the instance with today's answer

@pytest.mark.parametrize("first_failure", ["app-save", "named-app-missing"])
async def test_any_failure_whose_rollback_stops_says_the_connector_is_left(
    kv: KV, config: ConfigurationService, graph: Graph, caplog: pytest.LogCaptureFixture, first_failure: str,
) -> None:
    graph.delete_answer = {"success": False}
    if first_failure == "app-save":
        kv.fail_write = lambda key, n: key == APPS_PATH and n == 1
        body, status_before = _body(), 500
    else:
        body, status_before = _body(LINK_ONLY, oauthConfigId="no-such-app"), 404
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        answer = _refused(await _create(config, graph, body), 500, FINISH_IT)  # not the bare 500 or 404 of the failure
    assert isinstance(answer.__cause__, HTTPException) and answer.__cause__.status_code == status_before
    assert [c["name"] for c in graph.connectors()] == [NAME]
    (line,) = _rollback_lines(caplog)
    assert line.step == "instance"


async def test_a_config_key_that_cannot_be_deleted_stops_before_the_instance(
    kv: KV, config: ConfigurationService, graph: Graph, caplog: pytest.LogCaptureFixture,
) -> None:
    down: list[bool] = []

    def store_goes_down_at_the_config_write(key: str, _n: int) -> bool:
        if _is_connector_config(key):
            down.append(True)
        return bool(down)

    kv.fail_write = store_goes_down_at_the_config_write
    kv.fail_read = lambda _key, _n: bool(down)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _refused(await _create(config, graph), 500, FINISH_IT)
    assert [c["name"] for c in graph.connectors()] == [NAME]
    assert graph.deleted == []
    (line,) = _rollback_lines(caplog)
    assert (line.op, line.step, line.connector_id) == ("rollback", "config", graph.created[0])
    assert getattr(line, "error_message", None) is None
    assert SECRET not in caplog.text


async def test_a_config_key_still_there_after_its_delete_stops_before_the_instance(
    kv: KV, config: ConfigurationService, graph: Graph, caplog: pytest.LogCaptureFixture,
) -> None:
    # The config write landed but answered failure, and its delete answers "not deleted" while a read still finds it.
    landed: list[str] = []

    def lands_once(key: str, _n: int) -> bool:
        if _is_connector_config(key) and not landed:
            landed.append(key)
            return True
        return False

    kv.lost_reply = lands_once
    kv.delete_key = AsyncMock(return_value=False)  # type: ignore[method-assign]
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _refused(await _create(config, graph), 500, FINISH_IT)
    assert kv.saved(landed[0]) is not None  # no connector without it: the instance stays with its config
    assert [c["name"] for c in graph.connectors()] == [NAME] and graph.deleted == []
    assert [r.step for r in _rollback_lines(caplog)] == ["config"]


@pytest.mark.parametrize("delete_answer", [{"success": False, "error": "not found"}, ConnectionError("graph down")],
                         ids=["answers-failure", "raises"])
async def test_an_instance_the_graph_does_not_delete_stays_after_its_config_went(
    kv: KV, config: ConfigurationService, graph: Graph, caplog: pytest.LogCaptureFixture,
    delete_answer: dict[str, Any] | Exception,
) -> None:
    graph.delete_answer = delete_answer
    kv.fail_write = _first_connector_config_write()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _refused(await _create(config, graph), 500, FINISH_IT)
    assert not [key for key in kv.store if _is_connector_config(key)]
    assert [c["name"] for c in graph.connectors()] == [NAME]
    (line,) = _rollback_lines(caplog)
    assert (line.step, line.error_class, line.levelno) == ("instance", "unavailable", logging.WARNING)
    assert "graph down" not in caplog.text and "not found" not in caplog.text


async def test_an_instance_not_deleted_after_a_404_is_logged_as_the_graph_s_failure_not_the_404(
    config: ConfigurationService, graph: Graph, caplog: pytest.LogCaptureFixture,
) -> None:
    graph.delete_answer = {"success": False, "error": "not found"}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        _refused(await _create(config, graph, _body(LINK_ONLY, oauthConfigId="no-such-app")), 500, FINISH_IT)
    (line,) = _rollback_lines(caplog)
    assert (line.step, line.error_class) == ("instance", "unavailable")
    assert [c["name"] for c in graph.connectors()] == [NAME]


# ---- controls

async def test_a_create_that_saves_everything_deletes_nothing(kv: KV, config: ConfigurationService, graph: Graph) -> None:
    answer = await _create(config, graph)
    assert isinstance(answer, dict) and answer["success"] is True
    (app,) = kv.saved(APPS_PATH)
    (connector,) = graph.connectors()
    assert kv.saved(f"/services/connectors/{connector['_key']}/config")["auth"]["oauthConfigId"] == app["_id"]
    assert graph.deleted == []


async def test_a_duplicate_name_refused_before_the_instance_write_deletes_nothing(
    config: ConfigurationService, graph: Graph,
) -> None:
    graph.add_connector("existing")
    _refused(await _create(config, graph, _body(LINK_ONLY)), 400)
    assert graph.deleted == []
    assert [c["_key"] for c in graph.connectors()] == ["existing"]


# ---- the instance write itself: a node whose org edge was not written goes too

async def test_an_instance_whose_org_edge_was_not_written_is_removed(config: ConfigurationService, graph: Graph) -> None:
    graph.edge_writes_fail = True
    _refused(await _create(config, graph, _body(LINK_ONLY)), 500, "Failed to create connector instance")
    assert graph.connectors() == [] and graph.deleted == graph.created
    graph.edge_writes_fail = False
    await _retry_succeeds(config, graph, _body(LINK_ONLY))


async def test_a_value_error_after_the_node_write_removes_the_node(config: ConfigurationService, graph: Graph) -> None:
    graph.edge_writes_raise = ValueError("the edge was refused")
    _refused(await _create(config, graph, _body(LINK_ONLY)), 400)  # a ValueError still answers as before
    assert graph.connectors() == [] and graph.deleted == graph.created
    graph.edge_writes_raise = None
    await _retry_succeeds(config, graph, _body(LINK_ONLY))


async def test_an_edgeless_instance_the_graph_does_not_delete_is_logged_once(
    config: ConfigurationService, graph: Graph, caplog: pytest.LogCaptureFixture,
) -> None:
    graph.edge_writes_fail = True
    graph.delete_answer = ConnectionError("graph down")
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        answer = _refused(await _create(config, graph, _body(LINK_ONLY)), 500, FINISH_IT)
    assert isinstance(answer.__cause__, ConnectorInstanceNotDeletedError)
    (line,) = _rollback_lines(caplog)
    assert (line.step, line.connector_id) == ("instance", graph.created[0])
    assert "graph down" not in caplog.text


async def test_discard_frees_the_lane_only_after_the_graph_deleted_the_instance(graph: Graph) -> None:
    registry = _registry(graph)
    graph.add_connector("c1")
    with patch.object(registry_module, "free_lane_of_deleted_connector", AsyncMock()) as free_lane:
        graph.delete_answer = {"success": False}
        with pytest.raises(ConnectorInstanceNotDeletedError):
            await registry.discard_connector_instance("c1", ORG)
        free_lane.assert_not_awaited()
        graph.delete_answer = {"success": True}
        await registry.discard_connector_instance("c1", ORG)
        free_lane.assert_awaited_once_with(LOG, "c1")
    assert graph.connectors() == []


@pytest.mark.parametrize("delete_answer", [{"success": False}, ConnectionError("graph down")], ids=["answers-failure", "raises"])
async def test_a_failed_discard_puts_back_the_org_edge_a_store_removed_first(
    graph: Graph, delete_answer: dict[str, Any] | Exception,
) -> None:
    registry = _registry(graph)
    graph.add_connector("c1")
    graph.delete_drops_edges_first = True
    graph.delete_answer = delete_answer
    with pytest.raises((ConnectorInstanceNotDeletedError, ConnectionError)):
        await registry.discard_connector_instance("c1", ORG)
    assert [c["_key"] for c in graph.connectors()] == ["c1"]
    assert [(e["from_id"], e["to_id"]) for e in graph.edges] == [(ORG, "c1")]  # listed again, to finish or delete


async def test_a_failed_discard_asked_under_another_org_never_links_the_instance_there(graph: Graph) -> None:
    registry = _registry(graph)
    graph.add_connector("c1")
    graph.delete_drops_edges_first = True
    graph.delete_answer = {"success": False}
    with pytest.raises(ConnectorInstanceNotDeletedError):
        await registry.discard_connector_instance("c1", "org-2")
    assert graph.edges == []


async def test_a_discard_that_cannot_put_the_edge_back_still_raises_the_failed_delete(graph: Graph) -> None:
    registry = _registry(graph)
    graph.add_connector("c1")
    graph.delete_drops_edges_first = True
    graph.delete_answer = {"success": False}
    graph.batch_create_edges = AsyncMock(side_effect=ConnectionError("graph down"))  # type: ignore[method-assign]
    with pytest.raises(ConnectorInstanceNotDeletedError):
        await registry.discard_connector_instance("c1", ORG)
    assert graph.edges == []
