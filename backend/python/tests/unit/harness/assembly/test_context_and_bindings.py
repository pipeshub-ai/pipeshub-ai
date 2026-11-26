"""D-2: RunContext reaches no credential. D-6: the durable runtime never inherits legacy globals."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import typing
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_args, get_origin

import pytest
from pydantic import BaseModel

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.bindings import durable_bindings
from app.harness.assembly.context import RunContext
from harness_core import bindings
from harness_core.driver import RunDriver
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.assembly.model import ResolvedModel

_SECRET_NAME = re.compile(r"token|secret|passw|credential|auth|api_?key|cookie|bearer|private", re.I)
_ALLOWED_LEAVES = (str, int, float, bool, type(None))
PYTHON_ROOT = Path(__file__).resolve().parents[4]


def _leaf_types(annotation: Any, seen: set[type]) -> list[Any]:  # noqa: ANN401
    origin = get_origin(annotation)
    if origin is not None:
        return [leaf for arg in get_args(annotation) for leaf in _leaf_types(arg, seen)]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return []
        seen.add(annotation)
        return [
            leaf
            for name, field in annotation.model_fields.items()
            for leaf in [("field", annotation.__name__, name), *_leaf_types(field.annotation, seen)]
        ]
    return [annotation]


def _contract_detail(leaf: Any) -> bool:  # noqa: ANN401
    """What the run's pin (a generated contract model) adds to the walk: a literal's values, a field's
    constraints, and the JSON value of a policy dimension. Data, none of it an object."""
    constant = leaf is None or isinstance(leaf, str | int | float | bool)
    constraint = type(leaf).__module__.split(".")[0] in {"annotated_types", "pydantic"}
    return constant or constraint or leaf is Any


def test_d2_run_context_has_no_attribute_path_to_a_credential() -> None:
    hints = _leaf_types(RunContext, set())
    fields = [h for h in hints if isinstance(h, tuple)]
    leaves = [h for h in hints if not isinstance(h, tuple)]
    assert fields, "introspection found no fields"
    assert not [f for f in fields if _SECRET_NAME.search(f[2])]
    # Only plain data: no service, client, store or context object is reachable.
    json_value = typing.get_args(RunContext.model_fields["capabilities"].annotation)[1]
    allowed = (*_ALLOWED_LEAVES, json_value, Ellipsis)
    assert [leaf for leaf in leaves if leaf not in allowed and not _contract_detail(leaf)] == []
    assert RunContext.model_config.get("frozen") is True


def test_d2_a_run_context_built_from_a_real_claim_carries_no_secret_value() -> None:
    run = RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[f"jira@{w.JIRA}/search_issues"])))
    dumped = json.dumps(run.model_dump(mode="json"))
    assert "secret" not in dumped and "phw_" not in dumped
    assert run.tool_ids == (f"jira@{w.JIRA}/search_issues",)
    assert run.agent is not None and run.agent.agent_key == w.AGENT


def test_the_knowledge_scope_carries_plain_ids() -> None:
    # Id is a pydantic RootModel: str() of it is "root='kb-1'", which filters on nothing.
    spec = w.pinned_spec(tool_ids=[], kb=["kb-1", "kb-2"], apps=["app-1"])
    run = RunContext.from_claim(w.claim(spec))
    assert run.knowledge.kb == ("kb-1", "kb-2")
    assert run.knowledge.apps == ("app-1",)


def test_d6_durable_bindings_are_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    durable = durable_bindings()
    assert durable.event_deriver is None
    # PA-14: final_answer streams its answer as legacy does, when enabled.
    assert durable.streamed_answer_argument is not None
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "true")
    assert durable.streamed_answer_argument("final_answer") == "answer_markdown"
    assert durable.streamed_answer_argument("jira__search_issues") is None
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    assert durable.streamed_answer_argument("final_answer") is None
    assert durable.spawn_scheduler is None
    assert durable.special_route_fallback is None
    assert durable.invalid_tool_call_recovery is not None
    assert durable.api_mode_store is not None


@pytest.fixture
def _legacy_installed() -> Iterator[bindings.KernelBindings]:
    import app.agent_loop_lib.composition as legacy

    previous = bindings.current()
    legacy.install()
    try:
        yield bindings.current()
    finally:
        bindings.install(previous)


async def test_d6_a_durable_run_never_sees_the_legacy_bindings(
    _legacy_installed: bindings.KernelBindings,
) -> None:
    legacy = _legacy_installed
    assert legacy.event_deriver is not None and legacy.spawn_scheduler is not None
    seen: dict[str, bindings.KernelBindings] = {}

    port = ScriptedModelPort(
        ScriptedModel(
            ModelScript(
                id="d6",
                turns=[
                    Turn(reply=Reply(tool_calls=[ToolCallSpec(name="jira__search_issues", args={"jql": "x"})])),
                    Turn(reply=Reply(text="done")),
                ],
            )
        )
    )
    build_model = w.model_factory(port)

    async def model_factory(run: RunContext, services: object) -> ResolvedModel:
        seen["build"] = bindings.current()
        return await build_model(run, services)

    original = w.FakeJira.search_issues

    async def search_issues(self: w.FakeJira, jql: str) -> str:
        seen["tool"] = bindings.current()
        return await original(self, jql)

    with w.deployment(), pytest.MonkeyPatch.context() as patched:
        w.replace_tool(patched, w.FakeJira, "search_issues", search_issues)
        clock = ManualClock()
        journal = w.pipeshub_journal(clock=clock)
        spec = w.pinned_spec(tool_ids=[f"jira@{w.JIRA}/search_issues"])
        journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=spec, input_text="q")
        assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=model_factory)

        class _Attach:
            async def build(self, claim: Any) -> Any:  # noqa: ANN401
                from dataclasses import fields

                node = w.claim(claim.spec)
                values = {f.name: getattr(claim, f.name) for f in fields(claim)}
                return await assembler.build(type(node)(**values, run=node.run))

        driver = RunDriver(journal=journal, assembler=_Attach(), clock=clock)
        claim = await journal.claim("run-1", worker_id="w")
        assert claim is not None
        done = await driver.run_claimed(claim)
    assert done.status == "completed"
    assert set(seen) == {"build", "tool"}
    for scope in seen.values():
        assert scope is not legacy
        assert scope.event_deriver is None and scope.spawn_scheduler is None
    assert bindings.current() is legacy


_PROBE = """
import json, sys
order = sys.argv[1]
if order == "legacy-first":
    import app.agent_loop_lib  # noqa: F401 - installs the legacy bindings
