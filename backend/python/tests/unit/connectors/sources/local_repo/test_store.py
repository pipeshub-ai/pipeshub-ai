"""Manifest and diff logic of the server-side mirror of a pushed repository (no server, no database)."""

# ruff: noqa: ANN201
from __future__ import annotations

import hashlib

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
    d = repo.diff([entry("keep.py", "k\n")], False, [])
    assert d.deleted == [] and (repo.tree / "gone.py").is_file()
    d = repo.diff([entry("keep.py", "k\n")], True, [])
    assert sorted(d.deleted) == ["dir/x.py", "gone.py"]
    assert not (repo.tree / "gone.py").exists() and not (repo.tree / "dir").exists()  # the emptied directory goes too
    assert list(repo.manifest()) == ["keep.py"]


def test_explicit_deletes_and_a_rename_leave_no_old_path(repo):
    push(repo, **{"old.py": "def f(): pass\n"})
    d = repo.diff([entry("new.py", "def f(): pass\n")], False, ["old.py"])
    assert d.deleted == ["old.py"] and d.upload == ["new.py"]
    push(repo, **{"new.py": "def f(): pass\n"})
    assert sorted(repo.manifest()) == ["new.py"] and not (repo.tree / "old.py").exists()


def test_a_file_that_goes_missing_on_disk_is_asked_for_again(repo):
    push(repo, **{"a.py": "a\n"})
    (repo.tree / "a.py").unlink()
    assert repo.diff([entry("a.py", "a\n")], True, []).upload == ["a.py"]


@pytest.mark.parametrize("path", ["../x.py", "a/../../x.py", "/etc/passwd", "a//b.py", "./a.py", ".git/config", "src/.git/x", "a\\b.py", "", "a/", "x\x00y", "tab\tname.py", "a" * 300 + ".py", "dir/" + "文" * 100 + ".py", "<b>x</b>.py", "pkg/<script>alert(1)</script>.js"])
def test_unsafe_paths_are_refused_everywhere(repo, path):
    d = repo.diff([{"path": path, "sha256": sha("x"), "size": 1}], True, [path])
    assert d.upload == [] and d.deleted == [] and d.rejected and {r["reason"] for r in d.rejected} == {"invalid_path"}
    stored, rejected = repo.store([{"path": path, "sha256": sha("x"), "content": "x"}])
    assert stored == [] and rejected[0]["reason"] == "invalid_path"
    assert not any(p for p in repo.tree.rglob("*") if p.is_file())


def test_a_path_is_stored_in_nfc_form(repo):
    nfd = "café.py"
    stored, _ = push(repo, **{nfd: "x\n"})
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
    assert rejected == [{"path": "a", "reason": "invalid_path"}] and (repo.tree / "a" / "b.py").is_file()


def test_a_symlink_in_the_tree_cannot_redirect_a_write(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.tree / "link").symlink_to(outside, target_is_directory=True)
    _, rejected = push(repo, **{"link/x.py": "x\n"})
    assert rejected == [{"path": "link/x.py", "reason": "invalid_path"}] and not (outside / "x.py").exists()


def test_a_write_is_all_or_nothing_per_file_and_leaves_no_temporary_in_the_tree(repo):
    push(repo, **{"a.py": "one\n"})
    push(repo, **{"a.py": "two\n"})
    assert (repo.tree / "a.py").read_text() == "two\n"
    assert [p.name for p in repo.tree.iterdir()] == ["a.py"]


def test_identity_comes_from_org_user_and_key_only(tmp_path):
    a = store.tree_of("org1", "user1", KEY, base=tmp_path)
    assert a == store.tree_of("org1", "user1", KEY, base=tmp_path)
    assert len({a, store.tree_of("org1", "user2", KEY, base=tmp_path), store.tree_of("org2", "user1", KEY, base=tmp_path),
                store.tree_of("org1", "user1", "other-key-0002", base=tmp_path)}) == 4
    assert tmp_path in a.parents
    for bad in ("short", "../../etc/passwd-aaaa", "a/b/c/d/e/f/g/h", "x" * 65, "with space aaaa", ""):
        with pytest.raises(store.StoreError):
            store.tree_of("org1", "user1", bad, base=tmp_path)
    for bad_id in ("..", "a/b", "", "x" * 200):
        with pytest.raises(store.StoreError):
            store.tree_of(bad_id, "user1", KEY, base=tmp_path)
        with pytest.raises(store.StoreError):
            store.tree_of("org1", bad_id, KEY, base=tmp_path)


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
