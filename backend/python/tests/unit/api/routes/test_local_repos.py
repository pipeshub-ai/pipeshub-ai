"""HTTP behaviour of /api/v1/local-repos: identity from the token, scopes, limits, the register/manifest/upload/commit/status flow."""

# ruff: noqa: ANN201, ANN202
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.routes import local_repos
from app.connectors.sources.local_repo import store

KEY = "repo-key-0001"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeRegistry:
    def __init__(self) -> None:
        self.instances: dict[str, dict] = {}

    async def get_connector_instance(self, connector_id, user_id, org_id, is_admin):
        inst = self.instances.get(connector_id)
        return inst if inst and inst["createdBy"] == user_id else None


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv(store.BASE_ENV, str(tmp_path))
    registry, created = FakeRegistry(), []

    async def fake_create(request: Request, graph):
        body = await request.json()
        user = request.state.user
        cid = f"c{len(created) + 1}"
        registry.instances[cid] = {"createdBy": user["userId"], "name": body["instanceName"], "status": None, "isActive": False}
        created.append(body)
        return {"connector": {"connectorId": cid}}

    stats = {"data": {"stats": {"total": 0, "indexingStatus": {}}}}
    graph = SimpleNamespace(get_connector_stats=AsyncMock(side_effect=lambda org, cid: stats))
    submitted = []
    dispatcher = SimpleNamespace(submit=AsyncMock(side_effect=lambda spec: submitted.append(spec)), is_running=AsyncMock(return_value=False))
    monkeypatch.setattr(local_repos, "create_connector_instance", fake_create)
    async def fake_toggle(connector_id, request, graph):
        registry.instances[connector_id]["isActive"] = True
    toggle = AsyncMock(side_effect=fake_toggle)
    monkeypatch.setattr(local_repos, "toggle_connector_instance", toggle)
    monkeypatch.setattr(local_repos, "update_connector_instance_name", AsyncMock())
    monkeypatch.setattr(local_repos, "delete_connector_instance", AsyncMock())
    monkeypatch.setattr(local_repos, "get_graph_provider", AsyncMock(return_value=graph))
    monkeypatch.setattr(local_repos, "get_dispatcher", lambda: dispatcher)
    built = SimpleNamespace(at=10**15)
    monkeypatch.setattr(local_repos, "_graph_status", AsyncMock(side_effect=lambda *a: ("ready", built.at)))
    app = FastAPI()
    app.state.connector_registry, app.state.graph_provider = registry, graph

    @app.middleware("http")
    async def identify(request: Request, call_next):
        who = request.headers.get("x-test-user", "u1")
        scopes = request.headers.get("x-test-scopes")
        request.state.user = {"userId": who, "orgId": "org1", "isOAuth": scopes is not None, "oauthScopes": (scopes or "").split(",")}
        return await call_next(request)

    app.include_router(local_repos.router)
    return SimpleNamespace(c=TestClient(app), built=built, toggle=toggle, registry=registry, created=created, stats=stats, submitted=submitted, graph=graph)


def put_files(world, **files: str):
    return world.c.put(f"/api/v1/local-repos/{KEY}/files", json={"files": [{"path": p, "sha256": sha(t), "content": t} for p, t in files.items()]})


def test_register_is_idempotent_and_returns_the_limits(world):
    r = world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    assert r.status_code == 201 and r.json()["created"] is True and r.json()["state"] == "empty"
    assert r.json()["limits"]["maxBatchFiles"] == 100 and r.json()["name"] == "shop"
    again = world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    assert again.status_code == 200 and again.json()["created"] is False and again.json()["connectorId"] == r.json()["connectorId"]
    assert world.toggle.await_count == 1                              # turned on once, not again by the second register
    assert world.created[0]["instanceName"] == f"shop [{KEY[:8]}]"
    assert len(world.created) == 1 and world.created[0]["scope"] == "personal" and world.created[0]["connectorType"] == "Local Repository"
    assert world.created[0]["config"]["sync"]["customValues"] == {"repo_key": KEY}


@pytest.mark.parametrize("body", [{"name": "x", "path": "/etc"}, {"name": "x", "root": "/home"}, {"rootPath": "/"}])
def test_a_body_that_names_a_server_path_is_refused(world, body):
    assert world.c.put(f"/api/v1/local-repos/{KEY}", json=body).status_code == 422
    assert world.created == []


