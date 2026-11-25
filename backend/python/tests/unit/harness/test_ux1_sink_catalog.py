# pyright: reportPrivateUsage=false
"""UX1 CL-3..CL-7, CL-10: on the real tool catalog, the flow rule may ask only before a write that leaves the
deployment and the user's own space. Built with P2.1's enumerators (every tool a durable run can reach)."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from harness_core.effects.flow import FlowGuardedPolicy, FlowRule, external_write
from harness_core.effects.types import allow_all
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.tools.tags import SINK_TAG_KEY
from tests.unit.harness.test_effect_tags_complete import ENUMERATORS

if TYPE_CHECKING:
    from harness_core.effects.types import ToolEffect
    from harness_core.tools.base import Tool


@pytest.fixture(scope="module")
def catalog() -> dict[str, ToolEffect]:
    tools: list[Tool] = [tool for enumerate_ in ENUMERATORS.values() for tool in enumerate_()]
    return {tool.name: classify_tool_effect(tool.name, tool.tags) for tool in tools}


def _named(catalog: dict[str, ToolEffect], suffix: str) -> ToolEffect:
    [found] = [effect for name, effect in catalog.items() if name == suffix or name.endswith(("." + suffix, "_" + suffix))]
    return found


@pytest.mark.parametrize("name", ["save_artifact", "update_artifact", "promote_artifact", "skill_manage"])
def test_cl3_cl4_the_deployments_own_storage_never_asks(catalog: dict[str, ToolEffect], name: str) -> None:
    effect = _named(catalog, name)
    assert effect.effect == "write"
    assert effect.sink.egress == "none"
    assert not external_write(effect)


def test_cl5_run_code_without_network_stays_in_the_sandbox(catalog: dict[str, ToolEffect]) -> None:
    from app.agents.agent_loop.sandbox_bridge import register_coding_sandbox_tools
    from tests.unit.harness.test_effect_tags_complete import _registered

    assert _named(catalog, "run_code").sink.egress == "none"
    networked = {
        tool.name: classify_tool_effect(tool.name, tool.tags)
        for tool in _registered(lambda r: register_coding_sandbox_tools(r, object(), allow_network=True))  # type: ignore[arg-type]
    }
    assert networked["run_code"].sink.egress == "external"
    # install_packages reaches the package index whatever the run's network.
    assert _named(catalog, "install_packages").sink.egress == "external"


@pytest.mark.parametrize("name", ["gmail__draft_email", "drive__create_folder", "onedrive__create_onenote_notebook"])
def test_cl6_the_users_own_space_never_asks(catalog: dict[str, ToolEffect], name: str) -> None:
    effect = catalog[name]
    assert effect.sink.egress == "self"
    assert not external_write(effect)


@pytest.mark.parametrize(
    "name",
    ["gmail__send_email", "onedrive__create_word_file", "onedrive__copy_item", "onedrive__share_item", "slack__set_user_status"],
)
def test_cl6_a_location_that_may_be_shared_stays_external(catalog: dict[str, ToolEffect], name: str) -> None:
    """A drive id or a recipient can name a place others read: those writes still ask."""
    assert external_write(catalog[name])


@pytest.mark.parametrize("name", ["send_message", "send_email", "create_issue"])
def test_cl7_messages_and_shared_systems_stay_external(catalog: dict[str, ToolEffect], name: str) -> None:
    matching = [effect for tool, effect in catalog.items() if tool.endswith(name)]
    assert matching and all(external_write(effect) for effect in matching), name


def test_cl7_every_mcp_tool_is_external(catalog: dict[str, ToolEffect]) -> None:
    assert external_write(_named(catalog, "delete_page"))


def test_cl10_measure_what_may_ask(catalog: dict[str, ToolEffect]) -> None:
    """The counts UX1 records: the share of the catalog the flow rule can ever ask about."""
    kinds = Counter(effect.effect for effect in catalog.values())
    writes = [e for e in catalog.values() if e.effect in ("write", "execute")]
    sinks = Counter(e.sink.egress for e in writes)
    may_ask = sum(1 for e in writes if external_write(e))
    print(f"\nUX1-CL10 tools={len(catalog)} kinds={dict(kinds)} write_or_execute={len(writes)} sinks={dict(sinks)} may_ask={may_ask}")
    assert may_ask == sinks["external"]
    assert sinks["none"] >= 5 and sinks["self"] >= 3
    assert may_ask < len(writes)
    assert kinds["read"] > may_ask, "most tools are reads, which never ask"


@pytest.mark.parametrize("records", [None, frozenset({"rec-1"})], ids=["unknown", "private"])
def test_sink1_a_built_in_image_is_not_held_after_a_read_while_an_email_is(
    catalog: dict[str, ToolEffect], records: frozenset[str] | None
) -> None:
    """SINK1: generate_image went untagged, so the flow rule held it as an external write after any read."""
    ledger = SimpleNamespace(source_records=lambda _run_id: records)
    policy = FlowGuardedPolicy(allow_all, FlowRule("ask"), ledger, "r")  # type: ignore[arg-type]
    image = _named(catalog, "generate_image")
    assert image.sink.egress == "self"
    assert policy(ToolCall(id="c", name="generate_image", arguments={"prompt": "a cat"}), image) == "allow"
    assert policy(ToolCall(id="c", name="send_email", arguments={}), catalog["gmail__send_email"]) == "ask"


def test_sink1_every_platform_write_declares_where_its_arguments_go() -> None:
    """An untagged write is ``external`` (fail closed), which holds a built-in tool after any read: a platform write
    must say where its arguments go, so that default is never an accident."""
    from app.agent_loop_lib.tools.toolset import ToolsetBuilder
    from app.agents.agent_loop.tool_loader import (
        _has_tool_decorated_methods,
        _infer_path_prefix,
    )
    from tests.support.toolset_registry import discovered_toolsets

    checked, untagged = 0, []
    for ts_name, meta in sorted(discovered_toolsets().items()):
        cls = meta.get("class")
        if meta.get("provider") != "platform" or cls is None or not _has_tool_decorated_methods(cls):
            continue
        prefix = _infer_path_prefix(cls, fallback_name=ts_name)
        builder = ToolsetBuilder(cls.__new__(cls), name=prefix.rsplit("/", 1)[-1], description="", path_prefix=prefix)
        for tool in builder.tools:
            if classify_tool_effect(tool.name, tool.tags).needs_intent_commit:
                checked += 1
                if not any(tag.key == SINK_TAG_KEY for tag in tool.tags):
                    untagged.append(tool.name)
    assert checked >= 8, "the platform toolsets' writes were not found"
    assert not untagged, f"platform writes without a sink tag: {untagged}"


def _sandbox_tools(monkeypatch: pytest.MonkeyPatch, mode: str) -> dict[str, ToolEffect]:
    """The legacy sandbox toolsets as the loader offers them under ``SANDBOX_MODE=mode``."""
    from app.agent_loop_lib.tools.toolset import ToolsetBuilder
    from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
    from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

    monkeypatch.setenv("SANDBOX_MODE", mode)
    monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
    effects: dict[str, ToolEffect] = {}
    for cls, prefix in ((CodingSandbox, "/tools/coding_sandbox"), (DatabaseSandbox, "/tools/database_sandbox")):
        instance = cls({})  # type: ignore[arg-type]
        builder = ToolsetBuilder(instance, name=prefix.rsplit("/", 1)[-1], description="", path_prefix=prefix)
        for tool in instance.tools_for_request(builder.tools):
            effects[tool.name] = classify_tool_effect(tool.name, tool.tags)
    return effects


@pytest.mark.parametrize(("mode", "held"), [("docker", False), ("broker", False), ("local", True)])
def test_sink2_a_sandbox_run_is_held_after_a_private_read_only_where_it_runs_on_the_host(
    monkeypatch: pytest.MonkeyPatch, mode: str, held: bool
) -> None:
    """SINK2: docker and broker run the code with no network (installs take only allowlisted package names); local runs
    it on the host with the host's network, so only there may it carry a private read out."""
    ledger = SimpleNamespace(source_records=lambda _run_id: frozenset({"rec-1"}))
    policy = FlowGuardedPolicy(allow_all, FlowRule("ask"), ledger, "r")  # type: ignore[arg-type]
    effects = _sandbox_tools(monkeypatch, mode)
    assert set(effects) == {
        "coding_sandbox__execute_python", "coding_sandbox__execute_typescript",
        "database_sandbox__execute_sqlite", "database_sandbox__execute_postgresql",
    }
    for name, effect in effects.items():
        assert effect.sink.egress == ("external" if held else "self"), name
        assert policy(ToolCall(id="c", name=name, arguments={"code": "print(1)"}), effect) == ("ask" if held else "allow")


