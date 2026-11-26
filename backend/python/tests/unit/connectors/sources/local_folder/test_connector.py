"""Behaviour tests for the Folder connector against an in-memory graph."""

# ruff: noqa: ANN201, ANN202

from __future__ import annotations

import errno
import json
import os
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.sources.local_folder import scan
from app.models.entities import CodeFileRecord, FileRecord, RecordType
from app.models.permission import PermissionType
from tests.unit.connectors.sources.local_folder.harness import (
    FilterCollection,
    full_sync_unlinks,
    make_connector,
    mod,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = (tmp_path / "ws").resolve()
    root.mkdir()
    c, graph, sync = make_connector(root)
    patcher = patch.object(mod, "load_connector_filters", AsyncMock(return_value=(FilterCollection(), FilterCollection())))
    patcher.start()
    yield types.SimpleNamespace(root=root, graph=graph, c=c, sync=sync)
    patcher.stop()


def put(root, rel, data="print('hi')\n"):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    return p


async def test_first_sync_builds_hierarchy_languages_and_owner_group(env):
    put(env.root, "src/app.py")
    put(env.root, "docs/readme.md", "# hi")
    put(env.root, "notes.txt", "n")
    put(env.root, "node_modules/x.js")
    put(env.root, "blob.dat", "ab\x00cd")
    await env.c.run_sync()
    recs = env.graph.records
    assert sorted(recs) == ["docs", "docs/readme.md", "notes.txt", "src", "src/app.py"]
    code = recs["src/app.py"]
    assert isinstance(code, CodeFileRecord) and code.record_type == RecordType.CODE_FILE
    assert code.language == "python" and code.parent_external_record_id == "src"
    assert isinstance(recs["docs/readme.md"], FileRecord) and recs["docs/readme.md"].mime_type == "text/markdown"
    assert recs["src"].is_file is False and recs["src"].parent_external_record_id is None
    # Container rule: one group for the root, owned by the configuring user.
    assert len(env.graph.groups) == 1 and env.graph.groups[0].external_group_id == "folder:c1"
    assert [(p.email, p.type) for p in env.graph.perms] == [("dev@example.com", PermissionType.OWNER)]
    assert all(r.external_record_group_id == "folder:c1" for r in recs.values())


async def test_incremental_touches_only_what_changed_and_deletes(env):
    put(env.root, "a.py", "a = 1\n")
    p = put(env.root, "pkg/b.py", "b = 1\n")
    await env.c.run_sync()
    env.graph.new_calls.clear()

    await env.c.run_sync()
    assert env.graph.new_calls == []  # nothing moved, nothing written

    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))  # touched, same bytes
    await env.c.run_sync()
    assert env.graph.new_calls == []

    old_rev = env.graph.records["pkg/b.py"].external_revision_id
    p.write_text("b = 2\n")
    await env.c.run_sync()
    assert env.graph.new_calls == [["pkg/b.py"]]
    assert env.graph.records["pkg/b.py"].external_revision_id != old_rev

    (env.root / "a.py").unlink()
    (env.root / "pkg" / "b.py").unlink()
    (env.root / "pkg").rmdir()
    await env.c.run_sync()
    assert env.graph.records == {}
    assert set(env.graph.deleted) == {"a.py", "pkg/b.py", "pkg"}


async def test_gitignore_and_user_patterns_and_new_ignore_removes_records(env):
    put(env.root, ".gitignore", "*.gen.py\n")
    put(env.root, "a.py")
    put(env.root, "b.gen.py")
    put(env.root, "keep.txt", "k")
    await env.c.run_sync()
    assert set(env.graph.records) == {".gitignore", "a.py", "keep.txt"}
    env.sync["ignore_patterns"] = "keep.txt, a.py"
    # Ignoring is a change of scope: the path leaves the walk, so its record goes.
    await env.c.run_sync()
    assert set(env.graph.records) == {".gitignore"}


async def test_first_sync_prunes_leftovers_from_an_earlier_instance(env):
    stale = FileRecord(id="old", record_name="gone.txt", record_type=RecordType.FILE, external_record_id="gone.txt",
                       version=0, origin="CONNECTOR", connector_name=mod.Connectors.LOCAL_REPO, connector_id="c1",
                       is_file=True, record_group_type="DRIVE")
    env.graph.records["gone.txt"] = stale
    put(env.root, "a.py")
    await env.c.run_sync()
    assert "gone.txt" not in env.graph.records and "a.py" in env.graph.records


async def test_unreadable_file_is_not_deleted_and_sync_continues(env):
    put(env.root, "a.py")
    put(env.root, "b.py")
    await env.c.run_sync()
    (env.root / "b.py").write_text("changed = 1\n")
    real = scan.safe_open

    def flaky(root, rel, max_links=1):
        if rel == "b.py":
            raise scan.PathRefused("boom")
        return real(root, rel, max_links)

    with patch.object(scan, "safe_open", flaky):
        await env.c.run_sync()
    assert "b.py" in env.graph.records and env.graph.deleted == []


