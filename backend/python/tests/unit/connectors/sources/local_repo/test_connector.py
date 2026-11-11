"""The Local Repository connector on a pushed mirror, against the in-memory graph of the Folder connector tests."""

# ruff: noqa: ANN201, ANN202
from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.sources.local_folder import scan
from app.connectors.sources.local_repo import store
from app.connectors.sources.local_repo.connector import LocalRepoConnector
from app.models.entities import CodeFileRecord
from app.models.permission import PermissionType
from tests.unit.connectors.sources.local_folder.harness import (
    FakeSyncPoint,
    FilterCollection,
    Graph,
    User,
    mod,
)

KEY = "repo-key-0001"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def push(repo: store.Repo, **files: str) -> None:
    repo.store([{"path": p, "sha256": sha(t), "content": t} for p, t in files.items()])


def connector(user_id: str = "u1", key: str = KEY, extra_sync: dict | None = None):
    graph = Graph()
    graph.org_id = "org-1"
    user = MagicMock(spec=User)
    user.id, user.org_id, user.email, user.full_name, user.is_active = user_id, "org-1", f"{user_id}@example.com", user_id, True
    graph.get_user_by_user_id = AsyncMock(return_value=user)
    graph.on_new_app_users = AsyncMock()
    cfg = MagicMock()
    cfg.get_config = AsyncMock(side_effect=lambda path: {"sync": {"customValues": {"repo_key": key}, **(extra_sync or {})}})
    c = LocalRepoConnector(MagicMock(), graph, MagicMock(), cfg, f"conn-{user_id}", "personal", user_id)
    c.record_sync_point = FakeSyncPoint()
    return c, graph


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv(store.BASE_ENV, str(tmp_path))
    repo = store.Repo("org-1", "u1", KEY)
    repo.save_meta(name="shop")
    patcher = patch.object(mod, "load_connector_filters", AsyncMock(return_value=(FilterCollection(), FilterCollection())))
    patcher.start()
    c, graph = connector()
    yield type("Env", (), {"repo": repo, "c": c, "graph": graph, "base": tmp_path})
    patcher.stop()


async def test_a_pushed_repo_becomes_one_named_repository_owned_by_the_pusher(env):
    push(env.repo, **{"src/app.py": "def run():\n    return 1\n", "README.md": "# shop\n"})
    await env.c.run_sync()
    recs = env.graph.records
    assert sorted(recs) == ["README.md", "src", "src/app.py"]
    code = recs["src/app.py"]
    assert isinstance(code, CodeFileRecord) and code.language == "python" and code.connector_name == Connectors.LOCAL_REPO
    groups = {g.external_group_id: g for g in env.graph.groups}
    repo_group = groups["folder:conn-u1:.-code-repository"]  # the suffix the code tools list repositories by
    assert repo_group.name == "shop" and code.external_record_group_id == repo_group.external_group_id
    assert {p.email: p.type for p in env.graph.perms} == {"u1@example.com": PermissionType.OWNER}
    # nothing about the server's disk leaks into what is stored
    assert str(env.base) not in " ".join(str(r.weburl) for r in recs.values()) + " ".join(str(g.web_url) for g in env.graph.groups)
    # nor the client's repository key, which a search result would otherwise carry in the file's address
    assert env.repo.key not in " ".join(str(r.weburl) for r in recs.values()) + " ".join(str(g.web_url) for g in env.graph.groups)


async def test_a_second_user_with_the_same_key_gets_an_empty_repository_and_cannot_reach_the_first(env):
    push(env.repo, **{"secret.py": "TOKEN = 1\n"})
    other, graph2 = connector("u2")  # same key, other owner: a different directory, which does not exist
    with pytest.raises(ValueError, match="nothing has been pushed"):
        await other.run_sync()
    assert graph2.records == {}
    mine = store.Repo("org-1", "u2", KEY)
    mine.save_meta(name="mine")
    push(mine, **{"own.py": "x = 1\n"})
    await other.run_sync()
    assert sorted(graph2.records) == ["own.py"]  # only its own files, never secret.py


async def test_a_root_or_key_in_the_config_cannot_point_at_another_directory(env):
    push(env.repo, **{"a.py": "a = 1\n"})
    c, graph = connector(extra_sync={"root": "/etc"})
    await c.run_sync()
    assert sorted(graph.records) == ["a.py"] and c.root == (env.base / "org-1" / "u1" / store.repo_dir("org-1", "u1", KEY).name / "tree").resolve()
    traversal, _ = connector(key="../../u2/anything-0001")
    with pytest.raises(ValueError):
        await traversal.run_sync()


async def test_unchanged_files_are_not_written_again_and_a_change_writes_one_record(env):
    push(env.repo, **{"a.py": "a = 1\n", "b.py": "b = 1\n"})
    await env.c.run_sync()
    env.graph.new_calls.clear()
    await env.c.run_sync()
    assert env.graph.new_calls == []
    push(env.repo, **{"a.py": "a = 2\n"})
    await env.c.run_sync()
    assert env.graph.new_calls == [["a.py"]]


async def test_a_deleted_file_loses_its_record_and_a_rename_leaves_nothing_under_the_old_name(env):
    push(env.repo, **{"old/name.py": "def f():\n    return 1\n", "keep.py": "k = 1\n"})
    await env.c.run_sync()
    env.repo.diff([], False, ["old/name.py"])
    push(env.repo, **{"new/name.py": "def f():\n    return 1\n"})
    await env.c.run_sync()
    assert sorted(env.graph.records) == ["keep.py", "new", "new/name.py"]
    assert "old/name.py" in env.graph.deleted and "old" in env.graph.deleted