@pytest.mark.parametrize("key", ["short", "a" * 65, "has space key", "dot.dot.dot.dot", "semi;colon-key"])
def test_a_malformed_key_is_a_400(world, key):
    assert world.c.put(f"/api/v1/local-repos/{key}", json={"name": "x"}).status_code == 400


def test_manifest_upload_commit_status_flow(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    files = {"a.py": "a = 1\n", "pkg/b.py": "b = 1\n", "node_modules/x.js": "x"}
    m = world.c.post(f"/api/v1/local-repos/{KEY}/manifest", json={"complete": True, "files": [{"path": p, "sha256": sha(t), "size": len(t)} for p, t in files.items()]}).json()
    assert sorted(m["upload"]) == ["a.py", "pkg/b.py"] and m["rejected"] == [{"path": "node_modules/x.js", "reason": "ignored"}]
    up = put_files(world, **{p: t for p, t in files.items() if p in m["upload"]}).json()
    assert sorted(up["stored"]) == ["a.py", "pkg/b.py"] and up["rejected"] == []
    st = world.c.get(f"/api/v1/local-repos/{KEY}").json()
    assert st["state"] == "empty" and st["files"] == 2
    c = world.c.post(f"/api/v1/local-repos/{KEY}/commit")
    assert c.status_code == 202 and c.json()["generation"] == 1 and c.json()["state"] == "syncing"
    assert [s.connector_id for s in world.submitted] == ["c1"]
    inst = world.registry.instances["c1"]
    commit_at = c.json()["lastCommitAt"]
    inst.update(lastSyncStartedAtTimestamp=commit_at + 1, lastSyncFinishedAtTimestamp=commit_at + 2, lastSyncOutcome="succeeded")
    world.stats["data"]["stats"] = {"total": 2, "indexingStatus": {"QUEUED": 1, "COMPLETED": 1}}
    s = world.c.get(f"/api/v1/local-repos/{KEY}").json()
    assert s["state"] == "indexing" and s["indexing"]["queued"] == 1
    assert s["indexing"] == {"total": 2, "notStarted": 0, "queued": 1, "inProgress": 0, "completed": 1, "failed": 0, "skipped": 0}
    world.stats["data"]["stats"] = {"total": 2, "indexingStatus": {"COMPLETED": 2}}
    s = world.c.get(f"/api/v1/local-repos/{KEY}").json()
    assert s["state"] == "ready" and s["syncedGeneration"] == 1 and s["codeGraph"] == "ready"
    inst["lastSyncOutcome"] = "failed"
    inst["lastSyncStartedAtTimestamp"] = commit_at + 2
    inst["lastSyncFinishedAtTimestamp"] = commit_at + 3
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "failed"


def test_a_commit_while_a_sync_ran_before_it_is_not_reported_synced_and_restarts_the_sync(world, monkeypatch):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    world.registry.instances["c1"].update(lastSyncStartedAtTimestamp=1, lastSyncFinishedAtTimestamp=2, lastSyncOutcome="succeeded")
    world.c.post(f"/api/v1/local-repos/{KEY}/commit")
    n = len(world.submitted)
    monkeypatch.setattr(local_repos, "REKICK_AFTER_MS", -1)
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "syncing"
    assert len(world.submitted) == n + 1  # the poll started the run the coalesced commit was waiting for


def test_a_commit_whose_sync_never_starts_is_reported_stuck_not_syncing_forever(world, monkeypatch):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    world.c.post(f"/api/v1/local-repos/{KEY}/commit")
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "syncing"  # a young commit is just syncing
    monkeypatch.setattr(local_repos, "STUCK_AFTER_MS", -1)
    st = world.c.get(f"/api/v1/local-repos/{KEY}").json()
    assert st["state"] == "stuck" and "did not start" in st["error"]
    # a sync that is really running is not stuck, however long it takes
    monkeypatch.setattr(local_repos, "get_dispatcher", lambda: SimpleNamespace(submit=AsyncMock(), is_running=AsyncMock(return_value=True)))
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "syncing"


def test_a_run_that_has_started_but_not_finished_is_not_this_commits_outcome(world):
    # The previous run's "succeeded" stays on the app doc until this run ends; it must not be read as this commit's.
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    commit_at = world.c.post(f"/api/v1/local-repos/{KEY}/commit").json()["lastCommitAt"]
    inst = world.registry.instances["c1"]
    inst.update(lastSyncStartedAtTimestamp=commit_at + 5, lastSyncFinishedAtTimestamp=commit_at - 100, lastSyncOutcome="succeeded")
    world.stats["data"]["stats"] = {"total": 0, "indexingStatus": {}}
    st = world.c.get(f"/api/v1/local-repos/{KEY}").json()
    assert st["state"] == "syncing" and st["syncedGeneration"] == 0
    inst.update(lastSyncFinishedAtTimestamp=commit_at + 9)  # this run ended
    world.stats["data"]["stats"] = {"total": 1, "indexingStatus": {"COMPLETED": 1}}
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "ready"


def test_never_ready_with_files_pushed_and_no_records(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    put_files(world, **{"a.py": "a = 1\n"})
    world.c.post(f"/api/v1/local-repos/{KEY}/manifest", json={"complete": True, "files": [{"path": "a.py", "sha256": sha("a = 1\n"), "size": 6}]})
    c = world.c.post(f"/api/v1/local-repos/{KEY}/commit").json()
    world.registry.instances["c1"].update(lastSyncStartedAtTimestamp=c["lastCommitAt"] + 1, lastSyncFinishedAtTimestamp=c["lastCommitAt"] + 2, lastSyncOutcome="succeeded")
    world.stats["data"]["stats"] = {"total": 0, "indexingStatus": {}}
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "indexing"


def test_one_failed_file_does_not_fail_the_repository_but_is_never_called_ready(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    c = world.c.post(f"/api/v1/local-repos/{KEY}/commit").json()
    world.registry.instances["c1"].update(lastSyncStartedAtTimestamp=c["lastCommitAt"] + 1, lastSyncFinishedAtTimestamp=c["lastCommitAt"] + 2, lastSyncOutcome="succeeded")
    world.stats["data"]["stats"] = {"total": 4, "indexingStatus": {"COMPLETED": 3, "FAILED": 1}}
    st = world.c.get(f"/api/v1/local-repos/{KEY}").json()
    # Searchable, but not "ready": a file that failed is not in the index, and the status must say so.
    assert st["state"] == "partial" and st["indexing"]["failed"] == 1
    assert "1 file" in st["note"]
    world.stats["data"]["stats"] = {"total": 4, "indexingStatus": {"COMPLETED": 4}}
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "ready"
    world.stats["data"]["stats"] = {"total": 4, "indexingStatus": {"COMPLETED": 3, "FAILED": 1}}
    world.stats["data"]["stats"] = {"total": 2, "indexingStatus": {"FAILED": 2}}
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "failed"


@pytest.mark.parametrize("name", ["<img src=x onerror=alert(1)>", "a>b", "<script>"])
def test_a_repository_name_with_markup_is_refused(world, name):
    # The sanitiser is skipped for this API (file text must arrive byte for byte), so the name, which is shown in lists, is checked here.
    assert world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": name}).status_code == 422


def test_oversized_batches_are_413(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    many = {f"f{i}.py": "x" for i in range(101)}
    assert put_files(world, **many).status_code == 413
    assert put_files(world, **{"big.py": "x" * (4 * 1024 * 1024 + 1)}).status_code == 413


def test_a_repo_is_invisible_to_another_user_and_the_same_key_is_their_own_empty_repo(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    put_files(world, **{"secret.py": "TOKEN = 1\n"})
    bob = {"x-test-user": "u2"}
    for call in (lambda: world.c.get(f"/api/v1/local-repos/{KEY}", headers=bob),
                 lambda: world.c.post(f"/api/v1/local-repos/{KEY}/commit", headers=bob),
                 lambda: world.c.delete(f"/api/v1/local-repos/{KEY}", headers=bob),
                 lambda: world.c.post(f"/api/v1/local-repos/{KEY}/manifest", headers=bob, json={"complete": True})):
        assert call().status_code == 404
    assert world.c.get("/api/v1/local-repos", headers=bob).json() == {"repos": []}
    reg = world.c.put(f"/api/v1/local-repos/{KEY}", headers=bob, json={"name": "mine"})
    assert reg.status_code == 201 and reg.json()["files"] == 0 and reg.json()["connectorId"] != world.c.get(f"/api/v1/local-repos/{KEY}").json()["connectorId"]
    assert [r["repoKey"] for r in world.c.get("/api/v1/local-repos").json()["repos"]] == [KEY]
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["files"] == 1  # alice's repo is untouched by bob's register


def test_scopes(world):
    read = {"x-test-scopes": "code:read"}
    write = {"x-test-scopes": "code:write"}
    other = {"x-test-scopes": "kb:read,connector:write"}
    assert world.c.put(f"/api/v1/local-repos/{KEY}", headers=other, json={"name": "x"}).status_code == 403
    assert world.c.put(f"/api/v1/local-repos/{KEY}", headers=read, json={"name": "x"}).status_code == 403
    assert world.c.put(f"/api/v1/local-repos/{KEY}", headers=write, json={"name": "x"}).status_code == 201
    assert world.c.get(f"/api/v1/local-repos/{KEY}", headers=read).status_code == 200
    assert world.c.get(f"/api/v1/local-repos/{KEY}", headers=other).status_code == 403
    assert world.c.post(f"/api/v1/local-repos/{KEY}/commit", headers=read).status_code == 403
    assert world.c.delete(f"/api/v1/local-repos/{KEY}", headers=read).status_code == 403


def test_delete_removes_the_connector_and_the_mirror(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    put_files(world, **{"a.py": "a\n"})
    r = world.c.delete(f"/api/v1/local-repos/{KEY}")
    assert r.status_code == 202 and r.json() == {"repoKey": KEY, "state": "deleting"}
    local_repos.delete_connector_instance.assert_awaited_once()
    assert world.c.get(f"/api/v1/local-repos/{KEY}").status_code == 404


def test_a_register_that_could_not_turn_the_connector_on_is_finished_by_the_next_one(world):
    world.toggle.side_effect = RuntimeError("connection test failed")
    with pytest.raises(RuntimeError):
        world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    assert len(world.created) == 1 and world.registry.instances["c1"]["isActive"] is False
    world.toggle.side_effect = lambda connector_id, request, graph: world.registry.instances[connector_id].update(isActive=True)
    again = world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    assert again.status_code == 200 and len(world.created) == 1 and world.registry.instances["c1"]["isActive"] is True


def test_not_ready_while_code_pushed_after_the_last_graph_build_has_no_build_yet(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    put_files(world, **{"a.py": "a = 1\n", "notes.md": "# n\n"})
    commit = world.c.post(f"/api/v1/local-repos/{KEY}/commit").json()
    world.registry.instances["c1"].update(lastSyncStartedAtTimestamp=commit["lastCommitAt"] + 1, lastSyncFinishedAtTimestamp=commit["lastCommitAt"] + 2, lastSyncOutcome="succeeded")
    world.stats["data"]["stats"] = {"total": 2, "indexingStatus": {"COMPLETED": 2}}
    world.built.at = 1                                                  # the graph was built before the push
    s = world.c.get(f"/api/v1/local-repos/{KEY}").json()
    assert s["state"] == "indexing" and s["codeGraph"] == "stale"
    world.built.at = 10**15                                             # a build finished afterwards
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "ready"


def test_a_push_of_documents_only_does_not_wait_for_a_graph_build(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    put_files(world, **{"notes.md": "# n\n"})
    commit = world.c.post(f"/api/v1/local-repos/{KEY}/commit").json()
    world.registry.instances["c1"].update(lastSyncStartedAtTimestamp=commit["lastCommitAt"] + 1, lastSyncFinishedAtTimestamp=commit["lastCommitAt"] + 2, lastSyncOutcome="succeeded")
    world.stats["data"]["stats"] = {"total": 1, "indexingStatus": {"COMPLETED": 1}}
    world.built.at = 1
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["state"] == "ready"


def test_status_counts_what_was_left_out_altogether_by_reason(world):
    world.c.put(f"/api/v1/local-repos/{KEY}", json={"name": "shop"})
    c = world.c.post(f"/api/v1/local-repos/{KEY}/commit", json={"excluded": {"ignored_folder": 3, "binary": 1}}).json()
    store.Repo("org1", "u1", KEY).save_meta(excludedFiles={"api/x_pb2.py": "generated", "gen/y.py": "generated"})
    world.registry.instances["c1"].update(lastSyncStartedAtTimestamp=c["lastCommitAt"] + 1, lastSyncFinishedAtTimestamp=c["lastCommitAt"] + 2, lastSyncOutcome="succeeded")
    world.stats["data"]["stats"] = {"total": 4, "indexingStatus": {"COMPLETED": 4}}
    assert world.c.get(f"/api/v1/local-repos/{KEY}").json()["indexing"]["excluded"] == {"ignored_folder": 3, "binary": 1, "generated": 2}
    assert world.c.post(f"/api/v1/local-repos/{KEY}/commit", json={"excluded": {"x": 1}, "path": "/etc"}).status_code == 422
