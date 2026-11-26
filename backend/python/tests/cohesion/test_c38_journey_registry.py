"""C-38 (R31-JOURNEY, ADR-TEST-01): a new tool source, gateway tool, executor kind, run kind, surface, via or engine is a
registry entry.

The owner found the 2026-11-12 break because nothing enumerated the paths a run can take against the account shapes
people have. The feature registry (docs/test-plan/data/journeys.json) does, and it stays whole only if the code cannot
grow a dimension behind its back: each enumeration below is read from where the code defines it, and every value must
be named in the registry's ``codeKeys``, mapped to features or paths that exist, or given the reason it is none. A key
the code no longer has fails too, so the map only says what is true.
"""

from __future__ import annotations

import ast
import json
import sys
from functools import cache

import pytest

from tests.cohesion.support import PYTHON_ROOT, REPO_ROOT

REGISTRY = REPO_ROOT / "docs/test-plan/data/journeys.json"
CONTRACTS = REPO_ROOT / "contracts/harness"


@cache
def registry() -> dict:
    return json.loads(REGISTRY.read_text())


def tool_sources() -> set[str]:
    """The classes ``default_sources()`` registers: every tool a harness run is offered comes from one of them."""
    tree = ast.parse((PYTHON_ROOT / "app/harness/assembly/tool_sources.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "default_sources")
    return {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}


def _module_constant(module: str, name: str) -> str:
    import importlib

    value = getattr(importlib.import_module(module), name)
    assert isinstance(value, str), f"{module}.{name} is not a tool name"
    return value


def gateway_tools() -> set[str]:
    """R32-REPO: every tool an engine's Tool Gateway serves besides the run's granted catalog (``default_sources()``),
    read where the code adds it: the extras ``_runtime`` puts in the gateway's ``served`` map (``pipeshub@git``'s
    ``repository_fetch`` was one no feature named), every tool ``ph`` calls through the gateway (its ``_called``
    commands), the gateway's own question tool, its disclosure meta-tools and the managed hook's policy check."""
    import importlib

    found: set[str] = set()
    durable = PYTHON_ROOT / "app/harness/engines/durable.py"
    module = importlib.import_module("app.harness.engines.durable")
    for node in ast.walk(ast.parse(durable.read_text())):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == "served":
                    assert isinstance(target.slice, ast.Name), "served[...] is keyed by a named constant"
                    found.add(getattr(module, target.slice.id))
        if (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "served"
                and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)):
            found |= set(getattr(module, node.value.func.id)())
    ph = PYTHON_ROOT / "harness_engines/pipeshub/ph.py"
    for node in ast.walk(ast.parse(ph.read_text())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_called"
                and len(node.args) > 1 and isinstance(node.args[1], ast.Name) and node.args[1].id.isupper()):
            found.add(_module_constant("harness_engines.pipeshub.ph", node.args[1].id))
    from harness_core.gateway.asking import ASK_USER_NAMES

    found |= set(ASK_USER_NAMES)
    found |= {_module_constant("harness_core.tools.call_tool", n) for n in ("SEARCH_TOOLS", "CALL_TOOL")}
    found.add(_module_constant("harness_core.engines.native_policy", "POLICY_CHECK_TOOL"))
    return found


def executor_kinds() -> set[str]:
    spec = json.loads((CONTRACTS / "core/run-spec.schema.json").read_text())["$defs"]
    refs = [r["$ref"].rsplit("/", 1)[-1] for r in spec["Executor"]["anyOf"]]
    return {spec[r]["properties"]["kind"]["const"] for r in refs}


def run_kinds() -> set[str]:
    return set(json.loads((CONTRACTS / "core/common.schema.json").read_text())["$defs"]["RunKind"]["enum"])


def extension_enum(field: str) -> set[str]:
    return set(json.loads((CONTRACTS / "ext/pipeshub/run-spec.schema.json").read_text())["properties"][field]["enum"])


def engines() -> set[str]:
    return {e["kind"] for e in json.loads((PYTHON_ROOT / "harness_engines/allowlist.json").read_text())["engines"]}


ENUMERATIONS = {
    "toolSources": tool_sources,
    "gatewayTools": gateway_tools,
    "executorKinds": executor_kinds,
    "runKinds": run_kinds,
    "surfaces": lambda: extension_enum("surface"),
    "via": lambda: extension_enum("via"),
    "engines": engines,
}


@pytest.mark.parametrize("key", sorted(ENUMERATIONS))
def test_c38_every_value_the_code_enumerates_is_in_the_registry_and_nothing_else(key: str) -> None:
    code = ENUMERATIONS[key]()
    mapped = set(registry()["codeKeys"][key])
    assert code, f"read no {key} from the code: the scan is stale"
    assert code - mapped == set(), (
        f"{key} the code has and journeys.json's codeKeys does not: {sorted(code - mapped)}. Add each to the registry "
        "(a feature or path its runs take, with its cells) or give the reason it is none (ADR-TEST-01 D3)")
    assert mapped - code == set(), f"{key} journeys.json maps that the code no longer has: {sorted(mapped - code)}"


@pytest.mark.parametrize("key", sorted(ENUMERATIONS))
def test_c38_each_mapping_names_a_feature_or_path_that_exists_or_says_why_none(key: str) -> None:
    reg = registry()
    known = {f["id"] for f in reg["features"]} | {p["id"] for p in reg["paths"]}
    for value, target in reg["codeKeys"][key].items():
        if isinstance(target, dict):
            assert set(target) == {"none"} and target["none"].strip(), f"{key} {value}: a dict maps to its reason"
            continue
        names = [target] if isinstance(target, str) else target
        assert names, f"{key} {value} maps to nothing"
        assert not set(names) - known, f"{key} {value}: {sorted(set(names) - known)} are not features or paths"


def test_c38_every_engine_has_its_job_path() -> None:
    paths = {p["id"] for p in registry()["paths"]}
    assert {f"job.{e}" for e in engines()} <= paths


def test_c38_the_registry_check_passes() -> None:
    """journey_matrix.py --check: every feasible cell covered or a tracked gap, every named test real."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import journey_matrix

    m = journey_matrix.Matrix(journey_matrix.load(), journey_matrix.runner_literals())
    assert journey_matrix.shape_problems(m.data) + journey_matrix.problems(m) == []
