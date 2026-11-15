"""Manifest and diff logic of the server-side mirror of a pushed repository (no server, no database)."""

# ruff: noqa: ANN201
from __future__ import annotations

import hashlib
import os
import time

import pytest

from app.connectors.sources.local_repo import store

KEY = "repo-key-0001"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def entry(path: str, text: str) -> dict:
    return {"path": path, "sha256": sha(text), "size": len(text.encode())}


def push(repo: store.Repo, **files: str):
    """Upload files given as path=text (underscores stand for slashes only in the test's dict keys)."""
    return repo.store([{"path": p, "sha256": sha(t), "content": t} for p, t in files.items()])


@pytest.fixture
def repo(tmp_path):
    r = store.Repo("org1", "user1", KEY, base=tmp_path)
    r.save_meta(name="shop")
    return r


def test_the_first_manifest_asks_for_everything_and_a_replay_asks_for_nothing(repo):
    files = [entry("a.py", "a = 1\n"), entry("pkg/b.py", "b = 1\n")]
    first = repo.diff(files, True, [])
    assert sorted(first.upload) == ["a.py", "pkg/b.py"] and first.unchanged == 0
    stored, rejected = push(repo, **{"a.py": "a = 1\n", "pkg/b.py": "b = 1\n"})
    assert sorted(stored) == ["a.py", "pkg/b.py"] and rejected == []
    again = repo.diff(files, True, [])
    assert again.upload == [] and again.unchanged == 2 and again.deleted == []


def test_only_a_changed_file_is_asked_for_again(repo):
    push(repo, **{"a.py": "a = 1\n", "b.py": "b = 1\n"})
    d = repo.diff([entry("a.py", "a = 2\n"), entry("b.py", "b = 1\n")], True, [])
    assert d.upload == ["a.py"] and d.unchanged == 1


def test_a_complete_manifest_deletes_what_it_leaves_out_and_an_incomplete_one_does_not(repo):
    push(repo, **{"keep.py": "k\n", "gone.py": "g\n", "dir/x.py": "x\n"})
    repo.commit()
    d = repo.diff([entry("keep.py", "k\n")], False, [])
    assert d.deleted == [] and (repo.tree / "gone.py").is_file()
    d = repo.diff([entry("keep.py", "k\n")], True, [])
    assert sorted(d.deleted) == ["dir/x.py", "gone.py"]
    assert list(repo.manifest()) == ["keep.py"]
    assert (repo.tree / "gone.py").is_file()  # the committed copy stays, and stays indexed, until the commit publishes the deletion
    repo.commit()
    assert not (repo.tree / "gone.py").exists() and not (repo.tree / "dir").exists()  # the emptied directory goes too


def test_explicit_deletes_and_a_rename_leave_no_old_path(repo):
    push(repo, **{"old.py": "def f(): pass\n"})
    d = repo.diff([entry("new.py", "def f(): pass\n")], False, ["old.py"])
    assert d.deleted == ["old.py"] and d.upload == ["new.py"]
    push(repo, **{"new.py": "def f(): pass\n"})
    assert sorted(repo.manifest()) == ["new.py"] and not (repo.tree / "old.py").exists()


def test_a_file_that_goes_missing_on_disk_is_asked_for_again(repo):
    push(repo, **{"a.py": "a\n"})
    repo.commit()
    (repo.tree / "a.py").unlink()
    assert repo.diff([entry("a.py", "a\n")], True, []).upload == ["a.py"]


@pytest.mark.parametrize("path", ["../x.py", "a/../../x.py", "/etc/passwd", "a//b.py", "./a.py", ".git/config", "src/.git/x", "a\\b.py", "", "a/", "x\x00y", "tab\tname.py", "a" * 300 + ".py", "dir/" + "文" * 100 + ".py", "<b>x</b>.py", "pkg/<script>alert(1)</script>.js"])
def test_unsafe_paths_are_refused_everywhere(repo, path):
    d = repo.diff([{"path": path, "sha256": sha("x"), "size": 1}], True, [path])
    assert d.upload == [] and d.deleted == [] and d.rejected and {r["reason"] for r in d.rejected} == {"invalid_path"}
    stored, rejected = repo.store([{"path": path, "sha256": sha("x"), "content": "x"}])
    assert stored == [] and rejected[0]["reason"] == "invalid_path"
    assert not any(p for d in (repo.tree, repo.staging) for p in d.rglob("*") if p.is_file())


