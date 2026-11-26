"""Round 14 (WP-69 J-26 inputs x the workspace sync): the largest file written back follows ``HARNESS_INPUT_MAX_MB``.

WP-69 made the deployment's largest input ``HARNESS_INPUT_MAX_MB`` (100 MiB by default, an org may only lower it). The
sync that collects the engine's files back into the workspace had its own fixed 100 MiB per file, so an operator who
raised inputs to 300 MiB could hand an engine a 200 MiB file it could never write back changed. One number now:

R14-WB-1 the write-back limits take the deployment's input cap as the largest file, and the archive and expansion
         bounds are never below it (a file the cap allows is never refused as an archive too large).
R14-WB-2 a file over the cap is skipped and the rest collected; under a raised cap the same file is collected.
R14-WB-3 every production sync is built with those limits, from the controller's ``input_max_bytes``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from harness_core.workspace.sync.types import (
    FILE_TOO_LARGE,
    SyncLimits,
    write_back_limits,
)
from tests.unit.harness_core.workspace.sync.test_archive_safety import (
    HostileSandbox,
    archive,
    member,
    state,
    sync,
)

MIB = 1024 * 1024
ROOT = Path(__file__).resolve().parents[5]


def test_r14_wb_1_the_largest_file_written_back_is_the_input_cap() -> None:
    assert write_back_limits(100 * MIB) == SyncLimits(), "the default deployment keeps the old bounds"
    raised = write_back_limits(3 * 1024 * MIB)
    assert raised.max_file_bytes == 3 * 1024 * MIB
    assert raised.max_archive_bytes >= raised.max_file_bytes
    assert raised.max_expanded_bytes >= raised.max_file_bytes
    lowered = write_back_limits(10 * MIB)
    assert (lowered.max_file_bytes, lowered.max_archive_bytes) == (10 * MIB, SyncLimits().max_archive_bytes)


async def test_r14_wb_2_a_file_the_cap_allows_is_collected_and_one_over_it_skipped() -> None:
    big = member("ws/scan.pdf", b"p" * (2 * MIB))
    small = member("ws/notes.md", b"n" * 10)
    lowered, _ = sync(write_back_limits(1 * MIB))
    collected = await lowered.collect(HostileSandbox(archive(big, small)), state())  # type: ignore[arg-type]
    assert collected.skipped == {"scan.pdf": FILE_TOO_LARGE}
    assert set(collected.changes.upserts) == {"notes.md"}
    raised, _ = sync(write_back_limits(3 * MIB))
    big = member("ws/scan.pdf", b"p" * (2 * MIB))
    small = member("ws/notes.md", b"n" * 10)
    collected = await raised.collect(HostileSandbox(archive(big, small)), state())  # type: ignore[arg-type]
    assert collected.skipped == {}
    assert set(collected.changes.upserts) == {"scan.pdf", "notes.md"}


def test_r14_wb_3_every_production_sync_is_built_with_the_input_cap() -> None:
    built: list[str] = []
    for folder in ("app", "harness_core"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "TarWorkspaceSync(" not in source or "/testing/" in path.as_posix():
                continue
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "TarWorkspaceSync":
                    rel = path.relative_to(ROOT).as_posix()
                    limits = next((k.value for k in node.keywords if k.arg == "limits"), None)
                    assert isinstance(limits, ast.Call), (rel, "built without limits")
                    assert getattr(limits.func, "id", None) == "write_back_limits", rel
                    assert "input_max_bytes" in ast.unparse(limits), (rel, ast.unparse(limits))
                    built.append(rel)
    assert "app/harness/engines/durable.py" in built, built


async def test_r46fix_2_a_file_already_saved_over_a_lowered_cap_is_kept_never_deleted() -> None:
    """R46FIX-2 (MR-16): the cap lowered after a file over it was saved logs ``workspace_sync.skipped
    reason=file_too_large`` on the next save; the file stays in the workspace as it was, never proposed as deleted."""
    from harness_core.workspace.sync.types import SyncState
    from harness_core.workspace.types import ObjectRef, WorkspaceFile, build_manifest
    from tests.unit.harness_core.workspace.sync.test_archive_safety import (
        ROOT as SANDBOX_ROOT,
    )

    saved = WorkspaceFile("out/plan.txt", ObjectRef("obj-plan", "sha256:" + "a" * 64, 2 * MIB))
    base = build_manifest("ws:s", revision=1, parent_revision=0, files={"out/plan.txt": saved}, created_at_ms=1)
    lowered, _ = sync(write_back_limits(1 * MIB))
    sandbox = HostileSandbox(archive(member("ws/out/plan.txt", b"x" * (2 * MIB)), member("ws/out/small.txt", b"s")))
    collected = await lowered.collect(sandbox, SyncState("sbx-hostile", SANDBOX_ROOT, base))  # type: ignore[arg-type]
    assert collected.skipped == {"out/plan.txt": FILE_TOO_LARGE}
    assert "out/plan.txt" not in collected.changes.deletes
    assert set(collected.changes.upserts) == {"out/small.txt"}
