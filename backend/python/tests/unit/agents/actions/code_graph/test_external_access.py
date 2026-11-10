"""The code graph for agents outside PipesHub (X-1): repos named, not ids.

An MCP client knows `acme/shop`, not a connector id, and must never learn the
name of a repo it cannot read. The HTTP routes run the same tools as the
in-product agent, so they inherit the per-record ACL.
"""
import json
import logging

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from app.agents.actions.code_graph.repos import (
    AmbiguousRepoNameError,
    RepoNotFoundError,
    list_code_repos,
    repo_for_record,
    resolve_code_repo,
)
from app.api import connection_limit
from app.api.routes import code_graph as routes
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.entities import RecordType
from app.modules.retrieval.selection_scope import SelectionTooLargeError
from app.services.graph_db.interface.graph_db_provider import AccessCheck

from .conftest import CONN, ORG, USER
from .test_repo_identity import BLOBS, MAIN, TwoRepos

MIRROR = "conn-mirror"


class _Rec:
    def __init__(self, rid: str, record_type: RecordType = RecordType.CODE_FILE) -> None:
        self.id = rid
        self.record_type = record_type


class Repos(TwoRepos):
    """TwoRepos as the connectors write it, plus a Jira connector and,
    optionally, a second connector syncing acme/shop again."""

    def __init__(self, deny=(), mirror=False) -> None:
        super().__init__(deny=deny)
        for group, ext in (("g-shop-code", "101"), ("g-admin-code", "102")):
            g = next(g for g in self.groups if g["_key"] == group)
            g["externalGroupId"] = f"{ext}-code-repository"
        for rid in ("rec-shop", "rec-admin"):
            self.records[rid]["recordType"] = RecordType.CODE_FILE.value
        self.instances = [
            {"_key": CONN, "name": "GitHub", "type": "GITHUB TEAMS"},
            {"_key": "conn-jira", "name": "Jira", "type": "JIRA"},
        ]
        self.groups.append({"_key": "g-jira", "orgId": ORG, "connectorId": "conn-jira",
                            "externalGroupId": "PROJ-code-repository", "groupName": "PROJ"})
        if mirror:
            self.instances.append({"_key": MIRROR, "name": "GitLab", "type": "GitLab"})
            self.groups += [
                {"_key": "m-shop-code", "orgId": ORG, "connectorId": MIRROR,
                 "externalGroupId": "7-code-repository", "groupName": "Code repository",
                 "parentExternalGroupId": "7"},
                {"_key": "m-shop", "orgId": ORG, "connectorId": MIRROR,
                 "externalGroupId": "7", "groupName": "acme/shop"},
            ]
            self.records["rec-mirror"] = {"_key": "rec-mirror", "orgId": ORG, "connectorId": MIRROR,
                                          "recordGroupId": "m-shop-code",
                                          "recordType": RecordType.CODE_FILE.value}

    async def get_user_connector_instances(self, collection, user_id, org_id,
                                           team_scope, personal_scope) -> list[dict]:
        return self.instances if user_id == USER else []

    async def get_records_by_record_group(self, record_group_id, connector_id, org_id,
                                          depth, user_key=None, limit=None, **_) -> list[_Rec]:
        return [_Rec(rid) for rid, r in self.records.items()
                if r.get("recordGroupId") == record_group_id][:limit]


async def test_lists_only_the_code_repos_the_caller_can_read() -> None:
    repos = await list_code_repos(Repos(deny={"rec-admin"}), ORG, USER)
    assert [(r.name, r.connector_id, r.group_id) for r in repos] == [
        ("acme/shop", CONN, "g-shop-code"),
    ]


async def test_another_user_sees_no_repos() -> None:
    assert await list_code_repos(Repos(), ORG, "someone-else") == []


@pytest.mark.parametrize("name", ["acme/shop", "shop", "SHOP", "/acme/shop/", "GitHub:acme/shop"])
async def test_a_repo_is_named_the_way_a_person_would(name: str) -> None:
    repo = await resolve_code_repo(Repos(), ORG, USER, name)
    assert (repo.name, repo.group_id) == ("acme/shop", "g-shop-code")


