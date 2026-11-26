"""MW-7: an instance's runner spec; MW-8: the flag and the runner settings (P5.10)."""

from __future__ import annotations

import pytest

from app import edition_config
from app.agents.mcp.runner_fields import RunnerFields
from app.agents.mcp.service import instance_config_from_dict
from app.agents.mcp.stdio_policy import StdioPolicyError
from app.harness.mcp_runner.routing import (
    declared_env_names,
    runner_spec_for,
    runner_target,
)
from app.harness.mcp_runner.settings import (
    RunnerSettings,
    runner_settings,
    runners_enabled,
)
from app.utils.env_flags import InvalidEnvFlag
from harness_core.sandbox.mcp_runner import RunnerEgressHost, RunnerEgressInvalid
from harness_core.sandbox.mcp_runner.errors import MCPRunnerRefused
from harness_core.sandbox.types import RuntimeMode
from harness_core.testing.sandbox.mcp_runner import ECHO_COMMAND
from tests.unit.harness.mcp_runner.support import echo_declares, stdio_instance

SETTINGS = RunnerSettings(image="pipeshub/sandbox:x", isolation_floor=RuntimeMode.GVISOR, idle_ttl_s=60, max_age_s=600)
ENV = ("MCP_STDIO_RUNNERS", "MCP_RUNNER_IMAGE", "MCP_RUNNER_ISOLATION", "MCP_RUNNER_IDLE_TTL_S", "MCP_RUNNER_MAX_AGE_S", "SANDBOX_DOCKER_IMAGE")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