async def _body(resp):
    return b"".join([c async for c in resp.body_iterator])


async def test_stream_record_reads_file_and_refuses_escape(env, tmp_path):
    put(env.root, "src/a.py", "print(1)\n")
    (tmp_path / "secret.txt").write_text("secret")
    os.symlink(tmp_path / "secret.txt", env.root / "leak.txt")
    await env.c.run_sync()
    rec = env.graph.records["src/a.py"]
    assert await _body(await env.c.stream_record(rec)) == b"print(1)\n"
    for ext_id in ["../secret.txt", "/etc/passwd", "leak.txt", "src/../../secret.txt"]:
        forged = rec.model_copy(update={"external_record_id": ext_id})
        with pytest.raises(HTTPException) as e:
            await env.c.stream_record(forged)
        assert e.value.status_code == 404


async def test_manual_index_filter_marks_auto_index_off(env):
    put(env.root, "a.py")
    flt = MagicMock()
    flt.is_enabled = MagicMock(return_value=True)
    with patch.object(mod, "load_connector_filters", AsyncMock(return_value=(FilterCollection(), flt))):
        await env.c.run_sync()
    assert env.graph.records["a.py"].indexing_status == "AUTO_INDEX_OFF"


async def test_file_and_directory_swapping_at_one_path_keep_the_new_record(env):
    put(env.root, "a", "plain\n")
    put(env.root, "b/inner.py")
    await env.c.run_sync()
    (env.root / "a").unlink()
    put(env.root, "a/inner.py")
    (env.root / "b" / "inner.py").unlink()
    (env.root / "b").rmdir()
    put(env.root, "b", "now a file\n")
    await env.c.run_sync()
    await env.c.run_sync()
    assert set(env.graph.records) == {"a", "a/inner.py", "b"}
    assert env.graph.records["a"].is_file is False and env.graph.records["b"].is_file is True


async def test_pdf_and_office_files_are_indexed_by_mime_but_true_binaries_are_not(env):
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nstream\nx\x9c\x00\x01\nendstream\n%%EOF\n"
    for name in (
        "a.pdf",
        "a.docx",
        "a.xlsx",
        "a.pptx",
    ):
        (env.root / name).write_bytes(pdf if name.endswith("pdf") else b"PK\x03\x04\x00\x00\x00\x00")
    (env.root / "blob.dat").write_bytes(b"\x00\x01\x02")
    await env.c.run_sync()
    assert set(env.graph.records) == {"a.pdf", "a.docx", "a.xlsx", "a.pptx"}
    assert env.graph.records["a.docx"].mime_type.endswith("wordprocessingml.document")
    assert env.graph.records["a.pdf"].mime_type == "application/pdf"


async def test_non_utf8_name_is_stored_safely_and_still_streams(env):
    with open(os.fsencode(str(env.root)) + b"/bad\xff.txt", "wb") as f:
        f.write(b"hello")
    await env.c.run_sync()
    assert list(env.graph.records) == ["bad%FF.txt"]
    rec = env.graph.records["bad%FF.txt"]
    rec.record_name.encode("utf-8")  # store-safe
    assert await _body(await env.c.stream_record(rec)) == b"hello"


async def test_records_lost_from_the_store_are_rewritten(env):
    put(env.root, "d/a.py")
    await env.c.run_sync()
    env.graph.records.clear()
    await env.c.run_sync()
    assert set(env.graph.records) == {"d", "d/a.py"}


async def test_changed_root_resets_the_file_list(env, tmp_path, monkeypatch):
    other = (tmp_path / "other").resolve()
    other.mkdir()
    p1 = put(env.root, "same.py", "A = 1\n")
    p2 = put(other, "same.py", "B = 2\n")
    os.utime(p2, ns=(p1.stat().st_atime_ns, p1.stat().st_mtime_ns))
    put(env.root, "only_old.py")
    await env.c.run_sync()
    rev = env.graph.records["same.py"].external_revision_id
    env.sync["root"] = str(other)
    await env.c.run_sync()
    assert env.graph.records["same.py"].external_revision_id != rev
    assert "only_old.py" not in env.graph.records


