"""Tests for :mod:`app.sandbox.redact`."""

from __future__ import annotations

import pytest

from app.sandbox.redact import redact_sandbox_paths


class TestRedactSandboxPaths:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Local executor, full output-file path.
            (
                "Saved at: /tmp/pipeshub_sandbox/00408160-6b0f-42f2-8235-287a0e06d910/output/10_jokes.pdf",
                "Saved at: <output>/10_jokes.pdf",
            ),
            # macOS realpath form (/private/tmp).
            (
                "Saved at: /private/tmp/pipeshub_sandbox/abc-123/output/chart.png",
                "Saved at: <output>/chart.png",
            ),
            # Bare sandbox output dir, no file suffix.
            (
                "Output dir: /tmp/pipeshub_sandbox/abc-123/output",
                "Output dir: <output>",
            ),
            # Workdir path (no /output segment).
            (
                "Workspace: /tmp/pipeshub_sandbox/xyz-9/script.py",
                "Workspace: <workdir>/script.py",
            ),
            # Docker host path variant.
            (
                "Host path: /tmp/pipeshub_sandbox_docker/uuid-1/output/report.xlsx",
                "Host path: <output>/report.xlsx",
            ),
            # Docker container-internal output path (the real leak inside
            # containers since OUTPUT_DIR=/output there).
            (
                "Saved at: /output/10_jokes.pdf",
                "Saved at: <output>/10_jokes.pdf",
            ),
            (
                "writing to /output",
                "writing to <output>",
            ),
            # Multiple paths in one blob.
            (
                "Created /tmp/pipeshub_sandbox/a/output/x.pdf and /output/y.csv",
                "Created <output>/x.pdf and <output>/y.csv",
            ),
            # agent_loop_lib local coding-sandbox working dir + output subdir.
            (
                "Saved at: /tmp/alcs-3f9a1b2c4d/output/chart.png",
                "Saved at: <output>/chart.png",
            ),
            # agent_loop_lib local coding-sandbox workdir (no /output segment).
            (
                "Workspace: /tmp/alcs-3f9a1b2c4d/main.py",
                "Workspace: <workdir>/main.py",
            ),
            # agent_loop_lib Docker coding-sandbox working dir variant.
            (
                "Host path: /tmp/alcs-docker-9e8d7c6b5a/output/report.xlsx",
                "Host path: <output>/report.xlsx",
            ),
            # macOS realpath form of the agent_loop_lib workdir.
            (
                "Saved at: /private/tmp/alcs-docker-abc123/output/x.pdf",
                "Saved at: <output>/x.pdf",
            ),
        ],
    )
    def test_redacts_known_paths(self, raw: str, expected: str) -> None:
        assert redact_sandbox_paths(raw) == expected

    def test_idempotent(self) -> None:
        """Running the redactor twice yields the same result as once."""
        raw = (
            "Saved at: /tmp/pipeshub_sandbox/abc/output/foo.pdf; "
            "also /output/bar.csv; also /tmp/pipeshub_sandbox_docker/x/y.txt; "
            "also /tmp/alcs-abc/output/z.pdf; also /tmp/alcs-docker-xyz/w.txt"
        )
        once = redact_sandbox_paths(raw)
        twice = redact_sandbox_paths(once)
        assert once == twice

    def test_alcs_and_legacy_paths_in_one_blob(self) -> None:
        """The agent-loop and legacy sandbox paths can both appear in the
        same tool output (e.g. during the migration window) and must both
        be redacted independently."""
        raw = (
            "Legacy: /tmp/pipeshub_sandbox/a/output/legacy.pdf; "
            "agent-loop: /tmp/alcs-b/output/new.pdf"
        )
        assert redact_sandbox_paths(raw) == (
            "Legacy: <output>/legacy.pdf; agent-loop: <output>/new.pdf"
        )

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_and_none(self, value: str | None) -> None:
        assert redact_sandbox_paths(value) == ""

    def test_leaves_unrelated_paths_alone(self) -> None:
        """Paths that merely share a suffix must not be redacted."""
        raw = "Config at /etc/pipeshub/config.yaml; src at /home/user/project/output.py"
        assert redact_sandbox_paths(raw) == raw

    def test_does_not_clobber_output_inside_word(self) -> None:
        """``/some/output`` preceded by an alphanumeric char must stay intact."""
        raw = "Path is /my/output/file.txt and /etc/output"
        # Neither of the ``/output`` occurrences here is sandbox-related;
        # both are preceded by an alphanumeric char in the prior path
        # segment, so the bare-/output redactor must leave them alone.
        assert redact_sandbox_paths(raw) == raw
