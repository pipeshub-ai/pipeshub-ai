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
from app.api.routes import code_graph as routes
from app.models.entities import RecordType

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

    async def graph_provider(self) -> Repos:
        return self._graph

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

