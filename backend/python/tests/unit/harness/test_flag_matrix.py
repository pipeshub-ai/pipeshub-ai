"""F3 behaviour cases, Python half: the matrix's defaults and reasons, read from ``flags.json``.

FD-1 each Python-read env flag's code default is the matrix default; FB-2 all off is today's
product (no prompt change, no sandbox backend, the ledger meters); FB-3 the image's entrypoint
starts no harness process at the defaults. FB-6, FB-7 and FB-9 were ``code_task``'s (stage 1,
retired by S1R). Node's half: ``tests/modules/agent-harness/flag-matrix.test.ts``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.sandbox.coding.settings import (
    SandboxUnavailableError,
    resolve_sandbox_mode,
)
from app.agents.agent_loop.factory import (
    _transport_provider,  # pyright: ignore[reportPrivateUsage]
)
from app.agents.agent_loop.sandbox_bridge import sandbox_network_enabled
from app.agents.agent_loop.skills.manager_factory import skills_manager_config
from app.agents.agent_loop.skills_wiring import skills_enabled
from app.agents.mcp.stdio_policy import custom_stdio_allowed
from app.control_plane.runtime.deployment import flow_guard_enabled
from app.harness.access.offering import harness_offering
from app.harness.assembly.context_compiler import (
    auto_compact_enabled,
    context_compiler_enabled,
)
from app.harness.assembly.deep_route import deep_routing_enabled
from app.harness.assembly.model import ledger_enabled, provider_state_enabled
from app.harness.assembly.model_policy import model_fallback_enabled
from app.harness.credentials.read_through import credentials_switch_on
from app.harness.engines.worker import engine_runs_enabled, engine_snapshots_enabled
from app.harness.mcp_hub.settings import harness_enabled, hub_enabled
from app.harness.mcp_runner.settings import runners_enabled
from app.harness.policy_documents import policy_store_mode
from app.harness.reconcile import reconcile_enabled
from app.harness.sandbox.egress_config import connect_tunnels_enabled, kill_switch_on
from app.harness.sandbox.egress_state import state_required
from app.harness.telemetry import content_capture_enabled, tracing_enabled
from app.harness.tool_identity.settings import instance_tool_ids_enabled
from app.model_gateway_main import control_plane_pin
from app.modules.agents.qna.tool_system import code_execution_enabled
from app.sandbox.roots import operator_root
from app.sandbox_broker_main import warm_pool_max
from tests.cohesion.support import REPO_ROOT
from tests.support.mcp_tool_admission import mcp_approvals_required  # noqa: F401 - the fixture, used by name
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agents.agent_loop.context import AgentContext

# The matrix is the code's real defaults: not the suite's "unset MCP_HUB means off" (tests/conftest.py).
pytestmark = pytest.mark.mcp_hub_default

MATRIX: dict[str, Any] = json.loads((REPO_ROOT / "contracts/harness/flags.json").read_text(encoding="utf-8"))
FLAGS = {f["name"]: f for f in MATRIX["flags"]}
FEATURES = {f["name"]: f for f in MATRIX["features"]}
ENV_FLAGS = [f["name"] for f in MATRIX["flags"] if f["kind"] == "env"]


def _sandbox_mode_default() -> str | None:
    try:
        return resolve_sandbox_mode()
    except SandboxUnavailableError:
        return None


def _policy_store_mode_at_start() -> str:
    """The mode a process started now would read (it is read once per process)."""
    policy_store_mode.cache_clear()
    try:
        return policy_store_mode()
    finally:
        policy_store_mode.cache_clear()


# How each Python-read env flag's code default is observed; a new flag needs a probe here.
PROBES: dict[str, Callable[[], object]] = {
    "AGENT_HARNESS_BUDGET_LEDGER": ledger_enabled,
    "CONTROL_PLANE_PIN": control_plane_pin,
    "CONTROL_PLANE_POLICY_STORE": _policy_store_mode_at_start,
    "SANDBOX_MODE": _sandbox_mode_default,
    "SANDBOX_ALLOW_NETWORK": sandbox_network_enabled,
    "HARNESS_ENGINE_RUNS": engine_runs_enabled,
    "HARNESS_ENGINE_SNAPSHOTS": engine_snapshots_enabled,
    "AGENT_HARNESS_OFFERING": harness_offering,
    "SANDBOX_WARM_POOL_MAX": warm_pool_max,
    "MCP_STDIO_RUNNERS": runners_enabled,
    "HARNESS_CONTEXT_COMPILER": context_compiler_enabled,
    "HARNESS_AUTO_COMPACT": auto_compact_enabled,
    "HARNESS_DEEP_ROUTING": deep_routing_enabled,
    "HARNESS_OTEL": tracing_enabled,
    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": content_capture_enabled,
    # The matrix's MCP_HUB default is a harness run's (D-MCP-1); main's classic chat has no default of its own.
    "MCP_HUB": lambda: hub_enabled(harness=True),
    "AGENT_HARNESS_WORKER_ENABLED": harness_enabled,
    "CONTROL_PLANE_CREDENTIALS": credentials_switch_on,
    "EGRESS_CONNECT_TUNNELS": connect_tunnels_enabled,
    "AGENT_HARNESS_KILL_SWITCH": kill_switch_on,
    "INSTANCE_TOOL_IDS": instance_tool_ids_enabled,
    "HARNESS_RECONCILE": reconcile_enabled,
    "HARNESS_FLOW_GUARD": flow_guard_enabled,
    "HARNESS_PROVIDER_STATE": provider_state_enabled,
    "HARNESS_MODEL_FALLBACK": model_fallback_enabled,
    "EGRESS_STATE_REQUIRED": state_required,
    "MCP_ALLOW_CUSTOM_STDIO": custom_stdio_allowed,
    "PIPESHUB_ENABLE_SKILLS": skills_enabled,
    "PIPESHUB_ENABLE_CODE_EXECUTION": lambda: code_execution_enabled({}),  # type: ignore[arg-type]
    "PIPESHUB_SKILLS_AUTO_APPROVE": lambda: skills_manager_config().auto_approve,
    "PIPESHUB_SKILLS_WRITE_APPROVAL": lambda: skills_manager_config().write_approval,
    "PIPESHUB_SKILLS_LEARNING_ENABLED": lambda: skills_manager_config().learning_enabled,
    "PIPESHUB_AGENT_TRANSPORT": _transport_provider,
    "PIPESHUB_SANDBOX_ROOT": operator_root,
}
# Flags with a value set rather than a boolean: each has its own parser and tests.
NOT_BOOLEAN = frozenset({"SANDBOX_MODE", "AGENT_HARNESS_OFFERING", "CONTROL_PLANE_PIN", "CONTROL_PLANE_POLICY_STORE", "SANDBOX_WARM_POOL_MAX", "PIPESHUB_AGENT_TRANSPORT", "PIPESHUB_SANDBOX_ROOT"})
# Flags the matrix records with a parse of their own (not env_flag's vocabulary or its refusal).
OWN_PARSE = frozenset(f["name"] for f in MATRIX["flags"] if f.get("parser") in {"true_only", "boolean_words", "transport_name"})


# Settings a flag's default follows (``defaultFollows``): unset with every flag, so such a flag reads its off value.
FOLLOWED_SETTINGS = frozenset(
    f["defaultFollows"] for f in MATRIX["flags"] if f.get("defaultFollows") and f["defaultFollows"] not in FLAGS
)


def matrix_default(name: str) -> object:
    """A flag's default with nothing set: one that follows another flag reads that flag's; one that follows
    whether a setting is set (unset here) reads false."""
    follows = FLAGS[name].get("defaultFollows")
    if follows is None:
        return FLAGS[name]["default"]
    return matrix_default(follows) if follows in FLAGS else False


def reason(feature: str, need: str) -> str:
    entry = next(n for n in FEATURES[feature]["requires"] if need in (n.get("flag"), n.get("feature"), n.get("condition")))
    return cast("str", entry["reason"])


@pytest.fixture
def all_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [*ENV_FLAGS, *FOLLOWED_SETTINGS]:
        monkeypatch.delenv(name, raising=False)


# FD-1


def test_fd1_every_python_env_flag_has_a_probe() -> None:
    assert {n for n in ENV_FLAGS if "python" in FLAGS[n]["readers"]} == set(PROBES)


@pytest.mark.parametrize("name", sorted(PROBES))
def test_fd1_the_code_default_is_the_matrix_default(all_off: None, name: str) -> None:
    assert PROBES[name]() == matrix_default(name)


def test_fd1_a_flag_that_follows_a_setting_is_on_where_the_setting_is_set(
    all_off: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", "http://harness-state:3000")
    assert FLAGS["EGRESS_STATE_REQUIRED"]["defaultFollows"] == "AGENT_HARNESS_STATE_URL"
    assert PROBES["EGRESS_STATE_REQUIRED"]() is True


# FB-2: all off


async def _tools_and_prompt(context: AgentContext) -> tuple[list[str], str]:
    from app.agents.agent_loop.factory import PipesHubAgentFactory

    agent, runtime, goal, _ = await PipesHubAgentFactory().create(context, context.llm, "quick", query="hello")
    prompt = agent.spec.system_prompt
    rendered = prompt if isinstance(prompt, str) else prompt.build(agent.spec, runtime, goal, [], {})
    return sorted(agent.spec.tool_names), str(rendered)


async def test_fb2_all_off_is_todays_product(all_off: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
    context = make_context(conversation_id="conv-1")
    # The real Labs reader over a store with no platform settings: every flag absent.
    context.config_service.get_config = AsyncMock(return_value=None)  # pyright: ignore[reportAttributeAccessIssue]
    tools, _prompt = await _tools_and_prompt(context)
    assert "code_task" not in tools
    with pytest.raises(SandboxUnavailableError):
        resolve_sandbox_mode()
    assert ledger_enabled() is True
    assert FEATURES["session_erasure"]["alwaysOn"] is True


# FB-3


def _entrypoint(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """The process monitor's own flag functions, run under bash with ``env``."""
    text = (REPO_ROOT / "deployment" / "process_monitor.sh").read_text(encoding="utf-8")
    functions = [
        re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, flags=re.M | re.S)
        for name in ("log", "env_flag", "harness_worker_enabled", "model_gateway_enabled")
    ]
    assert all(functions)
    body = "".join(m.group(0) for m in functions if m is not None).replace('| tee -a "$LOG_FILE"', "")
    script = f"set -e\n{body}model_gateway_enabled || true\n" + (
        "harness_worker_enabled && echo worker; model_gateway_enabled && echo gateway; true"
    )
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run([bash, "-c", script], env=env, capture_output=True, text=True, check=False)


