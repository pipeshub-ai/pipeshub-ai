"""SYNCMAIN2: what a durable run carries from main's #4428 (model-call identity).

A durable attempt runs outside any request, so main's route-level bindings do not reach it: the assembler binds the
run's actor for its model calls.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.harness.assembly.model_identity import bind_run_identity
from app.services.model_identity.call_context import peek_call_context
from app.services.model_identity.principal import PrincipalKind
from app.services.model_identity.runtime import IdentityRuntime, set_identity_runtime
from app.services.model_identity.workload import Workload

ORG = "org-sm2"
ALICE = "user-alice"


def _run(*, chat: bool = False, agent_key: str | None = "agent-1", via: str = "session") -> Any:  # noqa: ANN401
    return SimpleNamespace(
        actor_id=ALICE,
        org_id=ORG,
        session_id="session-1",
        chat=object() if chat else None,
        agent=SimpleNamespace(agent_key=agent_key) if agent_key is not None else None,
        via=via,
    )


@pytest.fixture(autouse=True)
def _runtime() -> Any:  # noqa: ANN401
    set_identity_runtime(IdentityRuntime(None))
    yield
    set_identity_runtime(None)


def _bound_in_own_task(run: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    async def attempt() -> Any:  # noqa: ANN401
        await bind_run_identity(run, **kwargs)
        return peek_call_context()

    return asyncio.run(attempt())


def test_an_agent_runs_model_calls_as_its_actor() -> None:
    ctx = _bound_in_own_task(_run(), email="alice@example.com")
    assert ctx.principal.kind is PrincipalKind.USER
    assert (ctx.principal.subject_id, ctx.principal.org_id, ctx.principal.email) == (ALICE, ORG, "alice@example.com")
    assert ctx.workload is Workload.AGENT
    assert (ctx.conversation_id, ctx.agent_id) == ("session-1", "agent-1")


def test_a_chat_run_is_a_chat_workload() -> None:
    ctx = _bound_in_own_task(_run(chat=True, agent_key=None))
    assert ctx.workload is Workload.CHAT
    assert ctx.agent_id is None


def test_the_binding_stays_in_the_attempts_task() -> None:
    _bound_in_own_task(_run())
    assert peek_call_context() is None


def test_a_policy_that_cannot_be_read_still_names_the_actor() -> None:
    class Broken(IdentityRuntime):
        async def context(self, *args: object, **kwargs: object) -> Any:  # noqa: ANN401
            raise RuntimeError("policy store down")

    set_identity_runtime(Broken(None))
    ctx = _bound_in_own_task(_run())
    assert ctx.principal.subject_id == ALICE
    assert ctx.workload is Workload.AGENT