from app.harness.assembly.bindings import durable_bindings
if order == "durable-first":
    import app.agent_loop_lib  # noqa: F401
from harness_core import bindings
d = durable_bindings()
g = bindings.current()
def name(f):
    return None if f is None else f"{f.__module__}.{getattr(f, '__qualname__', type(f).__name__)}"
print(json.dumps({
    "durable": {k: name(getattr(d, k)) for k in ("event_deriver", "streamed_answer_argument", "spawn_scheduler", "special_route_fallback", "invalid_tool_call_recovery", "api_mode_store")},
    "legacy_installed": g.event_deriver is not None,
}))
"""


def test_d6_neither_import_order_changes_the_durable_bindings() -> None:
    results = []
    for order in ("legacy-first", "durable-first"):
        out = subprocess.run(
            [sys.executable, "-c", _PROBE, order],
            cwd=PYTHON_ROOT,
            capture_output=True,
            text=True,
            check=True,
            env={"PATH": "", "SECRET_KEY": "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881", "PYTHONPATH": str(PYTHON_ROOT)},
        )
        results.append(json.loads(out.stdout.strip().splitlines()[-1]))
    assert results[0]["durable"] == results[1]["durable"]
    assert results[0]["durable"]["event_deriver"] is None
    assert results[0]["durable"]["invalid_tool_call_recovery"].startswith("app.harness.assembly.bindings")
    assert all(result["legacy_installed"] for result in results)