async def test_a_gitignore_that_was_pushed_is_honoured_at_index_time(env):
    push(env.repo, **{".gitignore": "secrets/\n*.local\n", "secrets/key.py": "K = 1\n", "a.local": "x\n", "app.py": "a = 1\n"})
    await env.c.run_sync()
    assert sorted(env.graph.records) == [".gitignore", "app.py"]


def test_the_root_resolution_refuses_a_missing_mirror(tmp_path, monkeypatch):
    monkeypatch.setenv(store.BASE_ENV, str(tmp_path))
    c, _ = connector()
    with pytest.raises(scan.PathRefused):
        c._resolve_root({"customValues": {"repo_key": KEY}})


async def test_the_factory_builds_the_local_repository_connector_not_a_folder_one(env):
    from app.connectors.core.factory.connector_factory import ConnectorFactory
    from app.connectors.sources.local_folder.connector import FolderConnector

    assert ConnectorFactory.get_connector_class("Local Repository".replace(" ", "")) is LocalRepoConnector
    built = await LocalRepoConnector.create_connector(MagicMock(), MagicMock(), MagicMock(), "c9", "personal", "u1", data_entities_processor=env.graph)
    assert type(built) is LocalRepoConnector
    folder = await FolderConnector.create_connector(MagicMock(), MagicMock(), MagicMock(), "c8", "personal", "u1", data_entities_processor=env.graph)
    assert type(folder) is FolderConnector


async def test_images_and_svg_are_not_sent_to_the_indexer_because_they_need_a_vision_model(env):
    # With no model set up (a fresh standalone) an image fails its record and so used to fail the whole repository.
    push(env.repo, **{"logo.svg": "<svg xmlns='http://www.w3.org/2000/svg'><rect width='1' height='1'/></svg>\n", "a.py": "x = 1\n", "page.html": "<h1>hi</h1>\n"})
    await env.c.run_sync()
    recs = env.graph.records
    assert recs["logo.svg"].indexing_status == "AUTO_INDEX_OFF"
    assert recs["a.py"].indexing_status == "QUEUED" and recs["page.html"].indexing_status == "QUEUED"


async def test_the_sync_keeps_source_folders_named_like_build_output_and_still_drops_dependency_folders(env):
    push(env.repo, **{"internal/build/gen.go": "package build\n", "lib/env/loader.py": "x = 1\n", "node_modules/x/index.js": "x\n"})
    # the server refuses node_modules at upload; put it in the mirror as an older version of the server would have left it
    (env.repo.tree / "node_modules" / "x").mkdir(parents=True)
    (env.repo.tree / "node_modules" / "x" / "index.js").write_text("x\n")
    await env.c.run_sync()
    names = {k for k, r in env.graph.records.items() if getattr(r, "is_file", True) is not False}
    assert {"internal/build/gen.go", "lib/env/loader.py"} <= set(env.graph.records)
    assert not any(k.startswith("node_modules") for k in env.graph.records), names


async def test_tabular_data_files_are_left_unindexed_because_reading_them_needs_a_model(env):
    # data.csv ended in LLMNotConfiguredError, and a repository whose only indexable file was one ended "failed".
    push(env.repo, **{"data.csv": "a,b\n1,2\n", "a.py": "x = 1\n"})
    await env.c.run_sync()
    assert env.graph.records["data.csv"].indexing_status == "AUTO_INDEX_OFF"
    assert env.graph.records["a.py"].indexing_status == "QUEUED"


GENERATED = "# Generated by the protocol buffer compiler.  DO NOT EDIT!\nimport x\n" + "y = 1\n" * 10


async def test_names_that_only_look_generated_are_decided_by_the_file_and_the_refusals_are_counted(env):
    push(env.repo, **{"proto/payments_pb2.py": "def pay(amount):\n    return amount\n",       # hand-written, named like generated output
                      "gen/router.py": "def route(path):\n    return path\n",
                      "api/real_pb2.py": GENERATED, "generated/ids.py": GENERATED, "ok.py": "x = 1\n"})
    await env.c.run_sync()
    assert sorted(r for r in env.graph.records if r.endswith(".py")) == ["gen/router.py", "ok.py", "proto/payments_pb2.py"]
    assert env.repo.meta()["excludedFiles"] == {"api/real_pb2.py": "generated", "generated/ids.py": "generated"}


async def test_a_file_with_a_generator_header_is_counted_as_left_out_whatever_its_name(env):
    # Left to the indexer it was marked "not supported" after the fact and `status` could not say how many files that was.
    push(env.repo, **{"sdk/files.py": "# @generated by the SDK generator\nclass Files:\n    pass\n", "sdk/ok.py": "x = 1\n"})
    await env.c.run_sync()
    assert sorted(r for r in env.graph.records if r.endswith(".py")) == ["sdk/ok.py"]
    assert env.repo.meta()["excludedFiles"] == {"sdk/files.py": "generated"}


async def test_reading_a_record_during_the_sync_does_not_forget_the_files_refused_so_far(env):
    # The indexer fetches a record's content (stream_record -> _load_config) while the sync is still creating records.
    push(env.repo, **{f"sdk/g{i}.py": f"# @generated by a tool\nclass G{i}:\n    pass\n" for i in range(3)})
    finish = env.c._sync_finished

    async def after_a_read(*a, **k):
        await env.c._load_config()
        await finish(*a, **k)

    env.c._sync_finished = after_a_read
    await env.c.run_sync()
    assert sorted(env.repo.meta()["excludedFiles"]) == ["sdk/g0.py", "sdk/g1.py", "sdk/g2.py"]