def test_mw7_the_spec_is_the_instances_pinned_program(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")
    # Main's custom-STDIO allow-list (MCP_STDIO_ALLOWED_COMMANDS) holds for a runner's program too (PR1 J-8).
    monkeypatch.setenv("MCP_STDIO_ALLOWED_COMMANDS", "/opt/mcp/bin/server")
    config = instance_config_from_dict(
        stdio_instance(
            typeId=None,
            command="/opt/mcp/bin/server",
            args=["--stdio", "--read-only"],
            requiredEnv=["ECHO_API_TOKEN"],
            optionalEnv=["ECHO_REGION"],
            declaredEgressHosts=["api.listed.test", "api.other.test:8080"],
        )
    )
    spec = runner_spec_for(config, SETTINGS)
    assert (spec.server_id, spec.command, spec.args) == ("mcp-echo", "/opt/mcp/bin/server", ("--stdio", "--read-only"))
    assert spec.env_names == frozenset({"ECHO_API_TOKEN", "ECHO_REGION"})
    assert spec.egress == (RunnerEgressHost("api.listed.test", (80,)), RunnerEgressHost("api.other.test", (8080,)))
    assert spec.image == "pipeshub/sandbox:x"
    assert spec.isolation_floor is RuntimeMode.GVISOR


def test_mw7_a_catalog_instance_reaches_the_hosts_its_catalog_entry_declares() -> None:
    with echo_declares(["api.echo.test"]):
        config = instance_config_from_dict(stdio_instance(declaredEgressHosts=["elsewhere.test"]))
    assert runner_spec_for(config, SETTINGS).egress == (RunnerEgressHost("api.echo.test", (80,)),)


def test_mw7_without_an_extension_per_server_runner_fields_on_the_record_change_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fields only an edition's own routes write: this build runs every server as its declaration says."""
    monkeypatch.setattr(edition_config, "mcp_runner_settings", None, raising=False)
    with echo_declares(["api.echo.test"]):
        config = instance_config_from_dict(
            stdio_instance(egressHosts=["anywhere.test"], runnerImage="registry.example/other@sha256:" + "b" * 64)
        )
    spec = runner_spec_for(config, SETTINGS)
    assert spec.egress == (RunnerEgressHost("api.echo.test", (80,)),) and spec.image == SETTINGS.image


def test_mw7_an_extensions_runner_settings_apply_over_the_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[RunnerFields] = []

    class Pinned:
        def fields(self, instance: object, declared: RunnerFields) -> RunnerFields:
            seen.append(declared)
            return RunnerFields(egress_hosts=("api.pinned.test:8080",), runner_image="registry.example/pinned:1")

    monkeypatch.setattr(edition_config, "mcp_runner_settings", Pinned(), raising=False)
    with echo_declares(["api.echo.test"]):
        spec = runner_spec_for(instance_config_from_dict(stdio_instance()), SETTINGS)
    assert seen == [RunnerFields(egress_hosts=("api.echo.test",))]
    assert spec.egress == (RunnerEgressHost("api.pinned.test", (8080,)),) and spec.image == "registry.example/pinned:1"


def test_mw7_a_catalog_instance_runs_its_templates_program_not_a_stored_one() -> None:
    config = instance_config_from_dict(stdio_instance(command="/bin/sh", args=["-c", "id"]))
    spec = runner_spec_for(config, SETTINGS)
    assert (spec.command, spec.args) == (ECHO_COMMAND, ())


async def test_mw7_a_custom_instance_needs_the_operators_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_ALLOW_CUSTOM_STDIO", raising=False)
    config = instance_config_from_dict(stdio_instance(typeId=None, command="/opt/mcp/bin/server"))
    with pytest.raises(StdioPolicyError):
        _ = runner_spec_for(config, SETTINGS)
    with pytest.raises(MCPRunnerRefused) as refused:
        _ = await runner_target(config, {}, tenant="org-1", owner="user-1", principal="user-1")
    assert refused.value.reason_code == "stdio_launch_refused"


def test_mw7_defaults_the_image_and_declares_api_token_without_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A custom instance: a catalog one takes its env names from its template (main #4003).
    monkeypatch.setenv("MCP_ALLOW_CUSTOM_STDIO", "true")
    monkeypatch.setenv("MCP_STDIO_ALLOWED_COMMANDS", ECHO_COMMAND)
    config = instance_config_from_dict(stdio_instance(typeId=None, requiredEnv=[]))
    spec = runner_spec_for(config, SETTINGS)
    assert spec.image == "pipeshub/sandbox:x" and spec.egress == ()
    assert declared_env_names(config) == frozenset({"API_TOKEN"}) == spec.env_names


def test_mw7_a_bad_egress_entry_is_refused() -> None:
    with echo_declares(["api.listed.test:http"]), pytest.raises(RunnerEgressInvalid):
        _ = runner_spec_for(instance_config_from_dict(stdio_instance()), SETTINGS)


def test_mw8_the_flag_is_an_env_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert runners_enabled() is False
    monkeypatch.setenv("MCP_STDIO_RUNNERS", " ")
    assert runners_enabled() is False
    monkeypatch.setenv("MCP_STDIO_RUNNERS", "On")
    assert runners_enabled() is True
    monkeypatch.setenv("MCP_STDIO_RUNNERS", "maybe")
    with pytest.raises(InvalidEnvFlag):
        _ = runners_enabled()


def test_mw8_settings_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    assert runner_settings() == RunnerSettings("pipeshub/sandbox:latest", RuntimeMode.DOCKER_HARDENED, 900, 21600)
    monkeypatch.setenv("SANDBOX_DOCKER_IMAGE", "pipeshub/sandbox:y")
    assert runner_settings().image == "pipeshub/sandbox:y"
    monkeypatch.setenv("MCP_RUNNER_IMAGE", "pxh-mcp:1")
    monkeypatch.setenv("MCP_RUNNER_ISOLATION", "gvisor")
    monkeypatch.setenv("MCP_RUNNER_IDLE_TTL_S", "120")
    monkeypatch.setenv("MCP_RUNNER_MAX_AGE_S", "3600")
    assert runner_settings() == RunnerSettings("pxh-mcp:1", RuntimeMode.GVISOR, 120, 3600)


@pytest.mark.parametrize(("name", "value"), [("MCP_RUNNER_ISOLATION", "chroot"), ("MCP_RUNNER_IDLE_TTL_S", "0"), ("MCP_RUNNER_MAX_AGE_S", "-5")])
def test_mw8_a_bad_setting_refuses(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        _ = runner_settings()


def test_mw8_the_broker_allows_the_runner_image(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sandbox_broker_main import allowed_images

    monkeypatch.setenv("MCP_RUNNER_IMAGE", "pxh-mcp:1")
    assert {"pxh-mcp:1", "pipeshub/sandbox:latest"} <= allowed_images()