async def test_resolving_a_name_checks_access_on_that_repo_only() -> None:
    graph = Repos()
    checked: list[str] = []
    sample = graph.get_records_by_record_group

    async def counting(record_group_id, *a, **k) -> list[_Rec]:
        checked.append(record_group_id)
        return await sample(record_group_id, *a, **k)

    graph.get_records_by_record_group = counting
    await resolve_code_repo(graph, ORG, USER, "acme/shop")
    assert checked == ["g-shop-code"]


async def test_an_unreadable_repo_reads_as_absent() -> None:
    with pytest.raises(RepoNotFoundError):
        await resolve_code_repo(Repos(deny={"rec-admin"}), ORG, USER, "acme/admin")


async def test_one_repo_synced_twice_is_told_apart_by_connector() -> None:
    graph = Repos(mirror=True)
    with pytest.raises(AmbiguousRepoNameError) as exc:
        await resolve_code_repo(graph, ORG, USER, "acme/shop")
    assert exc.value.repos == ["GitHub:acme/shop", "GitLab:acme/shop"]
    picked = await resolve_code_repo(graph, ORG, USER, "GitLab:acme/shop")
    assert picked.connector_id == MIRROR


async def test_a_search_hit_names_its_repo() -> None:
    repo = await repo_for_record(Repos(), ORG, USER, "rec-admin")
    assert repo.name == "acme/admin"
    with pytest.raises(RepoNotFoundError):
        await repo_for_record(Repos(deny={"rec-admin"}), ORG, USER, "rec-admin")


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


class _Container:
    def __init__(self, graph) -> None:
        self._graph = graph
        self.retrievals = 0

    async def graph_provider(self) -> Repos:
        return self._graph

    async def retrieval_service(self) -> None:
        self.retrievals += 1

    def logger(self) -> logging.Logger:
        return logging.getLogger("test")

    def config_service(self) -> None:
        return None


