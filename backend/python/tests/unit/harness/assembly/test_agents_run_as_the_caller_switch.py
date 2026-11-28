"""``app.edition.AGENTS_RUN_AS_THE_CALLER``: a build that sets it runs every agent, a service-account one too, as
the identified person asking. A build that leaves it off keeps a service-account agent's own identity
(``test_service_account_identity.py``)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.routes.agent  # noqa: F401 - mounted before the fixture sets the switch, which its routes read at import
from app import edition
from app.harness.assembly.context import RunContext
from app.harness.assembly.tool_environment import _identity
from harness_core.driver import RunRefused
from tests.unit.harness.assembly.world import ORG, agent_doc, claim, pinned_spec

PERSON = {"_key": "key-alice", "userId": "user-alice", "orgId": ORG, "email": "alice@corp.example", "fullName": "Alice"}


@pytest.fixture(autouse=True)
def _as_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition, "AGENTS_RUN_AS_THE_CALLER", True)


def _graph() -> MagicMock:
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(return_value=dict(PERSON))
    graph.get_document = AsyncMock(return_value=dict(PERSON))
    graph.check_agent_permission = AsyncMock(return_value=None)
    return graph


async def test_a_service_identity_starts_no_run() -> None:
    run = RunContext.from_claim(claim(pinned_spec(tool_ids=[]))).model_copy(update={"via": "service"})
    graph = _graph()
    with pytest.raises(RunRefused) as refused:
        await _identity(run, agent_doc(isServiceAccount=True), graph, logging.getLogger("t"))
    assert refused.value.error_type == "unidentified_caller"
    graph.get_user_by_user_id.assert_not_awaited()


async def test_a_service_account_agent_runs_as_the_person_asking() -> None:
    run = RunContext.from_claim(claim(pinned_spec(tool_ids=[])))
    graph = _graph()
    info, service_account = await _identity(run, agent_doc(isServiceAccount=True), graph, logging.getLogger("t"))
    assert service_account is False
    assert info["userId"] == run.actor_id
    graph.check_agent_permission.assert_not_awaited()  # the org's agent: the run-definition route checked its org


def test_with_the_switch_set_the_run_definition_route_refuses_the_slack_service_token() -> None:
    """The route reads the switch when it is mounted, so it is checked in a process of its own."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    script = textwrap.dedent("""
        import asyncio
        from types import SimpleNamespace
        from app import edition
        edition.AGENTS_RUN_AS_THE_CALLER = True
        from fastapi import HTTPException
        from app.api.middlewares.auth import AUTH_POLICY_ATTR
        from app.api.routes.agent import router
        [route] = [r for r in router.routes if getattr(r, "path", "") == "/{agent_id}/run-definition"]
        user = {"orgId": "org-1", "userId": "5f0c0ffee0ddba11ab5e0001", "token_type": "scoped",
                "scopes": ["conversation:create"], "isServiceAccount": True}
        async def check():
            for dependency in route.dependant.dependencies:
                if getattr(dependency.call, AUTH_POLICY_ATTR, None) is not None:
                    await dependency.call(SimpleNamespace(state=SimpleNamespace(user=user)))
        try:
            asyncio.run(check())
        except HTTPException as exc:
            print("status", exc.status_code)
        else:
            print("admitted")
    """)
    root = Path(__file__).resolve().parents[4]
    done = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=300,
                          env={**__import__("os").environ, "PYTHONPATH": str(root)}, check=False)
    assert done.stdout.strip().splitlines()[-1:] == ["status 403"], done.stderr[-2000:]