class _Backend:
    """A registered coding backend factory, answering ``capabilities()`` as the real one does."""

    def __init__(self, capabilities: object) -> None:
        self._capabilities = capabilities

    def capabilities(self) -> object:
        return self._capabilities


def _run_code_on(backend: str) -> ToolEffect:
    from app.agent_loop_lib.sandbox.coding.broker import BrokerCodingSandbox
    from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
    from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
    from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
    from app.agents.agent_loop.sandbox_bridge import register_coding_sandbox_tools
    from tests.unit.harness.test_effect_tags_complete import _registered

    capabilities = {
        "docker": DockerCodingSandbox.describe_capabilities(allow_network=False),
        "broker": BrokerCodingSandbox.describe_capabilities(allow_network=False),
        "local": LocalCodingSandbox.describe_capabilities(),
    }[backend]
    manager = SandboxManager()
    manager.register_backend(SandboxType.CODING, _Backend(capabilities))
    tools = _registered(lambda r: register_coding_sandbox_tools(r, manager, allow_network=False))  # type: ignore[arg-type]
    [run_code] = [t for t in tools if t.name == "run_code"]
    return classify_tool_effect(run_code.name, run_code.tags)


@pytest.mark.parametrize(("backend", "held"), [("docker", False), ("broker", False), ("local", True)])
def test_sink2_run_code_takes_its_sink_from_what_the_backend_does(backend: str, held: bool) -> None:
    """SINK2's rule on the tool the loops actually offer: with network off, docker and broker run the code in their
    own network namespace (sink ``none``: own sandbox, CODEAUTO), while the local backend is a host subprocess with the
    host's network (``supports_network`` is always true there), so its ``run_code`` is ``external``: the flow rule holds
    it after a private read and a class ``ask`` is not waived as own-sandbox code."""
    from harness_core.effects.policy import in_own_sandbox

    effect = _run_code_on(backend)
    assert effect.sink.egress == ("external" if held else "none")
    assert in_own_sandbox(effect) is not held
    ledger = SimpleNamespace(source_records=lambda _run_id: frozenset({"rec-1"}))
    policy = FlowGuardedPolicy(allow_all, FlowRule("ask"), ledger, "r")  # type: ignore[arg-type]
    assert policy(ToolCall(id="c", name="run_code", arguments={"code": "print(1)"}), effect) == ("ask" if held else "allow")