async def test_literal_percent_names_do_not_collide_and_bad_ids_are_404(env):
    with open(os.fsencode(str(env.root)) + b"/a\xff.txt", "wb") as f:
        f.write(b"BIN")
    put(env.root, "a%FF.txt", "LIT")
    await env.c.run_sync()
    assert set(env.graph.records) == {"a%FF.txt", "a%25FF.txt"}
    assert await _body(await env.c.stream_record(env.graph.records["a%25FF.txt"])) == b"LIT"
    for bad in ["x%00y", "..%2Fws%2Fx", "%2Fetc%2Fpasswd"]:
        forged = env.graph.records["a%25FF.txt"].model_copy(update={"external_record_id": bad})
        with pytest.raises(HTTPException) as e:
            await env.c.stream_record(forged)
        assert e.value.status_code == 404


async def test_old_root_records_are_pruned_even_if_a_file_was_unreadable(env, tmp_path, monkeypatch):
    other = (tmp_path / "other").resolve()
    other.mkdir()
    put(env.root, "only_old.py")
    put(other, "ok.py")
    put(other, "locked.py")
    await env.c.run_sync()
    env.sync["root"] = str(other)
    real = scan.safe_open

    def flaky(root, rel, max_links=1):
        if rel == "locked.py":
            raise scan.PathRefused("denied")
        return real(root, rel, max_links)

    with patch.object(scan, "safe_open", flaky):
        await env.c.run_sync()
    assert "only_old.py" not in env.graph.records and "ok.py" in env.graph.records


async def test_legacy_office_files_are_routed_by_mime(env):
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00"
    for n in ("a.doc", "a.xls", "a.ppt"):
        (env.root / n).write_bytes(ole)
    await env.c.run_sync()
    assert {k: v.mime_type for k, v in env.graph.records.items()} == {
        "a.doc": "application/msword", "a.xls": "application/vnd.ms-excel", "a.ppt": "application/vnd.ms-powerpoint",
    }


async def test_code_in_a_git_repository_is_filed_under_the_repositorys_own_group(env):
    put(env.root, "shop/.git/HEAD", "ref: refs/heads/main\n")
    put(env.root, "shop/pkg/a.py")
    put(env.root, "shop/README.md", "# shop")
    put(env.root, "loose/b.py")
    await env.c.run_sync()
    recs = env.graph.records
    # a repository is a directory holding .git; its code files go to "<group>:<repo>-code-repository", everything else to the root group
    assert recs["shop/pkg/a.py"].external_record_group_id == "folder:c1:shop-code-repository"
    assert recs["shop/README.md"].external_record_group_id == "folder:c1"
    assert recs["loose/b.py"].external_record_group_id == "folder:c1"
    assert {g.external_group_id for g in env.graph.groups} == {"folder:c1", "folder:c1:shop-code-repository"}
    repo_group = next(g for g in env.graph.groups if g.external_group_id.endswith("-code-repository"))
    assert repo_group.name == "shop"
    # the repository grants no more than the root does (the same single owner) and has no parent, which would rename the repository in the code graph
    assert repo_group.parent_external_group_id is None and repo_group.inherit_permissions is False
    assert [(p.email, p.type) for p in env.graph.perms] == [("dev@example.com", PermissionType.OWNER)] * 2 or {p.email for p in env.graph.perms} == {"dev@example.com"}


async def test_a_sync_point_written_as_a_list_of_rows_by_an_earlier_version_is_still_read(env):
    put(env.root, "a.py")
    put(env.root, "b.py")
    await env.c.run_sync()
    env.c.record_sync_point.doc["files"] = json.loads(env.c.record_sync_point.doc["files"])  # the shape before the rows became a JSON string
    (env.root / "b.py").unlink()
    await env.c.run_sync()
    assert "b.py" not in env.graph.records and "a.py" in env.graph.records  # b.py was known only from the old rows
    assert isinstance(env.c.record_sync_point.doc["files"], str)


async def test_a_sync_whose_walk_could_not_read_a_folder_fails_and_keeps_every_record(env):
    put(env.root, "ok/a.py")
    put(env.root, "locked/b.py")
    await env.c.run_sync()
    assert set(env.graph.records) >= {"ok/a.py", "locked/b.py"}
    real = os.scandir

    def scandir(path):
        if str(path).endswith("locked"):
            raise PermissionError(13, "denied")
        return real(path)

    with patch.object(os, "scandir", scandir), pytest.raises(scan.WalkFailed):
        await env.c.run_sync()
    assert "locked/b.py" in env.graph.records and env.graph.deleted == []  # unreadable is not "deleted"


