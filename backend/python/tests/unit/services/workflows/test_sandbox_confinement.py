"""Confinement guarantees of the subprocess that runs generated workflow code.

Two things stop generated code from becoming a credential-exfiltration or
cross-tenant-read primitive: the child's environment is an allowlist (not a
deny-list, which silently inherits every new secret anyone adds), and the
journal RPC uses the HOST's run id rather than whatever the child claims.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from app.services.workflows.domain.models import (
    JournalEntry,
    ResultRef,
    StepOutcome,
)
from app.services.workflows.interface.broker import RunPrincipal
from app.services.workflows.runtime.sandbox import (
    WorkflowToolBridge,
    _apply_os_limits,
)

_SECRETS = {
    "OPENAI_API_KEY": "sk-real",
    "ANTHROPIC_API_KEY": "sk-ant",
    "JWT_SECRET": "jwt",
    "AWS_SECRET_ACCESS_KEY": "aws",
    "ARANGO_PASSWORD": "arango",
    "REDIS_URL": "redis://host",
}


class TestChildEnvironment:
    def test_excludes_provider_and_infrastructure_secrets(self) -> None:
        with patch.dict(os.environ, {**_SECRETS, "PATH": "/usr/bin"}, clear=True):
            env = WorkflowToolBridge._make_env()

        for name in _SECRETS:
            assert name not in env, f"{name} leaked into the workflow subprocess"
        assert env["PATH"] == "/usr/bin"

    def test_is_an_allowlist_so_a_newly_added_secret_is_excluded_by_default(self) -> None:
        """The regression this guards: a deny-list passes anything nobody
        thought to add to it, which is exactly how the next credential leaks."""
        with patch.dict(os.environ, {"SOME_FUTURE_TOKEN": "x", "HOME": "/root"}, clear=True):
            env = WorkflowToolBridge._make_env()

        assert env["HOME"] == "/root"
        assert "SOME_FUTURE_TOKEN" not in env

    def test_keeps_the_interpreter_essentials(self) -> None:
        essentials = {"PATH": "/bin", "HOME": "/h", "LANG": "C", "TMPDIR": "/tmp"}
        with patch.dict(os.environ, essentials, clear=True):
            env = WorkflowToolBridge._make_env()
        for k, v in essentials.items():
            assert env[k] == v
        assert "PYTHONPATH" in env, "app root must always be on PYTHONPATH"


class TestOsLimits:
    def test_sets_cpu_memory_file_and_core_limits(self) -> None:
        import resource

        applied: list[tuple[int, tuple[int, int]]] = []
        with patch.object(resource, "setrlimit", side_effect=lambda r, v: applied.append((r, v))):
            _apply_os_limits()

        limited = {r for r, _ in applied}
        assert resource.RLIMIT_CPU in limited
        assert resource.RLIMIT_NOFILE in limited
        assert resource.RLIMIT_AS in limited
        # No core dumps: a crash must not spill tenant data to disk.
        assert (resource.RLIMIT_CORE, (0, 0)) in applied

    def test_a_platform_that_rejects_a_limit_does_not_break_launch(self) -> None:
        import resource

        with patch.object(resource, "setrlimit", side_effect=ValueError("unsupported")):
            _apply_os_limits()  # must not raise


class _RecordingJournal:
    def __init__(self) -> None:
        self.appended: list[JournalEntry] = []
        self.looked_up: list[tuple[str, str]] = []

    async def lookup(self, run_id: str, step_key: str) -> JournalEntry | None:
        self.looked_up.append((run_id, step_key))
        return None

    async def append(self, entry: JournalEntry) -> None:
        self.appended.append(entry)


class _NullBroker:
    async def dispatch(self, call: Any, principal: Any) -> Any:  # noqa: ARG002
        raise AssertionError("not used")


def _bridge(journal: _RecordingJournal) -> WorkflowToolBridge:
    return WorkflowToolBridge(
        broker=_NullBroker(),
        principal=RunPrincipal(org_id="org-1", user_id="u-1", run_id="run-HOST"),
        journal=journal,
        working_dir="/tmp",
    )


class TestJournalRpcTrustBoundary:
    @pytest.mark.asyncio
    async def test_lookup_ignores_a_child_supplied_run_id(self) -> None:
        journal = _RecordingJournal()
        await _bridge(journal)._handle_journal_lookup(
            {"run_id": "run-VICTIM", "step_key": "ctx.tool:x#0"},
        )
        assert journal.looked_up == [("run-HOST", "ctx.tool:x#0")]

    @pytest.mark.asyncio
    async def test_append_is_stamped_with_the_host_run_id(self) -> None:
        journal = _RecordingJournal()
        response = await _bridge(journal)._handle_journal_append({
            "entry": {
                "run_id": "run-VICTIM",
                "step_key": "ctx.tool:x#0",
                "entry_kind": "tool",
                "outcome": "succeeded",
                "result_ref": ResultRef(inline={"forged": True}).model_dump(),
            },
        })

        assert response["ok"] is True
        assert [e.run_id for e in journal.appended] == ["run-HOST"]
        assert journal.appended[0].outcome == StepOutcome("succeeded")