def _entrypoint_starts(env: dict[str, str]) -> tuple[bool, bool]:
    done = _entrypoint(env)
    assert done.returncode == 0, done.stdout
    out = done.stdout.split()
    return "worker" in out, "gateway" in out


@pytest.mark.parametrize(
    ("env", "worker", "gateway"),
    [
        # On by default (ADR-OPS-06 as amended 2026-11-20): nothing set starts the worker and the gateway that follows it.
        ({}, True, True),
        ({"AGENT_HARNESS_WORKER_ENABLED": "false"}, False, False),
        ({"AGENT_HARNESS_WORKER_ENABLED": "true", "MODEL_GATEWAY_ENABLED": "false"}, True, False),
        ({"AGENT_HARNESS_WORKER_ENABLED": "off", "MODEL_GATEWAY_ENABLED": "true"}, False, True),
        ({"MODEL_GATEWAY_ENABLED": ""}, True, True),
        ({"AGENT_HARNESS_WORKER_ENABLED": "true"}, True, True),
        ({"AGENT_HARNESS_WORKER_ENABLED": "OFF"}, False, False),
        ({"AGENT_HARNESS_WORKER_ENABLED": "1", "MODEL_GATEWAY_ENABLED": "no"}, True, False),
    ],
)
def test_fb3_the_entrypoint_starts_harness_processes_only_when_asked(env: dict[str, str], worker: bool, gateway: bool) -> None:
    assert FLAGS["AGENT_HARNESS_WORKER_ENABLED"]["default"] is True
    assert FLAGS["MODEL_GATEWAY_ENABLED"]["defaultFollows"] == "AGENT_HARNESS_WORKER_ENABLED"
    assert _entrypoint_starts(env) == (worker, gateway)