def test_a_path_is_stored_in_nfc_form(repo):
    nfd = "café.py"
    stored, _ = push(repo, **{nfd: "x\n"})
    repo.commit()
    assert stored == ["café.py"] and (repo.tree / "café.py").is_file()


def test_the_default_ignore_list_applies_to_files_and_to_their_directories(repo):
    d = repo.diff([entry("node_modules/x/index.js", "x"), entry("src/app.py", "x"), entry("package-lock.json", "x"), entry("img.png", "x"),
                   entry("dist/out.js", "x")], True, [])
    assert d.upload == ["src/app.py"]
    assert {r["path"]: r["reason"] for r in d.rejected} == {"node_modules/x/index.js": "ignored", "package-lock.json": "ignored", "img.png": "ignored", "dist/out.js": "ignored"}
    _, rejected = push(repo, **{"node_modules/x/index.js": "x"})
    assert rejected == [{"path": "node_modules/x/index.js", "reason": "ignored"}]


def test_size_hash_and_binary_checks_reject_without_storing(repo, monkeypatch):
    monkeypatch.setenv("PIPESHUB_LOCAL_REPO_MAX_FILE_BYTES", "10")
    r = store.Repo("org1", "user1", KEY, base=repo.dir.parent.parent.parent)
    d = r.diff([{"path": "big.py", "sha256": sha("x" * 11), "size": 11}], True, [])
    assert d.upload == [] and d.rejected == [{"path": "big.py", "reason": "too_large"}]
    stored, rejected = r.store([
        {"path": "big.py", "sha256": sha("y" * 11), "content": "y" * 11},
        {"path": "lie.py", "sha256": sha("one"), "content": "two"},
        {"path": "bin.py", "sha256": sha("a\x00b"), "content": "a\x00b"},
        {"path": "ok.py", "sha256": sha("ok"), "content": "ok"},
    ])
    assert stored == ["ok.py"]
    assert {x["path"]: x["reason"] for x in rejected} == {"big.py": "too_large", "lie.py": "hash_mismatch", "bin.py": "binary"}


def test_the_file_limit_refuses_new_paths_not_changed_ones(repo, monkeypatch):
    monkeypatch.setenv("PIPESHUB_LOCAL_REPO_MAX_FILES", "2")
    r = store.Repo("org1", "user1", KEY, base=repo.dir.parent.parent.parent)
    push(r, **{"a.py": "a", "b.py": "b"})
    d = r.diff([entry("a.py", "a2"), entry("c.py", "c")], False, [])
    assert d.upload == ["a.py"] and d.rejected == [{"path": "c.py", "reason": "too_many_files"}]
    _, rejected = push(r, **{"c.py": "c"})
    assert rejected == [{"path": "c.py", "reason": "too_many_files"}]


def test_a_file_and_a_directory_can_swap_places(repo):
    push(repo, **{"a": "file\n"})
    stored, _ = push(repo, **{"a/b.py": "x\n"})  # the file `a` makes way for the directory
    assert stored == ["a/b.py"] and list(repo.manifest()) == ["a/b.py"]
    _, rejected = push(repo, **{"a": "file again\n"})  # a directory with files in it is not overwritten
    assert rejected == [{"path": "a", "reason": "invalid_path"}] and (repo.staging / "a" / "b.py").is_file()
    repo.commit()
    assert (repo.tree / "a" / "b.py").is_file()  # the committed file `a` made way for the directory


