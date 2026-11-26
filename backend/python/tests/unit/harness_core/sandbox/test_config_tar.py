"""SEC1 EC-7: what an engine obeys is written root's and read-only, in folders it shares with root."""

from __future__ import annotations

import io
import tarfile

import pytest

from harness_core.sandbox.tar import (
    CONFIG_MODE,
    SHARED_DIR_MODE,
    config_tar,
    put_config,
)


async def _members(stream) -> list[tarfile.TarInfo]:
    data = b"".join([chunk async for chunk in stream])
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return archive.getmembers()


async def test_ec7_files_links_and_homes_belong_to_root() -> None:
    members = await _members(
        config_tar({"/claude/settings.json": b"{}"}, homes=["/claude"], links={"/claude/skills": "/skills"})
    )
    by_name = {m.name: m for m in members}
    assert [m.name for m in members] == ["claude", "claude/skills", "claude/settings.json"]
    home, link, settings = by_name["claude"], by_name["claude/skills"], by_name["claude/settings.json"]
    assert home.isdir() and home.mode == SHARED_DIR_MODE
    assert link.issym() and link.linkname == "/skills"
    assert settings.isfile() and settings.mode == CONFIG_MODE
    assert {(m.uid, m.gid) for m in members} == {(0, 0)}


class _Recorder:
    def __init__(self) -> None:
        self.put: dict[str, list[str]] = {}

    async def put_tar(self, dest: str, stream) -> None:
        self.put[dest] = [m.name for m in await _members(stream)]


async def test_ec7_one_archive_per_mount_relative_to_it() -> None:
    sandbox = _Recorder()
    await put_config(sandbox, {"/state/codex/config.toml": b"x", "/workspace/.keep": b""}, homes=["/state/codex"])  # type: ignore[arg-type]
    assert sandbox.put == {"/state": ["codex", "codex/config.toml"], "/workspace": [".keep"]}


@pytest.mark.parametrize("path", ["relative/x", "/toplevel", "/state/../etc/x"])
async def test_ec7_paths_outside_a_mount_are_refused(path: str) -> None:
    with pytest.raises(ValueError, match="top-level"):
        await put_config(_Recorder(), {path: b""})  # type: ignore[arg-type]
