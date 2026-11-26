"""SEC-FS-02: a name a person or an engine supplies never becomes a path outside its folder (SFN-1..SFN-11).

The shared check (``harness_core.workspace.safe_names``) holds the cross-language vectors Node is tested against
(``contracts/harness/vectors/safe-names.json``), and every place a supplied name becomes a file uses it: the name
an input is staged under, the name ``ph inputs get`` asks for, a symbolic link's target, a sandbox's archive member
entering the store, an output published as an artifact, the broker's copy of a persistent folder and the write of a
large input into a running sandbox.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from harness_core.engines.controller import OUTPUT_UNSAFE_NAME, collected_deliverables
from harness_core.engines.durable.inputs import input_name
from harness_core.engines.input_staging import input_path
from harness_core.sandbox.workspace_fs import WorkspacePathRefused
from harness_core.testing.contracts.workspace import file
from harness_core.testing.contracts.workspace_sync import acquire, commit, tree
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.safe_names import (
    UnsafeName,
    check_name,
    check_portable_path,
    link_target_escapes,
)
from harness_core.workspace.sync import TAR
from harness_core.workspace.sync.rules import link_escapes, member_path
from harness_core.workspace.sync.types import UNSTORABLE_PATH, Collected
from tests.unit.harness_core.workspace.sync.support import ROOT, FakeSyncHarness

VECTORS = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/harness/vectors/safe-names.json").read_text(encoding="utf-8")
)
ALL_NAMES = [raw for raw, _ in VECTORS["names"]["accepted"]] + [raw for raw, _ in VECTORS["names"]["refused"]]


@pytest.mark.parametrize(("raw", "canonical"), VECTORS["names"]["accepted"])
def test_sfn_1_a_plain_name_is_accepted_in_nfc(raw: str, canonical: str) -> None:
    assert check_name(raw) == canonical


@pytest.mark.parametrize(("raw", "reason"), VECTORS["names"]["refused"])
def test_sfn_1_an_unsafe_name_is_refused_with_its_reason(raw: str, reason: str) -> None:
    with pytest.raises(UnsafeName) as refused:
        _ = check_name(raw)
    assert refused.value.reason == reason


@pytest.mark.parametrize(("raw", "canonical"), VECTORS["paths"]["accepted"])
def test_sfn_2_a_workspace_path_linux_allows_is_accepted(raw: str, canonical: str) -> None:
    assert check_portable_path(raw) == canonical


@pytest.mark.parametrize(("raw", "reason"), VECTORS["paths"]["refused"])
def test_sfn_2_a_workspace_path_that_escapes_on_some_system_is_refused(raw: str, reason: str) -> None:
    with pytest.raises(UnsafeName) as refused:
        _ = check_portable_path(raw)
    assert refused.value.reason == reason


@pytest.mark.parametrize("target", VECTORS["linkTargets"]["plain"])
def test_sfn_3_a_plain_relative_link_target_is_left_to_where_it_lands(target: str) -> None:
    assert not link_target_escapes(target)


@pytest.mark.parametrize("target", VECTORS["linkTargets"]["refused"])
def test_sfn_3_a_link_target_of_another_system_escapes(target: str) -> None:
    assert link_target_escapes(target)
    assert link_escapes("dir/link", target)


@pytest.mark.parametrize("raw", [*ALL_NAMES, "COM\u00b9.pdf", "LPT\u00b2", "..\\..\\x.pdf", "C:\\x.pdf"])
def test_sfn_4_an_upload_is_staged_under_a_name_the_check_takes(raw: str) -> None:
    staged = input_name(raw)
    assert check_name(staged) == staged


@pytest.mark.parametrize(
    "raw",
    ["..", "in/..", "/pipeshub/inputs/..", "%2e%2e", "..%2fsecret", "C:", "c:big.pdf", "big.pdf\x00", "CON", "a\nb"],
)
def test_sfn_5_ph_inputs_get_names_no_path_but_one_plain_file(raw: str) -> None:
    assert input_path({"name": raw}) is None


@pytest.mark.parametrize("name", ["C:", "c:x", "%2e%2e", "a%2fb", "x%5cy"])
def test_sfn_6_an_archive_member_with_a_drive_or_encoded_separator_is_not_stored(name: str) -> None:
    placed = member_path(f"workspace/dir/{name}", "workspace")
    assert (placed.path, placed.skip) == (None, UNSTORABLE_PATH)


@pytest.mark.parametrize("name", ["CON.txt", "report.", "a:b.csv", "aux", "charts/nul.png"])
def test_sfn_7_an_output_no_computer_can_hold_as_one_file_is_refused_not_published(name: str) -> None:
    upserts = {f"out/{name}": file(f"out/{name}", "x"), "out/ok.csv": file("out/ok.csv", "y")}
    collected = Collected(changes=ChangeSet(upserts=upserts), skipped={})
    outputs, refused = collected_deliverables(collected, "out")
    assert [o.path for o in outputs] == ["ok.csv"]
    assert [(r.path, r.reason_code) for r in refused] == [(name, OUTPUT_UNSAFE_NAME)]


def _tar(members: list[tarfile.TarInfo], contents: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for member in members:
            data = contents.get(member.name)
            if data is not None:
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def _member(name: str, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info


def test_sfn_8_the_broker_copies_a_persistent_folder_without_members_that_climb_out_or_write_through_links() -> None:
    from app.harness.sandbox.docker_broker import (
        _strip_top,  # pyright: ignore[reportPrivateUsage]
    )

    data = _tar(
        [
            _member("cache/ok.txt"),
            _member("cache/../../escape.txt"),
            _member("cache//abs.txt"),
            _member("cache/venv-python", tarfile.SYMTYPE, "/usr/bin/python3"),
            _member("cache/etc", tarfile.SYMTYPE, "/etc"),
            _member("cache/etc/passwd"),
            _member("cache/hard", tarfile.LNKTYPE, "cache/ok.txt"),
            _member("cache/hard-out", tarfile.LNKTYPE, "../../etc/shadow"),
            _member("cache/fifo", tarfile.FIFOTYPE),
        ],
        {"cache/ok.txt": b"ok", "cache/../../escape.txt": b"x", "cache//abs.txt": b"x", "cache/etc/passwd": b"x"},
    )
    out = io.BytesIO()
    _strip_top(iter([data]), out)
    with tarfile.open(fileobj=out, mode="r:") as archive:
        kept = {m.name: (m.type, m.linkname) for m in archive}
    assert kept == {
        "ok.txt": (tarfile.REGTYPE, ""),
        "venv-python": (tarfile.SYMTYPE, "/usr/bin/python3"),
        "etc": (tarfile.SYMTYPE, "/etc"),
        "hard": (tarfile.LNKTYPE, "ok.txt"),
    }


async def test_sfn_9_a_large_input_staged_mid_turn_never_follows_a_link_the_engine_planted() -> None:
    """The engine made ``in`` a link to a folder outside the workspace before asking for the input: the bytes
    never land there, nothing is left behind, and the input stays waiting."""
    h = FakeSyncHarness(TAR)
    manifest = await commit(h, {"in/big.pdf": b"%PDF big", "notes.md": b"n"})
    sandbox = await acquire(h)
    state = await h.sync.materialize(sandbox, manifest, root=ROOT, defer={"in/big.pdf"})
    await h.hands.mkdir(sandbox, "/tmp/elsewhere")
    await h.hands.remove(sandbox, f"{ROOT}/in")
    await h.hands.symlink(sandbox, f"{ROOT}/in", "/tmp/elsewhere")
    with pytest.raises(WorkspacePathRefused):
        _ = await h.sync.stage(sandbox, state, "in/big.pdf")
    files = sandbox.files  # type: ignore[attr-defined]
    assert "/tmp/elsewhere/big.pdf" not in files
    assert not any(".pipeshub-staging-" in path for path in files)
    assert set(await tree(sandbox, ROOT)) == {"in", "notes.md"}


async def test_sfn_9_a_large_input_staged_into_a_real_folder_lands_as_the_workspace_holds_it() -> None:
    h = FakeSyncHarness(TAR)
    manifest = await commit(h, {"in/big.pdf": b"%PDF big"})
    sandbox = await acquire(h)
    state = await h.sync.materialize(sandbox, manifest, root=ROOT, defer={"in/big.pdf"})
    state = await h.sync.stage(sandbox, state, "in/big.pdf")
    files = await tree(sandbox, ROOT)
    assert files == {"in/big.pdf": (manifest.files["in/big.pdf"].mode, b"%PDF big")}
    assert not any(".pipeshub-staging-" in path for path in sandbox.files)  # type: ignore[attr-defined]
