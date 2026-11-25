"""MA1 (QA37-U4, U5, K3): the model a child run calls, on the table Node runs too (``child-model.json``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.events.child_spawned import ChildSpawnedData
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal import InMemoryJournal
from harness_core.journal.child_model import (
    ChildSpecModel,
    child_model_of,
    role_keys,
)
from harness_core.journal.children import (
    ChildGrantRequest,
    ChildRefusal,
    ChildRefused,
    ChildRequest,
    child_grants,
    child_spec,
)
from harness_core.testing import ManualClock

VECTORS = Path(__file__).resolve().parents[6] / "contracts/harness/vectors"
TABLE: dict[str, Any] = json.loads((VECTORS / "child-model.json").read_text(encoding="utf-8"))
PARENT_SPEC: dict[str, Any] = json.loads((VECTORS / "child-grants.json").read_text(encoding="utf-8"))["parentSpec"]

A = "anthropic:claude-x"
B = "openai:gpt-5.4-mini"
RESEARCHER: dict[str, Any] = {"role": "Researcher", "toolNames": []}


def _parent(policy: list[dict[str, Any]] | None = None, ext_model: dict[str, Any] | None = None) -> RunSpec:
    spec = json.loads(json.dumps(PARENT_SPEC))
    if policy is not None:
        spec["model"]["policy"] = policy
        if ext_model is None:
            del spec["extensions"]["x.pipeshub"]["model"]
        else:
            spec["extensions"]["x.pipeshub"]["model"] = ext_model
    return validate_contract(RunSpec, spec)


@pytest.mark.parametrize("case", TABLE["roleKeys"], ids=[json.dumps(c["subAgent"]) for c in TABLE["roleKeys"]])
def test_qa37_u4_role_keys_of_a_spawn(case: dict[str, Any]) -> None:
    assert list(role_keys(case["subAgent"])) == case["keys"]


@pytest.mark.parametrize("case", TABLE["cases"], ids=[c["id"] for c in TABLE["cases"]])
def test_qa37_u5_a_child_inherits_unless_its_role_sets_a_model(case: dict[str, Any]) -> None:
    chosen = child_model_of(case["roleModels"], case["subAgent"])
    if case["expect"].get("inherit"):
        assert chosen is None
    else:
        assert chosen is not None
        assert chosen.wire() == case["expect"]["model"]
        _ = ChildSpawnedData.model_validate(
            {"childRunId": "c1", "grantDigest": "sha256:" + "0" * 64, "model": chosen.wire()}
        )


@pytest.mark.parametrize("case", TABLE["specs"], ids=[c["id"] for c in TABLE["specs"]])
def test_qa37_k3_child_spec_model_fields_match_node(case: dict[str, Any]) -> None:
    parent = _parent(case["parent"]["policy"], case["parent"]["extModel"])
    grants = child_grants(parent, ChildGrantRequest(), 1).grants
    assert grants is not None
    asked = case["model"]
    model = ChildSpecModel(asked["provider"], asked["model"], asked["modelKey"]) if asked else None

    spec = child_spec(parent, grants, model=model)

    assert spec["model"]["policy"] == case["expect"]["policy"]  # type: ignore[index, call-overload]
    assert spec["extensions"]["x.pipeshub"].get("model") == case["expect"]["extModel"]  # type: ignore[index, call-overload, union-attr]
    _ = validate_contract(RunSpec, spec)


def test_qa37_k3_a_role_model_changes_nothing_else_in_the_spec() -> None:
    parent = _parent()
    grants = child_grants(parent, ChildGrantRequest(), 1).grants
    assert grants is not None
    inherited = child_spec(parent, grants)
    assigned = child_spec(parent, grants, model=ChildSpecModel("openai", "gpt-5.4-mini", "mk-b"))

    def rest(spec: dict[str, Any]) -> dict[str, Any]:
        out = json.loads(json.dumps(spec))
        del out["model"]["policy"]
        del out["extensions"]["x.pipeshub"]["model"]
        return out

    assert rest(assigned) == rest(inherited)  # type: ignore[arg-type]


class _Rig:
    def __init__(self, role_routes: dict[str, str] | None, configured: dict[str, str] | None = None) -> None:
        self.journal = InMemoryJournal(clock=ManualClock(), configured_models=configured)
        self.journal.admit(
            "p", org_id="org1", session_id="s", spec=_parent(), input_text="go", role_routes=role_routes
        )

    async def spawn(
        self, run_id: str, invocation: str, definition: dict[str, Any] | None, max_children: int | None = None
    ) -> str:
        claim = await self.journal.claim(run_id, worker_id="w")
        assert claim is not None
        admitted = await self.journal.spawn(
            claim.token, ChildRequest(invocation, "task", ChildGrantRequest(max_children=max_children), definition=definition)
        )
        return admitted.child_run_id

    async def spec_of(self, run_id: str) -> dict[str, Any]:
        [pinned] = [e for e in await self.journal.read(run_id) if e.type == "run.spec_pinned"]
        return dict(pinned.data)["spec"]  # type: ignore[return-value]

    async def spawned(self, stream: str) -> list[dict[str, Any]]:
        return [dict(e.data) for e in await self.journal.read(stream) if e.type == "child.spawned"]


async def test_qa37_u5_memory_journal_pins_the_roles_model_and_names_it_in_child_spawned() -> None:
    rig = _Rig({"researcher": B}, {B: "mk-b"})

    child = await rig.spawn("p", "t1.0", RESEARCHER)

    spec = await rig.spec_of(child)
    assert spec["model"]["policy"] == [{"provider": "openai", "model": "gpt-5.4-mini"}]
    assert spec["extensions"]["x.pipeshub"]["model"] == {"modelKey": "mk-b", "modelName": "gpt-5.4-mini"}
    [event] = await rig.spawned("s")
    assert event["model"] == {"provider": "openai", "model": "gpt-5.4-mini", "role": "researcher"}


async def test_qa37_u5_a_child_without_a_role_model_is_spawned_as_before() -> None:
    plain, with_roles = _Rig(None), _Rig({"researcher": B}, {B: "mk-b"})
    definition = {"role": "writer", "toolNames": []}

    child = await plain.spawn("p", "t1.0", definition)
    same = await with_roles.spawn("p", "t1.0", definition)

    assert await with_roles.spec_of(same) == await plain.spec_of(child)
    [event], [other] = await plain.spawned("s"), await with_roles.spawned("s")
    assert "model" not in event
    assert other == event


async def test_qa37_u5_a_grandchild_reads_the_root_definition_and_else_takes_its_parents_model() -> None:
    rig = _Rig({"researcher": B, "checker": A}, {A: "model-2", B: "mk-b"})
    child = await rig.spawn("p", "t1.0", RESEARCHER, max_children=1)

    inherits = await rig.spawn(child, "t1.0", {"role": "writer", "toolNames": []}, max_children=1)
    own = await rig.spawn(inherits, "t1.0", {"role": "checker", "toolNames": []})

    assert (await rig.spec_of(inherits))["model"]["policy"] == [{"provider": "openai", "model": "gpt-5.4-mini"}]
    assert (await rig.spec_of(own))["model"]["policy"] == [{"provider": "anthropic", "model": "claude-x"}]
    assert "model" not in (await rig.spawned(child))[0]
    assert (await rig.spawned(inherits))[0]["model"]["role"] == "checker"


async def test_qa37_u5_a_role_model_the_deployment_does_not_configure_refuses_the_spawn() -> None:
    rig = _Rig({"researcher": B}, {B: "mk-b"})
    del rig.journal.configured_models[B]

    claim = await rig.journal.claim("p", worker_id="w")
    assert claim is not None
    with pytest.raises(ChildRefused) as refused:
        _ = await rig.journal.spawn(claim.token, ChildRequest("t1.0", "task", definition=RESEARCHER))

    assert refused.value.reason is ChildRefusal.GRANT_TOO_LARGE
    assert "models.roles.researcher" in str(refused.value)
    assert rig.journal.children_of("p") == []
    assert await rig.spawned("s") == []


async def test_qa37_u5_role_models_survive_an_op_log_restart(tmp_path: Path) -> None:
    oplog = tmp_path / "journal.jsonl"
    first = InMemoryJournal(clock=ManualClock(), oplog=oplog, configured_models={B: "mk-b"})
    first.admit("p", org_id="org1", session_id="s", spec=_parent(), input_text="go", role_routes={"researcher": B})
    claim = await first.claim("p", worker_id="w")
    assert claim is not None
    child = await first.spawn(
        claim.token, ChildRequest("t1.0", "task", ChildGrantRequest(max_children=2), definition=RESEARCHER)
    )

    restarted = InMemoryJournal(clock=ManualClock(), oplog=oplog, configured_models={A: "model-2"})

    assert [e.data for e in await restarted.read("s")] == [e.data for e in await first.read("s")]
    assert [e.data["model"]["role"] for e in await restarted.read("s") if e.type == "child.spawned"] == ["researcher"]  # type: ignore[index, call-overload]
    grand = await restarted.claim(child.child_run_id, worker_id="w")
    assert grand is not None
    # The restarted journal holds the root's role routes: B is no longer configured, so the role is refused.
    with pytest.raises(ChildRefused) as refused:
        _ = await restarted.spawn(grand.token, ChildRequest("t1.0", "task", definition=RESEARCHER))
    assert refused.value.reason is ChildRefusal.GRANT_TOO_LARGE
    restarted.configured_models[B] = "mk-b"
    assert (await restarted.spawn(grand.token, ChildRequest("t1.0", "task", definition=RESEARCHER))).created
