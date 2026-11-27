"""``app.edition.AGENTS_RUN_AS_THE_CALLER``: a build that sets it runs every agent, a service-account one too, as
the identified person asking. A build that leaves it off keeps a service-account agent's own identity
(``test_service_account_identity.py``)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

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
