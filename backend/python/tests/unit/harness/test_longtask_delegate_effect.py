"""LONGTASK-F2: in-sandbox code never asks; a package install asks at the call, with its package names.

A react chat reaches code only through ``coding_agent``. Its effect was "an unknown external write" whenever any
tool it may call was not a read, so once the run had read anything the flow rule held every ``coding_agent`` call:
code that ``run_code`` runs without asking (CODEAUTO, owner 2026-10-18) asked through its delegate, from the second
turn of every react chat on, because the delegate *could* call ``install_packages``.

Now a delegate can do what its tools can do (the strongest effect, the furthest sink), and on the durable loop
``install_packages`` is not claimed by ``coding_agent``: the run calls it itself, so the kernel asks at that call,
under the flow rule, with the packages in the card.
"""

from __future__ import annotations

import pytest

from app.harness.assembly.assembler import Catalog
from app.harness.assembly.tool_sources import PLATFORM, SourcedTool
from harness_core.effects.policy import in_own_sandbox
from harness_core.provenance.rules import NO_CONTENT_RULE
from tests.unit.harness.test_effect_tags_complete import mcp, sandbox

CODE = ("run_code", "read_sandbox_file")


def _effects(delegates: dict[str, tuple[str, ...]]) -> dict[str, object]:
    from app.agent_loop_lib.tools.registry import ToolRegistry

    registry = ToolRegistry()
    tools = [*sandbox(), *mcp()]
    for tool in tools:
        registry.register_tool(tool)
    listed = tuple(SourcedTool(t.name, f"{PLATFORM}@x/{t.name}", None, provenance=NO_CONTENT_RULE) for t in tools)
    return Catalog(registry, listed).with_delegates(delegates).effects()  # type: ignore[return-value]


def test_a_delegate_that_only_runs_code_in_its_sandbox_never_asks() -> None:
    effects = _effects({"coding_agent": CODE})
    delegate, run_code = effects["coding_agent"], effects["run_code"]
    assert delegate.effect == "execute" and delegate.sink == run_code.sink  # type: ignore[attr-defined]
    assert in_own_sandbox(delegate)  # type: ignore[arg-type]


@pytest.mark.parametrize("nested", [False, True])
def test_a_delegate_that_may_install_packages_or_write_outside_is_an_external_call(nested: bool) -> None:
    for extra in ("install_packages", mcp()[0].name):
        delegates = {"inner": (*CODE, extra), "outer": ("inner",)} if nested else {"outer": (*CODE, extra)}
        outer = _effects(delegates)["outer"]
        assert (outer.sink.egress, outer.sink.args_leave_boundary) == ("external", True), extra  # type: ignore[attr-defined]
        assert not outer.idempotent  # type: ignore[attr-defined]


def test_a_delegate_with_a_tool_outside_the_catalog_stays_an_unknown_write() -> None:
    stray = _effects({"stray": (*CODE, "not_in_the_catalog")})["stray"]
    assert (stray.effect, stray.sink.egress, stray.idempotent) == ("write", "external", False)  # type: ignore[attr-defined]


def test_a_read_only_delegate_stays_a_read() -> None:
    assert _effects({"reader": ("read_sandbox_file",)})["reader"].effect == "read"  # type: ignore[attr-defined]


def test_on_the_durable_loop_coding_agent_leaves_install_packages_to_the_run() -> None:
    from app.harness.assembly.loop import durable_domain_definitions

    coding = next(d for d in durable_domain_definitions() if d.name == "coding_agent")
    assert "install_packages" not in coding.tool_names and "run_code" in coding.tool_names