async def test_a_full_sync_keeps_the_links_of_files_it_could_not_read_and_unlinks_only_what_is_gone(env):
    put(env.root, "a.py")
    put(env.root, "locked/b.py")
    put(env.root, "c.py")
    put(env.root, "gone.py")
    await env.c.run_sync()
    assert set(env.graph.records) == {"a.py", "locked", "locked/b.py", "c.py", "gone.py"}
    (env.root / "gone.py").unlink()
    real_open, real_hash = scan.safe_open, scan.sha256_fd
    opened: dict[int, str] = {}

    def flaky_open(root, rel, max_links=1):
        if rel == "locked/b.py":
            raise scan.PathRefused("cannot open: Permission denied")
        fd = real_open(root, rel, max_links)
        opened[fd] = rel
        return fd

    def flaky_hash(fd):
        if opened.get(fd) == "c.py":
            os.close(fd)
            raise OSError(errno.EIO, "Input/output error")
        return real_hash(fd)

    with patch.object(scan, "safe_open", flaky_open), patch.object(scan, "sha256_fd", flaky_hash):
        unlinked = await full_sync_unlinks(env.c, env.graph)
    on_disk = {"locked", "locked/b.py", "c.py"}
    assert "gone.py" in env.graph.deleted and "gone.py" not in env.graph.records
    assert unlinked & on_disk == set()  # still on disk: unreadable this run is not gone


def refusing(*rels):
    """``scan.safe_open`` that refuses ``rels`` as a file the process may not read."""
    real = scan.safe_open

    def open_(root, rel, max_links=1):
        if rel in rels:
            raise scan.PathRefused("cannot open: Permission denied")
        return real(root, rel, max_links)

    return patch.object(scan, "safe_open", open_)


def ids_of(graph, *rels):
    return {graph.records[r].id for r in rels}


async def test_a_full_sync_keeps_every_stored_folder_above_an_unreadable_file(env):
    for rel in ("a/x.py", "a/b/c/deep.py", "a/b/c/other.py"):
        put(env.root, rel)
    await env.c.run_sync()
    assert {"a", "a/b", "a/b/c"} <= set(env.graph.records)
    with refusing("a/b/c/deep.py", "a/b/c/other.py"):
        unlinked = await full_sync_unlinks(env.c, env.graph)
    assert unlinked == set() and env.graph.deleted == []
    assert "a" in env.graph.new_calls[-2]  # the ancestor of a live file is written
    assert set(env.graph.kept) >= ids_of(env.graph, "a/b", "a/b/c", "a/b/c/deep.py", "a/b/c/other.py")
    assert len(env.graph.kept) == len(set(env.graph.kept))


async def test_an_unreadable_file_that_was_never_stored_notes_nothing(env):
    put(env.root, "a.py")
    await env.c.run_sync()
    put(env.root, "new.py")
    with refusing("new.py"):
        unlinked = await full_sync_unlinks(env.c, env.graph)
    assert env.graph.kept == [] and env.c.full_sync_coverage().complete
    assert unlinked == set() and "new.py" not in env.graph.records


async def test_a_full_sync_that_cannot_list_stored_records_and_could_not_read_a_file_unlinks_nothing(env):
    put(env.root, "a.py")
    put(env.root, "locked/b.py")
    await env.c.run_sync()
    env.graph.get_records_by_status = AsyncMock(side_effect=RuntimeError("store down"))
    with refusing("locked/b.py"):
        unlinked = await full_sync_unlinks(env.c, env.graph)
    assert not env.c.full_sync_coverage().complete and unlinked == set()
    await full_sync_unlinks(env.c, env.graph)
    assert env.c.full_sync_coverage().complete  # every file read: nothing was held back


async def test_a_full_sync_with_every_file_readable_notes_nothing_kept_and_is_complete(env):
    put(env.root, "a.py")
    put(env.root, "d/e.py")
    await env.c.run_sync()
    (env.root / "a.py").unlink()
    unlinked = await full_sync_unlinks(env.c, env.graph)
    assert env.graph.kept == [] and env.c.full_sync_coverage().complete
    assert "a.py" in env.graph.deleted and {"d", "d/e.py"} <= set(env.graph.new_calls[-1] + env.graph.new_calls[-2])
    assert unlinked == set()


async def test_a_full_sync_still_unlinks_a_folder_emptied_on_disk_next_to_an_unreadable_file(env):
    for rel in ("keep/locked.py", "old/x.py", "old2.py"):
        put(env.root, rel)
    await env.c.run_sync()
    gone = ids_of(env.graph, "old", "old/x.py", "old2.py")
    held = ids_of(env.graph, "keep", "keep/locked.py")
    (env.root / "old" / "x.py").unlink()
    (env.root / "old").rmdir()
    (env.root / "old2.py").unlink()
    with refusing("keep/locked.py"):
        unlinked = await full_sync_unlinks(env.c, env.graph)
    assert unlinked == set() and set(env.graph.deleted) == {"old", "old/x.py", "old2.py"}
    assert sorted(env.graph.kept) == sorted(held) and not gone & set(env.graph.kept)


def test_loading_the_harness_leaves_the_real_application_container_in_place():
    import sys

    from app.containers import connector as container

    assert sys.modules["app.containers.connector"] is container and hasattr(container, "initialize_container")