def test_a_symlink_in_the_tree_cannot_redirect_a_write(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    repo.staging.mkdir(exist_ok=True)
    (repo.staging / "link").symlink_to(outside, target_is_directory=True)
    _, rejected = push(repo, **{"link/x.py": "x\n"})
    assert rejected == [{"path": "link/x.py", "reason": "invalid_path"}] and not (outside / "x.py").exists()


def test_a_write_is_all_or_nothing_per_file_and_leaves_no_temporary_in_the_tree(repo):
    push(repo, **{"a.py": "one\n"})
    push(repo, **{"a.py": "two\n"})
    assert (repo.staging / "a.py").read_text() == "two\n"
    assert [p.name for p in repo.staging.iterdir()] == ["a.py"]
    repo.commit()
    assert (repo.tree / "a.py").read_text() == "two\n" and [p.name for p in repo.tree.iterdir()] == ["a.py"]


def test_identity_comes_from_org_user_and_key_only(tmp_path):
    a = store.repo_dir("org1", "user1", KEY, base=tmp_path)
    assert a == store.repo_dir("org1", "user1", KEY, base=tmp_path)
    assert len({a, store.repo_dir("org1", "user2", KEY, base=tmp_path), store.repo_dir("org2", "user1", KEY, base=tmp_path),
                store.repo_dir("org1", "user1", "other-key-0002", base=tmp_path)}) == 4
    assert tmp_path in a.parents
    for bad in ("short", "../../etc/passwd-aaaa", "a/b/c/d/e/f/g/h", "x" * 65, "with space aaaa", ""):
        with pytest.raises(store.StoreError):
            store.repo_dir("org1", "user1", bad, base=tmp_path)
    for bad_id in ("..", "a/b", "", "x" * 200):
        with pytest.raises(store.StoreError):
            store.repo_dir(bad_id, "user1", KEY, base=tmp_path)
        with pytest.raises(store.StoreError):
            store.repo_dir("org1", bad_id, KEY, base=tmp_path)


def test_commit_counts_generations_and_keeps_the_name(repo):
    assert repo.commit()["generation"] == 1
    m = repo.commit()
    assert m["generation"] == 2 and m["name"] == "shop" and m["committedAt"] > 0


@pytest.mark.parametrize("path", ["internal/build/gen.go", "cmd/out/main.go", "lib/env/loader.py", "pkg/dist/bundle.py", "src/target/Main.java", "app/coverage/report.py", "mod/x.go"])
def test_source_folders_that_share_a_build_output_name_are_kept(repo, path):
    # A pushed repository is already what git lists (its .gitignore applied by the client); only the junk git would not know is left out.
    stored, rejected = repo.store([{"path": path, "sha256": sha("x = 1\n"), "content": "x = 1\n"}])
    assert stored == [path] and rejected == []


@pytest.mark.parametrize("path", ["node_modules/a/index.js", "web/node_modules/a/index.js", "__pycache__/x.py", "src/.venv/lib/x.py", "vendor/mod/x.go",
                                  "dist/app.js", "yarn.lock", "logo.png", "app.min.js"])
def test_the_junk_git_does_not_know_and_top_level_artifact_folders_are_still_left_out(repo, path):
    stored, rejected = repo.store([{"path": path, "sha256": sha("x"), "content": "x"}])
    assert stored == [] and rejected[0]["reason"] == "ignored"


def test_a_repository_cannot_hold_more_than_its_total_byte_limit_but_a_changed_file_may_replace_itself(repo, monkeypatch):
    monkeypatch.setenv("PIPESHUB_LOCAL_REPO_MAX_TOTAL_BYTES", "10")
    r = store.Repo("org1", "user1", KEY, base=repo.dir.parent.parent.parent)
    assert push(r, **{"a.py": "x" * 6})[0] == ["a.py"]
    stored, rejected = push(r, **{"b.py": "y" * 6})
    assert stored == [] and rejected == [{"path": "b.py", "reason": "too_large"}]
    assert push(r, **{"a.py": "z" * 9})[0] == ["a.py"]  # 9 bytes replacing 6 stays inside 10
    assert r.limits["maxTotalBytes"] == 10


@pytest.mark.parametrize("key", [KEY + "\n", "\n" + KEY, KEY[:7]])
def test_a_key_is_matched_whole_so_a_trailing_newline_is_no_key(key):
    with pytest.raises(store.StoreError):
        store.repo_dir("org1", "user1", key)


def test_a_hash_with_a_trailing_newline_is_not_a_hash(repo):
    d = repo.diff([{"path": "a.py", "sha256": sha("a") + "\n", "size": 1}], False, [])
    assert d.upload == [] and d.rejected == [{"path": "a.py", "reason": "invalid_path"}]


def test_nothing_reaches_the_tree_before_a_commit_and_an_interrupted_upload_leaves_nothing_in_it(repo):
    push(repo, **{"keep.py": "k = 1\n", "half1.py": "h = 1\n", "deep/dir/half3.py": "h = 3\n"})  # an upload that "crashed" before its commit
    assert not any(p.is_file() for p in repo.tree.rglob("*"))  # the sync walks the tree: it sees none of it
    # the client starts over: its manifest, complete, lists only what the repository holds now
    d = repo.diff([entry("keep.py", "k = 1\n")], True, [])
    assert sorted(d.deleted) == ["deep/dir/half3.py", "half1.py"]
    # a link and a stray the manifest never knew (written, but its row was lost) are in the tree: the commit makes the tree what the manifest lists
    (repo.tree / "link").symlink_to("/etc")
    (repo.tree / "stray.py").write_text("s = 1\n")
    repo.commit()
    assert sorted(str(p.relative_to(repo.tree)) for p in repo.tree.rglob("*")) == ["keep.py"]
    assert not any(repo.staging.rglob("*.py"))


def test_a_replacement_or_deletion_changes_the_committed_tree_only_at_the_commit(repo):
    push(repo, **{"a.py": "v = 1\n", "b.py": "w = 1\n"})
    repo.commit()
    push(repo, **{"a.py": "v = 2\n"})
    repo.diff([], False, ["b.py"])
    assert (repo.tree / "a.py").read_text() == "v = 1\n" and (repo.tree / "b.py").is_file()
    repo.commit()
    assert (repo.tree / "a.py").read_text() == "v = 2\n" and not (repo.tree / "b.py").exists()


def _fail_link_on(monkeypatch, name):  # noqa: ANN202
    """Make linking of the file called ``name`` into a new generation fail, once the others have been linked."""
    real, real_copy = os.link, store.shutil.copy2

    def link(src, dst, *a, **k):  # noqa: ANN202
        if str(dst).endswith("/" + name):
            raise OSError("disk went away")
        return real(src, dst, *a, **k)

    def copy(src, dst, *a, **k):  # noqa: ANN202 - the copy that stands in for a link must fail too
        if str(dst).endswith("/" + name):
            raise OSError("disk went away")
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(store.os, "link", link)
    monkeypatch.setattr(store.shutil, "copy2", copy)

    def restore() -> None:
        monkeypatch.setattr(store.os, "link", real)
        monkeypatch.setattr(store.shutil, "copy2", real_copy)

    return restore


def test_a_commit_that_fails_midway_leaves_the_old_generation_serving_and_can_be_repeated(repo, monkeypatch):
    push(repo, **{"a.py": "v = 1\n", "b.py": "w = 1\n"})
    repo.commit()
    old = repo.current_tree()
    push(repo, **{"a.py": "v = 2\n", "c.py": "n = 1\n", "README.md": "# x\n"})
    restore = _fail_link_on(monkeypatch, "README.md")  # a.py and c.py are already linked into the new generation when this one fails
    with pytest.raises(OSError):
        repo.commit()
    assert repo.current_tree() == old  # the old generation still serves, untouched
    assert sorted(p.name for p in old.iterdir()) == ["a.py", "b.py"] and (old / "a.py").read_text() == "v = 1\n"
    assert not any(p.name.startswith(".build") for p in repo.generations.iterdir())  # nothing of the failed build is left to be read
    assert (repo.staging / "a.py").is_file()  # the staged files wait for the repeat
    restore()
    repo.commit()
    new = repo.current_tree()
    assert new != old and sorted(p.name for p in new.iterdir()) == ["README.md", "a.py", "b.py", "c.py"] and (new / "a.py").read_text() == "v = 2\n"


def test_a_retried_commit_after_a_failed_move_still_asks_for_a_graph_build(repo, monkeypatch):
    import types

    ticks = iter(range(2_000_000_000, 3_000_000_000, 5))  # a clock that moves a whole few seconds between any two readings
    monkeypatch.setattr(store, "time", types.SimpleNamespace(time=lambda: next(ticks)))
    push(repo, **{"a.py": "v = 1\n", "README.md": "# one\n"})
    repo.commit()
    before = repo.meta()["codeChangedAt"]
    repo.diff([], False, ["a.py"])  # a deleted code file...
    push(repo, **{"README.md": "# two\n"})  # ...and a replaced document
    restore = _fail_link_on(monkeypatch, "README.md")
    with pytest.raises(OSError):
        repo.commit()
    assert repo.meta()["codeChangedAt"] == before  # nothing was published
    restore()
    meta = repo.commit()  # the repeat moves only the document, and must still know the code changed
    assert meta["codeChangedAt"] == meta["committedAt"] > before and meta["pendingCodeChange"] is False


def test_a_commit_whose_metadata_write_fails_after_the_switch_still_asks_for_a_graph_build_when_repeated(repo, monkeypatch):
    push(repo, **{"a.py": "v = 1\n"})
    real = repo._write_json
    calls = []

    def write(name, data):  # noqa: ANN202
        if name == "repo.json" and repo.current_tree().name != "g000000" and not calls:
            calls.append(1)
            raise OSError("no space")  # the generation has been switched; the metadata write is what fails
        return real(name, data)

    monkeypatch.setattr(repo, "_write_json", write)
    with pytest.raises(OSError):
        repo.commit()
    assert repo.meta()["pendingCodeChange"] == repo.current_tree().name and not repo.meta().get("codeChangedAt")  # switched, not finished
    meta = repo.commit()
    assert meta["codeChangedAt"] == meta["committedAt"] and meta["pendingCodeChange"] is False


def test_a_generation_that_a_sync_is_reading_is_kept_and_the_others_go(repo):
    push(repo, **{"a.py": "v = 1\n"})
    repo.commit()
    pinned, release = repo.pin()  # a sync starts reading this generation
    push(repo, **{"a.py": "v = 2\n"})
    repo.commit()
    push(repo, **{"a.py": "v = 3\n"})
    repo.commit()
    names = sorted(p.name for p in repo.generations.iterdir())
    assert pinned.name in names and (pinned / "a.py").read_text() == "v = 1\n"  # still readable after two newer commits
    assert names == [pinned.name, repo.current_tree().name] or len(names) == 2
    release()
    push(repo, **{"a.py": "v = 4\n"})
    repo.commit()
    assert [p.name for p in repo.generations.iterdir()] == [repo.current_tree().name]


def test_a_mark_left_by_a_sync_that_died_does_not_keep_a_generation(repo, monkeypatch):
    push(repo, **{"a.py": "v = 1\n"})
    repo.commit()
    pinned, _ = repo.pin()  # never released
    mark = next((repo.dir / "readers").iterdir())
    old = time.time() - store.READER_TTL_SECONDS - 10
    os.utime(mark, (old, old))
    push(repo, **{"a.py": "v = 2\n"})
    repo.commit()
    assert not pinned.exists() and not mark.exists()


def test_the_pointer_cannot_lead_out_of_the_generations_folder(repo, tmp_path):
    push(repo, **{"a.py": "v = 1\n"})
    repo.commit()
    for text in ("../../x", "g000001/../../x", "/etc", "", "gabc"):
        (repo.dir / "current").write_text(text)
        with pytest.raises(store.StoreError):
            repo.current_tree()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.generations / "g000099").symlink_to(outside, target_is_directory=True)  # a link planted in place of a generation
    (repo.dir / "current").write_text("g000099")
    with pytest.raises(store.StoreError):
        repo.current_tree()


