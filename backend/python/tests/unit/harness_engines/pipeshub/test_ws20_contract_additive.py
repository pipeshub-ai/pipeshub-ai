"""WS-20 (OWD-6, C3.01, C3.13): the ``/pipeshub`` contract changes additively only within a version.

``contract_v1_surface.json`` is the pre-registered surface of contract 1: the image's folder (each path's kind, link
target and mode, ``AGENTS.md`` read-only), the per-turn session folder's fixed names, every ``ph`` command with its
arguments and flags (``--json`` on every verb), the ``--json`` error codes and a tool's ``<tool>.schema.json`` shape.
The built contract must keep every name in it unchanged; it may add. A removal, a rename, a changed link, mode, flag
kind or exit code, or a new required argument of an existing command is a new contract version (a new golden beside
this one), never an edit of this file.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from harness_engines import pipeshub
from harness_engines.pipeshub import CONTRACT_VERSION
from harness_engines.pipeshub.surface import contract_surface, non_additive

PACKAGE = Path(pipeshub.__file__).parent
GOLDEN = Path(__file__).with_name("contract_v1_surface.json")


@pytest.fixture
def current(tmp_path: Path) -> dict[str, Any]:
    """The surface of the contract as built: ``install.sh`` under a temporary root, read back."""
    subprocess.run(
        ["sh", str(PACKAGE / "install.sh")], env={"DESTDIR": str(tmp_path), "PATH": "/usr/bin:/bin"}, check=True, timeout=60
    )
    return contract_surface(tmp_path)


def _golden() -> dict[str, Any]:
    return json.loads(GOLDEN.read_text())


def test_ws20_the_golden_is_contract_1_and_names_every_part() -> None:
    golden = _golden()
    assert golden["contract"] == CONTRACT_VERSION == 1
    assert set(golden) == {"contract", "layout", "session", "ph", "exitCodes", "schemaFile"}
    assert golden["layout"]["AGENTS.md"] == {"kind": "file", "mode": "0444"}
    assert golden["layout"]["VERSION"] == {"kind": "file", "mode": "0444"}
    # Every verb takes --json (the global flag; a verb's own --json, except call where it carries the arguments).
    assert golden["ph"]["ph"]["options"]["--json"]["action"] == "store_true"
    for name, command in golden["ph"].items():
        if name in ("ph", "tools", "skill", "policy", "git", "inputs"):
            continue
        assert "--json" in command["options"], name
    assert golden["schemaFile"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_ws20_the_built_contract_keeps_every_name_of_contract_1(current: dict[str, Any]) -> None:
    violations = non_additive(_golden(), current)
    assert violations == [], (
        "a non-additive change to /pipeshub contract 1: keep the old name as an alias, or start contract 2 "
        f"with its own golden: {violations}"
    )


def test_ws20_an_addition_keeps_the_contract(current: dict[str, Any]) -> None:
    grown = copy.deepcopy(current)
    grown["layout"]["memory"] = {"kind": "link", "target": "session/memory"}
    grown["session"]["session.json"] = {"kind": "file"}
    grown["ph"]["memory list"] = {"positionals": {}, "required": 0, "options": {"--json": {"action": "store_true"}}}
    grown["ph"]["tools search"]["options"]["--toolset"] = {"action": "store"}
    grown["ph"]["tools search"]["positionals"]["extra"] = {"nargs": "?"}
    grown["exitCodes"]["73"] = "cannot_create"
    grown["schemaFile"]["examples"] = "array"
    assert non_additive(_golden(), grown) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda s: s["layout"].pop("outputs"), "/layout/outputs: removed"),
        (lambda s: s["layout"].__setitem__("out", s["layout"].pop("outputs")), "/layout/outputs: removed"),
        (lambda s: s["layout"]["tools"].__setitem__("target", "session/tool"), "/layout/tools/target: changed"),
        (lambda s: s["layout"]["AGENTS.md"].__setitem__("mode", "0644"), "/layout/AGENTS.md/mode: changed"),
        (lambda s: s["session"].pop("gateway.json"), "/session/gateway.json: removed"),
        (lambda s: s["ph"].pop("call"), "/ph/call: removed"),
        (lambda s: s["ph"]["tools search"]["options"].pop("--limit"), "/ph/tools search/options/--limit: removed"),
        (
            lambda s: s["ph"]["tools search"]["options"]["--limit"].__setitem__("type", "str"),
            "/ph/tools search/options/--limit/type: changed",
        ),
        (lambda s: s["ph"]["fetch"]["positionals"]["record_ids"].__setitem__("nargs", "1"), "/ph/fetch/positionals"),
        (lambda s: s["ph"]["tools list"].__setitem__("required", 1), "/ph/tools list/required: changed"),
        (lambda s: s["exitCodes"].__setitem__("75", "pending"), "/exitCodes/75: changed"),
        (lambda s: s.__setitem__("contract", 2), "/contract: changed"),
        (lambda s: s["schemaFile"].pop("title"), "/schemaFile/title: removed"),
        (lambda s: s.__setitem__("ph", []), "/ph: changed"),
    ],
)
def test_ws20_a_removal_rename_or_changed_name_breaks_the_contract(
    current: dict[str, Any], change: Any, expected: str  # noqa: ANN401
) -> None:
    broken = copy.deepcopy(current)
    change(broken)
    violations = non_additive(_golden(), broken)
    assert any(v.startswith(expected) for v in violations), violations
