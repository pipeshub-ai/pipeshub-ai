"""Recommendations 2 and 6 (recs audit): which outer isolation an engine run gets, and which protocol drives
each harness.

R2: the controller takes the strongest runtime the broker offers at or above the profile's floor and never a weaker
one; the Docker broker maps gVisor to ``runsc`` (``test_docker_broker.py``). The release floor is gVisor
(``policy/release.json``): a production host without ``runsc`` refuses engine runs, and
only the documented dev/test override (``engineIsolation.devOverride``) admits hardened runc. The
Kubernetes agent-sandbox and Kata modes are deferred (P5.1b, P5.3); the skips below say so.

R6: every released engine is driven through its own native protocol: Codex's app-server (JSON-RPC over stdio),
Claude Code's stream-json (the protocol the Claude Agent SDK speaks to the CLI) and opencode's HTTP server. No ACP
driver exists; the research (F4, F5) found ACP drops what PipesHub needs (client tools, system prompt, allow-list,
steering).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.engines.controller import EngineControllerConfig, engine_mode
from harness_core.engines.isolation import OVERRIDE_ENV
from harness_core.engines.types import is_acp_kind
from harness_core.sandbox.types import RuntimeMode
from harness_engines.release import (
    release_allowlist,
    release_default_profile,
    release_registry,
)

REPO = Path(__file__).resolve().parents[6]
ROUTE = AccessRouteRef("route-azure-gpt", "d1")


def test_r2_an_engine_runs_on_gvisor_wherever_the_broker_offers_it() -> None:
    hardened, gvisor = RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR
    assert engine_mode({hardened, gvisor}) is gvisor
    assert engine_mode({gvisor, hardened}, hardened) is gvisor


def test_r2_the_release_floor_is_gvisor_and_only_the_dev_override_admits_hardened_runc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2 floor: production refuses an engine without runsc; dev/test may opt out."""
    hardened, gvisor = RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR
    monkeypatch.delenv(OVERRIDE_ENV, raising=False)
    assert {release_default_profile(r.version, ROUTE).isolation_min for r in release_allowlist()} == {gvisor}
    assert engine_mode({hardened}, gvisor) is None
    assert engine_mode({hardened, gvisor}, gvisor) is gvisor
    monkeypatch.setenv(OVERRIDE_ENV, "true")
    assert {release_default_profile(r.version, ROUTE).isolation_min for r in release_allowlist()} == {hardened}
    assert engine_mode({hardened}, hardened) is hardened


@pytest.mark.skip(
    reason="recs audit R2: deferred (P5.1b Kata, P5.3 Kubernetes agent-sandbox); Helm has no "
    "agent-sandbox CRD, no Kata RuntimeClass and no egress proxy, and the test runner has no runsc or KVM"
)
def test_r2_helm_runs_each_engine_in_an_agent_sandbox_under_gvisor_or_kata() -> None:
    raise AssertionError("unreachable")


def test_r2_helm_has_no_agent_sandbox_or_kata_yet() -> None:
    """Pins what the skip above says, so the skip is revisited the day the chart grows either."""
    chart = REPO / "deployment" / "helm" / "pipeshub-ai"
    text = "\n".join(p.read_text(errors="replace") for p in chart.rglob("*.yaml"))
    assert "agents.x-k8s.io" not in text
    assert "handler: kata" not in text.lower()


def _launches() -> dict[str, tuple[str, ...]]:
    registry = release_registry()
    config = EngineControllerConfig(images={})
    found: dict[str, tuple[str, ...]] = {}
    for release in release_allowlist():
        if is_acp_kind(release.version.kind):
            continue
        adapter = registry.adapter_for(release.version)
        profile = release_default_profile(release.version, ROUTE)
        api = min(adapter.capabilities().model_apis, key=str)
        endpoints = config.endpoints("x" * 16, "y" * 16, api)
        found[release.version.kind] = adapter.render_launch(profile, endpoints, "/skills").argv
    return found


def test_r6_each_released_engine_is_driven_through_its_native_protocol() -> None:
    argv = _launches()
    assert set(argv) == {"codex", "claude_code", "opencode"}
    assert argv["codex"][0] == "/usr/local/bin/codex-app-server" and "stdio://" in argv["codex"]
    claude = argv["claude_code"]
    assert claude[claude.index("--input-format") + 1] == "stream-json"
    assert claude[claude.index("--output-format") + 1] == "stream-json"
    assert argv["opencode"][:2] == ("/usr/local/bin/opencode", "serve")
    assert not any("acp" in part.lower() for parts in argv.values() for part in parts)


def test_r6_the_drive_each_adapter_declares_matches_its_protocol() -> None:
    registry = release_registry()
    drives = {
        r.version.kind: registry.adapter_for(r.version).capabilities().drive
        for r in release_allowlist()
        if not is_acp_kind(r.version.kind)
    }
    assert drives == {"codex": "stdio", "claude_code": "stdio", "opencode": "port"}


def test_r6_an_acp_engine_is_driven_through_a_generic_acp_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """ACP-driver closes the R6 gap: a long-tail agent is a signed profile the generic ACP driver runs over stdio
    (here opencode's own ACP server, ``acp-opencode``, installed as a signed profile would be)."""
    from harness_engines import release as release_module
    from harness_engines.acp.adapter import AcpAdapter
    from harness_engines.acp.registry import packaged_acp_agents

    monkeypatch.setattr(release_module, "acp_profiles", lambda env=None: packaged_acp_agents(unsigned_ok=True))
    acp = next(r for r in release_allowlist() if is_acp_kind(r.version.kind))
    adapter = release_registry().adapter_for(acp.version)
    assert isinstance(adapter, AcpAdapter) and adapter.capabilities().drive == "stdio"
    profile = release_default_profile(acp.version, ROUTE)
    api = min(adapter.capabilities().model_apis, key=str)
    argv = adapter.render_launch(profile, EngineControllerConfig(images={}).endpoints("x" * 16, "y" * 16, api), "/skills").argv
    assert argv[:2] == ("/usr/local/bin/opencode", "acp")
