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


def push_uncommitted(repo: store.Repo, **files: str) -> None:
    repo.store([{"path": p, "sha256": sha(t), "content": t} for p, t in files.items()])


def push(repo: store.Repo, **files: str) -> None:
    push_uncommitted(repo, **files)
    repo.commit()  # the sync indexes what a commit published


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
    assert sorted(graph.records) == ["a.py"] and c.root == env.repo.current_tree() and c.root.parent == (store.repo_dir("org-1", "u1", KEY) / "generations").resolve()
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


async def test_files_that_no_commit_published_are_not_indexed_and_the_stray_ones_a_complete_manifest_removed_never_are(env):
    push_uncommitted(env.repo, **{"half.py": "h = 1\n", "keep.py": "k = 1\n"})  # uploaded, then the client stopped before committing
    await env.c.run_sync()
    assert env.graph.records == {}  # nothing is searchable until a commit publishes it
    env.repo.diff([{"path": "keep.py", "sha256": sha("k = 1\n"), "size": 6}], True, [])  # the client starts over without half.py
    env.repo.commit()
    await env.c.run_sync()
    assert sorted(r for r in env.graph.records if r.endswith(".py")) == ["keep.py"]
    assert not (env.repo.tree / "half.py").exists()


async def test_a_file_replaced_after_the_commit_is_not_indexed_and_its_old_record_stays_until_the_next_commit(env):
    push(env.repo, **{"a.py": "v = 1\n", "b.py": "w = 1\n"})
    await env.c.run_sync()
    old_rev = env.graph.records["a.py"].external_revision_id
    assert old_rev == sha("v = 1\n")
    push_uncommitted(env.repo, **{"a.py": "v = 2\n"})  # replaced on the server; the scheduled sync runs before the client's commit
    await env.c.run_sync()
    assert "a.py" in env.graph.records and env.graph.records["a.py"].external_revision_id == old_rev  # kept, not retired as gone, not re-indexed
    assert env.graph.deleted == [] and (env.repo.tree / "a.py").read_text() == "v = 1\n"  # what the indexer reads is still the indexed version
    env.repo.diff([], False, ["b.py"])  # a deletion is staged the same way
    await env.c.run_sync()
    assert "b.py" in env.graph.records and env.graph.deleted == []
    env.repo.commit()
    await env.c.run_sync()
    assert env.graph.records["a.py"].external_revision_id == sha("v = 2\n")
    assert "b.py" not in env.graph.records


async def test_a_damaged_state_file_fails_the_sync_instead_of_retiring_every_record(env):
    push(env.repo, **{"a.py": "a = 1\n"})
    await env.c.run_sync()
    assert "a.py" in env.graph.records
    (env.repo.dir / "repo.json").write_text("{not json")
    with pytest.raises(store.StoreError):
        await env.c.run_sync()
    assert "a.py" in env.graph.records and env.graph.deleted == []


async def test_a_committed_file_whose_name_holds_a_percent_sign_is_indexed(env):
    push(env.repo, **{"a%b.py": "x = 1\n", "ok.py": "y = 1\n"})
    await env.c.run_sync()
    assert sorted(r for r in env.graph.records if r.endswith(".py")) == ["a%25b.py", "ok.py"]  # the stored form of a literal "%"


async def test_classifying_a_file_reads_it_off_the_event_loop(env):
    import threading

    main = threading.get_ident()
    seen = []
    real = env.c._file_record

    def spy(*a, **k):
        seen.append(threading.get_ident())
        return real(*a, **k)

    env.c._file_record = spy
    push(env.repo, **{"a.py": "a = 1\n", "gen.py": "# @generated\nx = 1\n"})
    await env.c.run_sync()
    assert seen and all(t != main for t in seen)


async def test_a_content_read_returns_the_committed_bytes_while_a_replacement_waits_for_its_commit(env):
    push(env.repo, **{"a.py": "v = 1\n"})
    await env.c.run_sync()
    record = env.graph.records["a.py"]
    push_uncommitted(env.repo, **{"a.py": "v = 2\n"})  # staged: the upload has landed, the commit has not
    response = await env.c.stream_record(record)
    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"v = 1\n"  # what the record was indexed from, not the upload
    assert (env.repo.staging / "a.py").read_text() == "v = 2\n"