def test_a_commit_that_changes_code_asks_for_a_graph_build_and_one_that_does_not_does_not(repo):
    push(repo, **{"a.py": "a\n"})
    assert "codeChangedAt" not in repo.meta()  # staged, not published
    first = repo.commit()["codeChangedAt"]
    push(repo, **{"README.md": "# x\n"})
    assert repo.commit()["codeChangedAt"] == first  # only a document changed


def test_a_user_cannot_hold_more_than_the_byte_limit_across_repositories(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPESHUB_LOCAL_REPO_MAX_USER_BYTES", "10")
    first = store.Repo("org1", "user1", KEY, base=tmp_path)
    second = store.Repo("org1", "user1", "other-key-0002", base=tmp_path)
    third = store.Repo("org1", "user2", KEY, base=tmp_path)
    for r in (first, second, third):
        r.save_meta(name="x")
    assert push(first, **{"a.py": "x" * 6})[0] == ["a.py"]
    stored, rejected = push(second, **{"b.py": "y" * 6})
    assert stored == [] and rejected == [{"path": "b.py", "reason": "user_quota"}]
    assert push(first, **{"a.py": "z" * 9})[0] == ["a.py"]  # replacing its own file stays inside the limit
    assert push(third, **{"c.py": "w" * 9})[0] == ["c.py"]  # another user has a quota of their own
    assert first.user_bytes() == 9 and third.user_bytes() == 9
    assert first.limits["maxUserBytes"] == 10 and first.limits["maxReposPerUser"] == 20


def test_the_repository_count_per_user_is_what_the_user_directory_holds(tmp_path):
    a, b = store.Repo("org1", "user1", KEY, base=tmp_path), store.Repo("org1", "user1", "other-key-0002", base=tmp_path)
    assert a.user_repos() == 0
    a.save_meta(name="a")
    b.save_meta(name="b")
    assert a.user_repos() == 2 and store.Repo("org1", "user2", KEY, base=tmp_path).user_repos() == 0


def test_a_removal_that_fails_is_logged_and_raised_not_ignored(repo, monkeypatch, caplog):
    monkeypatch.setattr(store.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    with caplog.at_level("ERROR"), pytest.raises(store.StoreError) as exc:
        repo.destroy()
    assert exc.value.reason == "remove_failed" and "could not remove" in caplog.text


def test_a_repository_that_is_already_gone_is_a_removal_that_succeeded(repo):
    repo.destroy()
    repo.destroy()
    assert not repo.dir.exists()


@pytest.mark.parametrize("path", ["a‮b.py", "a‪b.py", "pkg/⁦x⁩.py", "x‎.py", "x‏y.py", "z​w.py", "﻿bom.py"])
def test_names_with_bidi_and_other_format_characters_are_refused(repo, path):
    d = repo.diff([{"path": path, "sha256": sha("x"), "size": 1}], False, [])
    assert d.upload == [] and d.rejected == [{"path": path, "reason": "invalid_path"}]
    assert repo.store([{"path": path, "sha256": sha("x"), "content": "x"}])[1][0]["reason"] == "invalid_path"


def test_concurrent_uploads_into_two_repositories_cannot_both_spend_the_last_of_the_user_quota(tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("PIPESHUB_LOCAL_REPO_MAX_USER_BYTES", "10")
    first = store.Repo("org1", "user1", KEY, base=tmp_path)
    second = store.Repo("org1", "user1", "other-key-0002", base=tmp_path)
    for r in (first, second):
        r.save_meta(name="x")
    real_write = store.Repo._write_file

    def slow_write(self, *a, **k):  # noqa: ANN202 - the quota was read before this write lands: without the user lock both uploads pass it
        time.sleep(0.2)
        return real_write(self, *a, **k)

    monkeypatch.setattr(store.Repo, "_write_file", slow_write)
    go = threading.Barrier(2)

    def up(repo, path):  # noqa: ANN202
        go.wait()
        return push(repo, **{path: "x" * 6})

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda a: up(*a), [(first, "a.py"), (second, "b.py")]))
    assert sorted(len(stored) for stored, _ in results) == [0, 1]
    assert first.user_bytes() == 6


@pytest.mark.parametrize("name", ["files.json", "repo.json"])
def test_an_unreadable_state_file_is_an_error_not_an_empty_repository(repo, name):
    push(repo, **{"a.py": "a\n", "b.py": "b\n"})
    repo.commit()
    (repo.dir / name).write_text("{not json")
    with pytest.raises(store.StoreError) as exc:
        {"files.json": repo.manifest, "repo.json": repo.meta}[name]()
    assert exc.value.reason == "state_unreadable"
    # nothing is replaced or swept on top of the damaged file
    if name == "files.json":
        with pytest.raises(store.StoreError):
            push(repo, **{"c.py": "c\n"})
        with pytest.raises(store.StoreError):
            repo.commit()
        assert (repo.dir / name).read_text() == "{not json"
        assert sorted(p.name for p in repo.tree.iterdir()) == ["a.py", "b.py"]


def test_a_state_file_that_does_not_exist_yet_is_an_empty_one(tmp_path):
    fresh = store.Repo("org1", "user1", KEY, base=tmp_path)
    assert fresh.manifest() == {} and fresh.meta() == {}


def test_a_delayed_delete_leaves_a_repository_that_was_registered_again_under_the_same_key(repo):
    repo.save_meta(connectorId="new-connector")  # the key now belongs to a connector made after the old one was deleted
    assert repo.destroy("old-connector") is False and repo.exists()
    assert repo.destroy("new-connector") is True and not repo.dir.exists()


def _clock(monkeypatch) -> None:
    import types

    ticks = iter(range(2_000_000_000, 3_000_000_000, 5))
    monkeypatch.setattr(store, "time", types.SimpleNamespace(time=lambda: next(ticks)))


def test_staging_a_code_file_and_dropping_it_before_the_commit_changes_nothing(repo, monkeypatch):
    _clock(monkeypatch)
    push(repo, **{"a.py": "a = 1\n"})
    first = repo.commit()["codeChangedAt"]
    push(repo, **{"b.py": "b = 1\n"})  # staged...
    repo.diff([entry("a.py", "a = 1\n")], True, [])  # ...and the client's next manifest does not list it
    assert repo.commit()["codeChangedAt"] == first  # the published code is what it was: no build is owed, so none is waited for
    assert sorted(p.name for p in repo.current_tree().iterdir()) == ["a.py"]


def test_sending_the_bytes_that_are_already_published_changes_nothing_but_other_bytes_do(repo, monkeypatch):
    _clock(monkeypatch)
    push(repo, **{"a.py": "a = 1\n"})
    first = repo.commit()["codeChangedAt"]
    (repo.staging).mkdir(exist_ok=True)
    (repo.staging / "a.py").write_text("a = 1\n")  # staged again with identical bytes (a client that re-sent the file)
    assert repo.commit()["codeChangedAt"] == first
    push(repo, **{"a.py": "a = 2\n"})
    assert repo.commit()["codeChangedAt"] > first


def test_a_code_file_removed_by_the_manifest_changes_the_code_even_with_no_other_note_of_it(repo, monkeypatch):
    _clock(monkeypatch)
    push(repo, **{"a.py": "a = 1\n", "README.md": "# x\n"})
    first = repo.commit()["codeChangedAt"]
    repo.diff([entry("README.md", "# x\n")], True, [])  # a.py is gone from the manifest; nothing else was written about it
    assert repo.commit()["codeChangedAt"] > first
    assert not (repo.current_tree() / "a.py").exists()


def test_a_commit_that_changes_only_documents_leaves_code_changed_at_alone_and_a_stale_pending_mark_is_ignored(repo, monkeypatch):
    _clock(monkeypatch)
    push(repo, **{"a.py": "a = 1\n"})
    first = repo.commit()["codeChangedAt"]
    repo.save_meta(pendingCodeChange="g000099")  # left by an attempt whose generation never became current
    push(repo, **{"README.md": "# x\n"})
    meta = repo.commit()
    assert meta["codeChangedAt"] == first and meta["pendingCodeChange"] is False


@pytest.mark.parametrize("rel, text", [
    ("web/dist/app.js", "var a = 1;\n"),
    ("app.min.js", "var a=1;\n"),
    ("third_party/lib.py", "def f():\n    return 1\n"),
    ("sdk/files.py", "# @generated by the SDK generator\nclass Files:\n    pass\n"),
])
def test_a_change_to_a_file_the_sync_leaves_out_does_not_move_code_changed_at(repo, monkeypatch, rel, text):
    _clock(monkeypatch)
    push(repo, **{"a.py": "a = 1\n"})
    first = repo.commit()["codeChangedAt"]
    push(repo, **{rel: text})
    assert repo.commit()["codeChangedAt"] == first  # no code record changes, so no graph build would be requested
    push(repo, **{rel: text + "# more\n"})  # changed bytes of such a file: still nothing
    assert repo.commit()["codeChangedAt"] == first
    repo.diff([entry("a.py", "a = 1\n")], True, [])  # and removing it
    assert repo.commit()["codeChangedAt"] == first


def test_a_real_code_file_moves_code_changed_at_and_so_does_one_that_stops_or_starts_being_code(repo, monkeypatch):
    _clock(monkeypatch)
    push(repo, **{"a.py": "a = 1\n"})
    first = repo.commit()["codeChangedAt"]
    push(repo, **{"b.py": "b = 1\n"})
    second = repo.commit()["codeChangedAt"]
    assert second > first
    push(repo, **{"b.py": "# @generated by a tool\nb = 1\n"})  # was a code record, now left out: its record goes
    third = repo.commit()["codeChangedAt"]
    assert third > second
    push(repo, **{"b.py": "b = 2\n"})  # and back
    assert repo.commit()["codeChangedAt"] > third