def _client(graph, user=None) -> TestClient:
    app = FastAPI()
    app.container = _Container(graph)

    @app.middleware("http")
    async def _auth(request: Request, call_next) -> Response:
        request.state.user = user or {"orgId": ORG, "userId": USER}
        return await call_next(request)

    app.include_router(routes.router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def blobs(monkeypatch) -> None:
    monkeypatch.setattr(routes, "BlobStorage", lambda **_: BLOBS)


async def test_read_names_the_repo_and_reads_only_its_file(blobs) -> None:
    res = _client(Repos()).post("/api/v1/code-graph/read",
                                json={"repo": "acme/admin", "file_path": MAIN})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["repo"] == "acme/admin"
    assert "admin" in json.dumps(body) and "'shop'" not in json.dumps(body)


async def test_outline_and_entry_points_routes_answer_for_one_repo(blobs) -> None:
    """The A-9 tools reach MCP through these routes, scoped like the rest."""
    client = _client(Repos())
    outline = client.post("/api/v1/code-graph/outline",
                          json={"repo": "acme/admin", "file_path": MAIN})
    assert outline.status_code == 200, outline.text
    assert [s["qualified_name"] for s in outline.json()["symbols"]] == ["function:create_app"]
    entries = client.post("/api/v1/code-graph/entry-points", json={"repo": "acme/admin"})
    assert entries.status_code == 200, entries.text
    assert "entry_points" in entries.json()
    bad = client.post("/api/v1/code-graph/search",
                      json={"repo": "acme/admin", "pattern": "x", "mode": "grep"})
    assert bad.status_code == 422


async def test_the_search_route_takes_the_external_mode_and_a_name(blobs) -> None:
    res = _client(Repos()).post("/api/v1/code-graph/search", json={
        "repo": "acme/admin", "pattern": "requests", "mode": "external", "name": "get"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "external" and body["module"] == "requests" and body["name"] == "get"
    assert body["matches"] == [] and "hint" in body


async def test_only_a_path_free_text_search_builds_the_retrieval_service(blobs) -> None:
    client = _client(Repos())
    for body in ({"pattern": "create_app"}, {"pattern": "x", "mode": "text", "path": "app/"},
                 {"pattern": "requests", "mode": "external"}):
        client.post("/api/v1/code-graph/search", json={"repo": "acme/admin", **body})
    assert client.app.container.retrievals == 0
    res = client.post("/api/v1/code-graph/search", json={"repo": "acme/admin", "pattern": "x", "mode": "text"})
    assert client.app.container.retrievals == 1
    assert "path" in res.json()["error"]


async def test_impact_route_takes_edge_types_and_names_a_bad_one(blobs, monkeypatch) -> None:
    passed: list[object] = []
    impact = routes.CodeGraph.impact

    async def spy(self, **kwargs):  # noqa: ANN202
        passed.append(kwargs.get("edge_types"))
        return await impact(self, **kwargs)

    monkeypatch.setattr(routes.CodeGraph, "impact", spy)
    client = _client(Repos())
    target = {"repo": "acme/admin", "file_path": MAIN, "qualified_name": "function:create_app"}
    calls = client.post("/api/v1/code-graph/impact", json={**target, "edge_types": ["CALLS"]})
    assert calls.status_code == 200, calls.text
    assert "error" not in calls.json()
    assert passed == [["CALLS"]]  # the route hands the filter on; the tool's tests cover applying it
    bad = client.post("/api/v1/code-graph/impact", json={**target, "edge_types": ["CALLZ"]})
    assert "unknown edge_types ['CALLZ']" in bad.json()["error"]


async def test_impact_of_diff_route_answers_for_one_repo_and_refuses_an_empty_patch() -> None:
    client = _client(Repos())
    patch = f"--- a/{MAIN}\n+++ b/{MAIN}\n@@ -2 +2 @@\n-a\n+b\n"
    res = client.post("/api/v1/code-graph/impact-of-diff", json={"repo": "acme/admin", "patch": patch})
    assert res.status_code == 200, res.text
    assert {c["file_path"] for c in res.json()["changed"]} == {MAIN}
    assert client.post("/api/v1/code-graph/impact-of-diff",
                       json={"repo": "acme/admin", "patch": ""}).status_code == 422
    assert client.post("/api/v1/code-graph/impact-of-diff",
                       json={"repo": "acme/admin", "patch": "x" * (routes.MAX_PATCH_CHARS + 1)}).status_code == 422


async def test_history_route_answers_for_one_repo_and_bounds_its_limit() -> None:
    client = _client(Repos())
    res = client.post("/api/v1/code-graph/history", json={"repo": "acme/admin", "file_path": MAIN})
    assert res.status_code == 200, res.text
    assert res.json()["pull_requests"] == [] and res.json()["file_path"] == MAIN
    assert res.json()["untrusted"], "PR and ticket titles are written by whoever opens them"
    assert client.post("/api/v1/code-graph/history",
                       json={"repo": "acme/admin", "file_path": MAIN, "limit": 101}).status_code == 422


async def test_read_by_search_hit_needs_no_repo(blobs) -> None:
    res = _client(Repos()).post("/api/v1/code-graph/read", json={"record_id": "rec-shop"})
    assert res.status_code == 200, res.text
    assert res.json()["repo"] == "acme/shop"


@pytest.mark.parametrize("direction", ["inbound", "outbound", "any"])
async def test_every_direction_the_tool_takes_is_accepted(direction: str) -> None:
    res = _client(Repos()).post("/api/v1/code-graph/neighbours", json={
        "repo": "acme/shop", "file_path": MAIN, "direction": direction})
    assert res.status_code == 200, res.text
    assert "error" not in res.json()


async def test_an_unreadable_repo_is_a_404_that_names_nothing(blobs) -> None:
    res = _client(Repos(deny={"rec-admin"})).post(
        "/api/v1/code-graph/read", json={"repo": "acme/admin", "file_path": MAIN})
    assert res.status_code == 404
    assert res.json()["error"].startswith("No indexed repository you can read")


async def test_an_ambiguous_name_is_a_409_listing_the_choices(blobs) -> None:
    res = _client(Repos(mirror=True)).post(
        "/api/v1/code-graph/neighbours", json={"repo": "acme/shop", "file_path": MAIN})
    assert res.status_code == 409
    assert res.json()["repos"] == ["GitHub:acme/shop", "GitLab:acme/shop"]


async def test_repos_route_lists_readable_repos() -> None:
    res = _client(Repos(deny={"rec-shop"})).get("/api/v1/code-graph/repos")
    assert res.json() == {"repos": [
        {"repo": "acme/admin", "source": "GitHub", "connector_id": CONN, "graph_status": "never_built"},
    ]}


async def test_repos_route_says_the_commit_each_repo_was_synced_at() -> None:
    """Stamped by the git connectors on the code group; a repo not yet stamped says nothing."""
    graph = Repos()
    next(g for g in graph.groups if g["_key"] == "g-shop-code")["lastSyncedCommit"] = "9" * 40
    by_repo = {r["repo"]: r for r in _client(graph).get("/api/v1/code-graph/repos").json()["repos"]}
    assert by_repo["acme/shop"]["indexed_commit"] == "9" * 40
    assert "indexed_commit" not in by_repo["acme/admin"]



@pytest.mark.parametrize(("route", "body"), [
    ("path", {"file_path_a": "no/such/a.py", "qualified_name_a": "function:a",
              "file_path_b": "no/such/b.py", "qualified_name_b": "function:b"}),
    ("search", {"pattern": "no_such_name_anywhere"}),
])
async def test_a_result_naming_no_file_says_the_named_repos_commit(route, body, blobs) -> None:
    """Nothing was found, so no file or block says a commit; the one repo the call named still does."""
    graph = Repos()
    next(g for g in graph.groups if g["_key"] == "g-shop-code")["lastSyncedCommit"] = "9" * 40
    client = _client(graph)
    shop = client.post(f"/api/v1/code-graph/{route}", json={"repo": "acme/shop", **body})
    assert shop.status_code == 200, shop.text
    assert shop.json()["indexed_commit"] == "9" * 40
    admin = client.post(f"/api/v1/code-graph/{route}", json={"repo": "acme/admin", **body})
    assert admin.status_code == 200 and "indexed_commit" not in admin.json()  # never stamped

async def test_repos_route_reports_each_repos_graph_status() -> None:
    """Built, pending or failed is visible per repo (O-3)."""
    class Built(Repos):
        async def get_nodes_by_filters(self, collection, filters, return_fields=None,
                                       transaction=None, raise_on_error=False) -> list[dict]:
            if collection == "syncPoints":
                failed = filters.get("syncPointKey", "").startswith("g-admin-code")
                return [{"lastEdgeBuildAt": 1700, "edgeBuildStatus":
                         "failed" if failed else "ready",
                         "edgeBuildLastError": "boom" if failed else None}]
            return await super().get_nodes_by_filters(collection, filters, return_fields,
                                                      transaction, raise_on_error)

    res = _client(Built()).get("/api/v1/code-graph/repos")
    by_repo = {r["repo"]: r for r in res.json()["repos"]}
    assert by_repo["acme/shop"]["graph_status"] == "ready"
    assert by_repo["acme/shop"]["last_built_at"] == 1700
    assert by_repo["acme/admin"]["graph_status"] == "failed"
    assert by_repo["acme/admin"]["last_error"] == "boom"


@pytest.mark.parametrize("scopes", [["semantic:write"], ["connector:read", "semantic:read"]])
async def test_an_oauth_token_without_code_read_is_refused(scopes: list[str]) -> None:
    """Tokens minted before code:read existed lose access until re-issued."""
    user = {"orgId": ORG, "userId": USER, "isOAuth": True, "oauthScopes": scopes,
            "token_type": "regular"}
    res = _client(Repos(), user=user).get("/api/v1/code-graph/repos")
    assert res.status_code == 403
    assert "code:read" in res.json()["detail"]


async def test_an_oauth_token_with_code_read_is_served() -> None:
    user = {"orgId": ORG, "userId": USER, "isOAuth": True, "oauthScopes": ["code:read"],
            "token_type": "regular"}
    res = _client(Repos(), user=user).get("/api/v1/code-graph/repos")
    assert res.status_code == 200


async def test_a_storage_failure_is_explicit() -> None:
    class Broken(Repos):
        async def get_user_connector_instances(self, *a, **k) -> list[dict]:
            raise RuntimeError("down")

    res = _client(Broken()).get("/api/v1/code-graph/repos")
    assert res.status_code == 503
    assert res.json() == {"error": "graph unavailable"}


async def test_a_failing_tool_is_a_503_that_names_nothing(monkeypatch) -> None:
    def broken(**_: object) -> None:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(routes, "BlobStorage", broken)
    res = _client(Repos()).post("/api/v1/code-graph/read", json={"repo": "acme/shop", "file_path": MAIN})
    assert res.status_code == 503
    assert res.json() == {"error": "graph unavailable"}


async def test_a_missing_repo_name_says_how_to_find_one(blobs) -> None:
    res = _client(Repos()).post("/api/v1/code-graph/read", json={"file_path": MAIN})
    assert res.status_code == 404
    assert res.json()["error"].startswith("Give `repo`")


async def test_an_unreadable_record_is_a_404_that_names_it(blobs) -> None:
    res = _client(Repos(deny={"rec-shop"})).post("/api/v1/code-graph/read", json={"record_id": "rec-shop"})
    assert res.status_code == 404
    assert res.json()["error"] == "record_id 'rec-shop' is not an indexed code file you can read."


LIMITED = {"orgId": ORG, "userId": USER,
           "knowledgeLimit": {"apps": [], "recordGroups": ["g-shop-code"], "records": []}}


def _limit_admits(monkeypatch, *inside: str) -> None:
    """The graph's answer for a connected app's limit: only ``inside`` is in it."""
    async def outside(graph, user_id, org_id, ids, limit) -> set[str]:
        return set() if limit is None else {i for i in ids if i not in inside}

    async def groups_outside(graph, user_id, org_id, groups, limit) -> set[str]:
        return set() if limit is None else {i for i in groups if i not in inside}

    monkeypatch.setattr(connection_limit, "outside_connection", outside)
    monkeypatch.setattr(connection_limit, "groups_outside_connection", groups_outside)


async def test_a_connected_apps_limit_hides_every_repo_outside_it(monkeypatch, blobs) -> None:
    """A PAT or OAuth app limited to some sources reads no repo past them (H3)."""
    _limit_admits(monkeypatch, "g-shop-code")
    client = _client(Repos(), user=LIMITED)
    assert [r["repo"] for r in client.get("/api/v1/code-graph/repos").json()["repos"]] == ["acme/shop"]
    by_name = client.post("/api/v1/code-graph/read", json={"repo": "acme/admin", "file_path": MAIN})
    assert by_name.status_code == 404
    assert by_name.json()["error"].startswith("No indexed repository you can read")
    by_record = client.post("/api/v1/code-graph/read", json={"record_id": "rec-admin"})
    assert by_record.status_code == 404
    assert by_record.json()["error"] == "record_id 'rec-admin' is not an indexed code file you can read."
    # A tool reads the record's own repo, whatever `repo` names.
    mixed = client.post("/api/v1/code-graph/read", json={"repo": "acme/shop", "record_id": "rec-admin"})
    assert mixed.status_code == 404
    assert mixed.json()["error"] == "record_id 'rec-admin' is not an indexed code file you can read."
    inside = client.post("/api/v1/code-graph/read", json={"repo": "acme/shop", "file_path": MAIN})
    assert inside.status_code == 200, inside.text


async def test_a_limit_too_large_to_check_is_a_422_not_an_outage(monkeypatch) -> None:
    async def too_large(*_: object) -> set[str]:
        raise SelectionTooLargeError

    monkeypatch.setattr(connection_limit, "outside_connection", too_large)
    monkeypatch.setattr(connection_limit, "groups_outside_connection", too_large)
    client = _client(Repos(), user=LIMITED)
    assert client.get("/api/v1/code-graph/repos").status_code == 422
    assert client.post("/api/v1/code-graph/read", json={"repo": "acme/shop", "file_path": MAIN}).status_code == 422


def test_a_large_result_is_cut_to_the_budget_and_says_so() -> None:
    rows = [{"file_path": f"src/f{i}.py", "qualifiedName": f"function:f{i}"} for i in range(5000)]
    out = routes.fit_to_budget({"neighbors": rows, "truncated": False}, max_chars=4000)
    assert len(json.dumps(out)) <= 4000
    assert out["truncated"] is True
    assert out["omitted_for_size"]["neighbors"] == 5000 - len(out["neighbors"])
    code = routes.fit_to_budget({"code": "x" * 10_000}, max_chars=2000)
    assert len(json.dumps(code)) <= 2000 and code["truncated"] is True


def test_a_small_result_is_untouched() -> None:
    small = {"neighbors": [{"a": 1}], "truncated": False}
    assert routes.fit_to_budget(small) is small



WHOLE_CONNECTOR = {"orgId": ORG, "userId": USER,
                   "knowledgeLimit": {"apps": [CONN], "recordGroups": [], "records": []}}


class RecordGrantsOnly(Repos):
    """A user who may read the repos' files but holds no permission on the record
    group nodes, on a store that can go down (a Neo4j or ArangoDB read that fails)."""

    down = False

    async def get_user_by_user_id(self, user_id, *, raise_on_error=False) -> dict | None:
        if self.down:
            if raise_on_error:
                raise ConnectionError("store down")
            return None
        return {"_key": "ukey", "userId": user_id}

    async def check_access(self, user_key, org_id, *, node_ids=(), scopes=(), **_) -> AccessCheck:
        if self.down:
            raise PermissionVerificationUnavailableError("store down")
        return AccessCheck()  # no group node is readable


async def test_a_repo_read_through_its_files_is_inside_a_limit_that_names_its_connector(blobs) -> None:
    client = _client(RecordGrantsOnly(), user=WHOLE_CONNECTOR)
    assert [r["repo"] for r in client.get("/api/v1/code-graph/repos").json()["repos"]] == ["acme/admin", "acme/shop"]
    assert client.post("/api/v1/code-graph/read", json={"repo": "acme/shop", "file_path": MAIN}).status_code == 200


async def test_a_limit_naming_another_connector_still_hides_the_repos(blobs) -> None:
    other = {**WHOLE_CONNECTOR, "knowledgeLimit": {"apps": ["conn-other"], "recordGroups": [], "records": []}}
    assert _client(RecordGrantsOnly(), user=other).get("/api/v1/code-graph/repos").json()["repos"] == []


async def test_a_store_outage_is_a_503_not_a_hidden_repo_and_the_repos_return_with_it(blobs) -> None:
    graph = RecordGrantsOnly()
    client = _client(graph, user=WHOLE_CONNECTOR)
    graph.down = True
    assert client.get("/api/v1/code-graph/repos").status_code == 503
    assert client.post("/api/v1/code-graph/read", json={"repo": "acme/shop", "file_path": MAIN}).status_code == 503
    graph.down = False
    assert len(client.get("/api/v1/code-graph/repos").json()["repos"]) == 2


async def test_a_repo_with_no_readable_file_stays_out_though_the_limit_names_its_connector(blobs) -> None:
    """Readable repos are found first, then the limit is applied: a limit never makes a repo readable."""
    client = _client(RecordGrantsOnly(deny={"rec-shop"}), user=WHOLE_CONNECTOR)
    assert [r["repo"] for r in client.get("/api/v1/code-graph/repos").json()["repos"]] == ["acme/admin"]
