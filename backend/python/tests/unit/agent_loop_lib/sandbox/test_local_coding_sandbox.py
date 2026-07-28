"""Tests for the local backend's `$OUTPUT_DIR` parity with Docker — the
"output-dir-parity" fix. Without this, `run_code`'s "write to $OUTPUT_DIR"
contract would be true on Docker but not on the local (default) backend."""

from __future__ import annotations

import os

import pytest

from app.agent_loop_lib.sandbox.coding.base import normalize_sandbox_path
from app.agent_loop_lib.sandbox.coding.environment import sanitized_subprocess_env
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.agent_loop_lib.sandbox.manager import SandboxManager, UnknownSandboxError
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    ReadSandboxFileTool,
    unknown_sandbox_guidance,
)


class TestSanitizedSubprocessEnvOutputDir:
    def test_output_dir_is_working_dir_slash_output(self, tmp_path) -> None:
        working_dir = str(tmp_path)
        env = sanitized_subprocess_env(working_dir)
        assert env["OUTPUT_DIR"] == os.path.join(working_dir, "output")


class TestLocalCodingSandboxProvision:
    async def test_provision_creates_the_output_directory(self, tmp_path) -> None:
        sandbox = LocalCodingSandbox(working_dir=str(tmp_path / "sandbox-1"))

        await sandbox.provision()

        assert os.path.isdir(os.path.join(sandbox.working_dir, "output"))

    async def test_provision_is_idempotent(self, tmp_path) -> None:
        sandbox = LocalCodingSandbox(working_dir=str(tmp_path / "sandbox-1"))

        await sandbox.provision()
        await sandbox.provision()

        assert os.path.isdir(os.path.join(sandbox.working_dir, "output"))


class TestNormalizeSandboxPath:
    """The `$OUTPUT_DIR` contract tells the model where to WRITE, so it
    reaches for the same spelling when naming the file to a path-taking
    tool — but nothing shell-expands a tool argument."""

    @pytest.mark.parametrize(
        "given",
        ["$OUTPUT_DIR/report.pdf", "${OUTPUT_DIR}/report.pdf", "/output/report.pdf",
         "output/report.pdf", "  $OUTPUT_DIR/report.pdf  "],
    )
    def test_every_spelling_of_the_output_dir_resolves_to_one_path(self, given) -> None:
        assert normalize_sandbox_path(given) == "output/report.pdf"

    def test_bare_alias_becomes_the_directory_itself(self) -> None:
        assert normalize_sandbox_path("$OUTPUT_DIR") == "output"

    @pytest.mark.parametrize(
        "given",
        ["input/artifacts/data.csv", "main.py", "/etc/passwd", "/outputs/x.pdf",
         "$OUTPUT_DIRECTORY/x.pdf", "../escape.txt"],
    )
    def test_leaves_every_other_path_untouched(self, given) -> None:
        assert normalize_sandbox_path(given) == given.strip()


class TestResolvePathStillRejectsEscapes:
    """Normalization must not become a way around the traversal guard."""

    @pytest.mark.parametrize("given", ["/etc/passwd", "../../etc/passwd", "$OUTPUT_DIR/../../../etc/passwd"])
    def test_escaping_paths_are_rejected(self, tmp_path, given) -> None:
        sandbox = LocalCodingSandbox(working_dir=str(tmp_path / "sandbox-1"))
        with pytest.raises(ValueError, match="escapes the sandbox"):
            sandbox._resolve_path(given)

    async def test_output_dir_alias_reads_the_real_file(self, tmp_path) -> None:
        sandbox = LocalCodingSandbox(working_dir=str(tmp_path / "sandbox-1"))
        await sandbox.provision()
        with open(os.path.join(sandbox.working_dir, "output", "report.pdf"), "wb") as f:
            f.write(b"pdf-bytes")

        assert await sandbox.download_file("$OUTPUT_DIR/report.pdf") == b"pdf-bytes"


class TestUnknownSandboxTools:
    """A dead sandbox_id is unrecoverable, so the error has to say what to
    do instead — otherwise the model retries with another guessed id."""

    async def test_read_sandbox_file_explains_that_ids_do_not_survive_the_turn(self) -> None:
        manager = SandboxManager()
        tool = ReadSandboxFileTool(manager)

        result = await tool.execute(sandbox_id="33974192-b41f-4db2-ade0-3fd891aa5b96", path="code.py")

        assert result.success is False
        assert "no 'coding' sandbox with id" in result.error
        assert "only for the turn that created them" in result.error
        assert "Do not retry with another id" in result.error

    def test_guidance_keeps_the_original_error_text(self) -> None:
        message = unknown_sandbox_guidance(UnknownSandboxError("no 'coding' sandbox with id 'abc'"))

        assert message.startswith("no 'coding' sandbox with id 'abc'.")