# FM-F5 (F3 follow-up): one boolean parse, a malformed value refuses to start


@pytest.mark.parametrize("name", ["AGENT_HARNESS_WORKER_ENABLED", "MODEL_GATEWAY_ENABLED"])
def test_fm_f5_the_entrypoint_refuses_a_malformed_flag(name: str) -> None:
    done = _entrypoint({name: "enabled"})
    assert done.returncode == 1
    assert f"{name}='enabled' is not a boolean" in done.stdout
    assert "worker" not in done.stdout.split() and "gateway" not in done.stdout.split()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("TRUE", True), (" yes ", True), ("on", True), ("0", False), ("False", False), ("no", False), ("OFF", False)],
)
@pytest.mark.parametrize("name", sorted(PROBES.keys() - NOT_BOOLEAN - OWN_PARSE))
def test_fm_f5_every_python_flag_accepts_the_one_vocabulary(
    all_off: None, monkeypatch: pytest.MonkeyPatch, name: str, raw: str, expected: bool
) -> None:
    monkeypatch.setenv(name, raw)
    assert PROBES[name]() is expected


@pytest.mark.parametrize("name", sorted(PROBES.keys() - NOT_BOOLEAN - OWN_PARSE))
def test_fm_f5_blank_is_the_matrix_default(all_off: None, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "")
    assert PROBES[name]() is matrix_default(name)


