"""A run's knowledge tools search as the run's org: the worker binds it (``run_org_context``), and the knowledge
call pins its principal to that binding, not to anything in the tool state. A run built without the binding has no
org, and the vector filter refuses a blank org, so every search and listing fails."""

from __future__ import annotations

import dataclasses
from typing import Any

from app.core import org_context
from app.harness.assembly.assembler import run_org_context
from app.harness.knowledge.knowledge_service import KnowledgeCall
from app.harness.worker.claimer import RUN_JOB_SOURCE


def _run(*, actor: str) -> Any:  # noqa: ANN401 - RunContext
    from app.harness.assembly.context import RunContext
    from tests.unit.harness.assembly import world as w

    claim = w.claim(w.pinned_spec(tool_ids=[], apps=["app-1"], kb=[], strict_scope=False, agent=True), query="q")
    run = claim.run.model_copy(update={"org_id": "org-run", "actor_id": actor})
    return RunContext.from_claim(dataclasses.replace(claim, org_id="org-run", run=run))


def test_a_run_acts_as_its_org_and_person() -> None:
    run = _run(actor="user-1")
    ctx = run_org_context(run)
    assert (ctx.org_id, ctx.user_id, ctx.kind, ctx.source, ctx.request_id) == (
        "org-run", "user-1", "user", RUN_JOB_SOURCE, run.run_id,
    )


def test_a_run_with_no_person_acts_as_a_service() -> None:
    ctx = run_org_context(_run(actor=""))
    assert (ctx.org_id, ctx.user_id, ctx.kind) == ("org-run", None, "service")


def test_a_knowledge_call_in_the_run_searches_as_the_runs_org() -> None:
    state = {"org_id": "org-other", "user_id": "user-1"}
    with org_context.bind(run_org_context(_run(actor="user-1"))):
        assert KnowledgeCall.from_state(state).principal.org_id == "org-run"


def test_a_knowledge_call_outside_a_run_has_no_org() -> None:
    assert KnowledgeCall.from_state({"org_id": "org-other"}).principal.org_id == ""