async def test_a_sync_that_runs_while_a_commit_lands_reads_one_generation_from_start_to_end(env):
    push(env.repo, **{"a.py": "v = 1\n", "b.py": "w = 1\n", "c.py": "z = 1\n"})
    env.repo.commit()
    old_name = env.repo.current_tree().name
    push_uncommitted(env.repo, **{"a.py": "v = 2\n", "d.py": "n = 1\n"})
    real_walk = scan.walk
    committed_during = []

    def walk(*a, **k):  # the commit lands after the walk has listed the first file
        for i, entry in enumerate(real_walk(*a, **k)):
            yield entry
            if i == 0:
                committed_during.append(env.repo.commit())

    with patch.object(scan, "walk", walk):
        await env.c.run_sync()
    assert committed_during and env.repo.current_tree().name != old_name  # the commit did land mid-sync
    # the sync saw the old tree whole: old content, and none of the new commit's files
    assert sorted(r for r in env.graph.records if r.endswith(".py")) == ["a.py", "b.py", "c.py"]
    assert env.graph.records["a.py"].external_revision_id == sha("v = 1\n")
    assert (env.repo.generations / old_name).exists()  # marked by the sync that read it, so the commit left it
    # the next sync reads the new generation whole
    await env.c.run_sync()
    assert env.graph.records["a.py"].external_revision_id == sha("v = 2\n") and "d.py" in env.graph.records


async def test_a_failed_commit_indexes_nothing_of_it(env, monkeypatch):
    push(env.repo, **{"a.py": "v = 1\n"})
    await env.c.run_sync()
    push_uncommitted(env.repo, **{"a.py": "v = 2\n", "new.py": "n = 1\n"})
    real_link, real_copy = store.os.link, store.shutil.copy2
    monkeypatch.setattr(store.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("disk went away")))
    monkeypatch.setattr(store.shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("disk went away")))
    with pytest.raises(OSError):
        env.repo.commit()
    monkeypatch.setattr(store.os, "link", real_link)
    monkeypatch.setattr(store.shutil, "copy2", real_copy)
    await env.c.run_sync()
    assert sorted(r for r in env.graph.records if r.endswith(".py")) == ["a.py"]
    assert env.graph.records["a.py"].external_revision_id == sha("v = 1\n")


async def test_the_event_loop_stays_responsive_while_a_commit_holds_the_repository_lock(env):
    import asyncio
    import threading
    import time

    push(env.repo, **{"a.py": "a = 1\n"})
    held, release = threading.Event(), threading.Event()

    def commit_in_progress():  # a large commit: it holds the repository lock for a while
        with env.repo._lock:
            held.set()
            release.wait(10)

    holder = threading.Thread(target=commit_in_progress)
    holder.start()
    held.wait()
    sync = asyncio.create_task(env.c.run_sync())  # pinning a generation waits for that lock
    gaps, last = [], time.monotonic()
    for _ in range(15):
        await asyncio.sleep(0.02)
        now = time.monotonic()
        gaps.append(now - last)
        last = now
    release.set()
    holder.join()
    assert max(gaps) < 0.5  # the loop kept running while the pin waited (it was blocked for the whole commit before)
    await sync
    assert "a.py" in env.graph.records


async def test_the_commit_and_the_sync_agree_on_which_files_are_code_records(env):
    from app.connectors.sources.local_repo import code_rule

    files = {"a.py": "a = 1\n", "web/dist/app.js": "var a = 1;\n", "app.min.js": "var a=1;\n", "third_party/lib.py": "def f():\n    return 1\n",
             "sdk/files.py": "# @generated by the SDK generator\nclass Files:\n    pass\n", "gen/router.py": "def route(p):\n    return p\n",
             "api/real_pb2.py": "# Generated by the protocol buffer compiler.  DO NOT EDIT!\nimport x\n", "README.md": "# x\n", "src/main.ts": "export const a = 1;\n"}
    push(env.repo, **files)
    await env.c.run_sync()
    for rel in files:
        stored = env.repo.current_tree() / rel
        expected = code_rule.is_code_record(rel, lambda stored=stored: code_rule.read_head(stored))
        indexed = rel in env.graph.records and type(env.graph.records[rel]).__name__ == "CodeFileRecord"
        assert indexed == expected, rel