@pytest.mark.parametrize("name", sorted(PROBES.keys() - NOT_BOOLEAN - OWN_PARSE))
def test_fm_f5_a_malformed_value_is_a_typed_error_naming_the_flag(
    all_off: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    from app.utils.env_flags import InvalidEnvFlag, validate_env_flags

    monkeypatch.setenv(name, "enabled")
    with pytest.raises(InvalidEnvFlag, match=name):
        PROBES[name]()
    with pytest.raises(InvalidEnvFlag, match=name) as refused:
        validate_env_flags()
    assert refused.value.name == name


async def test_fm_f5_the_query_service_refuses_to_start_on_a_malformed_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI

    from app.query_main import lifespan
    from app.utils.env_flags import InvalidEnvFlag

    monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", "maybe")
    with pytest.raises(InvalidEnvFlag, match="SANDBOX_ALLOW_NETWORK"):
        async with lifespan(FastAPI()):
            pass


@pytest.mark.parametrize("value", ["true", "false", "maybe"])
def test_sr_2_the_retired_code_task_switch_is_read_by_nothing(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from app.utils.env_flags import ENV_FLAG_DEFAULTS, validate_env_flags

    monkeypatch.setenv("PIPESHUB_CODE_TASK", value)
    validate_env_flags()
    assert "PIPESHUB_CODE_TASK" not in ENV_FLAG_DEFAULTS
    assert "PIPESHUB_CODE_TASK" not in FLAGS and "ENABLE_CODE_TASK" not in FLAGS and "code_task" not in FEATURES


async def test_fm_f5_the_worker_refuses_to_start_on_a_malformed_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness_main import run_worker
    from app.utils.env_flags import InvalidEnvFlag

    monkeypatch.setenv("AGENT_HARNESS_BUDGET_LEDGER", "disabled")
    with pytest.raises(InvalidEnvFlag, match="AGENT_HARNESS_BUDGET_LEDGER"):
        await run_worker()


# P5.10: the MCP runner flag (feature mcp_stdio_runners)


async def test_p510_runners_off_is_todays_host_process_and_on_without_a_broker_is_refused(
    monkeypatch: pytest.MonkeyPatch, all_off: None
) -> None:
    from app.agents.mcp.client import MCPConnectionError, build_transport
    from app.agents.mcp.service import instance_config_from_dict
    from app.harness.mcp_runner.routing import stdio_target
    from app.harness.mcp_runner.transport import RunnerClientTransport
    from harness_core.sandbox.mcp_runner import RunnerUnavailable
    from tests.unit.harness.mcp_runner.support import ProcessStub, stdio_instance

    config = instance_config_from_dict(stdio_instance())
    assert FEATURES["mcp_stdio_runners"]["whenOff"] == "legacy"
    # Off: the host spawns the server itself (the SDK's stdio client), not through a runner.
    assert not isinstance(build_transport(config, env={}).streams, RunnerClientTransport)
    assert await stdio_target(config, {}, tenant="org-1", owner="user-1") is None

    monkeypatch.setenv("MCP_STDIO_RUNNERS", "true")
    process = ProcessStub(error=RunnerUnavailable("no broker", cause="broker_unreachable"))
    monkeypatch.setattr("app.harness.mcp_runner.routing.process_mcp_runner", process)
    with pytest.raises(RunnerUnavailable) as refused:
        _ = await stdio_target(config, {}, tenant="org-1", owner="user-1")
    assert reason("mcp_stdio_runners", "sandbox_plane").startswith(refused.value.reason_code)
    with pytest.raises(MCPConnectionError):
        _ = build_transport(config, env={})


# P2.8: the MCP hub flag (feature mcp_hub)


@pytest.mark.usefixtures("mcp_approvals_required")
async def test_p28_hub_off_is_todays_path_and_on_refuses_what_the_matrix_says(
    monkeypatch: pytest.MonkeyPatch, all_off: None
) -> None:
    from app.agents.mcp.discovery import build_namespaced_tool_name
    from tests.unit.harness.credentials.support import DictConfig
    from tests.unit.harness.mcp_hub.support import (
        URL,
        FakeServer,
        context,
        hub_on,
        load,
        serve,
        teardown,
    )

    assert FEATURES["mcp_hub"]["whenOff"] == "legacy"
    assert not hub_enabled()
    server = FakeServer()
    built = serve(monkeypatch, {URL: server})
    off = context(DictConfig())
    registry = await load(off)
    assert "mcp_fake_search" in registry.names(), "off: every listed tool, no approval"
    assert [b["url"] for b in built] == [URL]
    # Off: main's names (a name a provider would refuse is made valid with a hash), no hub check.
    assert build_namespaced_tool_name("fake", "search") == "mcp_fake_search"
    await teardown(off)

    _ = hub_on(monkeypatch)
    on = context(DictConfig())
    _ = await load(on)
    assert on.mcp_tool_load_failures[0]["reason"] == reason("mcp_hub", "manifest_approved").split(" ")[0]
    metadata = context(DictConfig(), instance={**on.mcp_server_configs["mcp-fake"]["instance"], "url": "http://169.254.169.254/"})
    _ = await load(metadata)
    assert metadata.mcp_tool_load_failures[0]["reason"] == reason("mcp_hub", "public_url")
    await teardown(on, metadata)


# P2.2: instance-scoped tool ids (feature instance_tool_ids)


def test_p22_off_keys_toolsets_by_type_as_today_and_on_keeps_instances(
    monkeypatch: pytest.MonkeyPatch, all_off: None
) -> None:
    from app.api.routes.agent import _parse_toolsets

    two = [
        {"name": "jira", "instanceId": "a", "tools": [{"name": "search_issues"}]},
        {"name": "jira", "instanceId": "b", "tools": [{"name": "create_issue"}]},
    ]
    assert FEATURES["instance_tool_ids"]["whenOff"] == "legacy"
    assert not instance_tool_ids_enabled()
    assert list(_parse_toolsets(two)) == ["jira"], reason("instance_tool_ids", "INSTANCE_TOOL_IDS")
    monkeypatch.setenv("INSTANCE_TOOL_IDS", "true")
    assert len(_parse_toolsets(two)) == 2
