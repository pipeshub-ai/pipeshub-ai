"""Admission policy for STDIO MCP servers, which run as child processes of the query service.

Interim control until STDIO servers move into MCP-runner sandboxes. The active policy is
bound in `app.edition_config.stdio_mcp_policy` (create/update) and
`app.edition_config.stdio_mcp_launch_policy` (every launch); editions that cannot trust org
admins with host execution bind `deny_custom_stdio_policy` and `deny_custom_stdio_launch`.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.http_status_code import HttpStatusCode

if TYPE_CHECKING:
    from app.agents.mcp.models import MCPServerInstanceConfig, MCPServerTemplate


@dataclass(frozen=True)
class StdioPolicyDenial:
    status_code: int
    detail: str


_NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
_SEMVER_EXACT = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"
)
_PY_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(\[[A-Za-z0-9._,-]+\])?$")
_PEP440_EXACT = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")


@dataclass(frozen=True)
class _Launcher:
    ecosystem: str  # "npm" or "python"
    # Name the package to install; the first positional is then a binary name, not a spec.
    package_options: frozenset[str]
    # Consume the next arg without naming a package.
    value_options: frozenset[str]
    # Install additional packages next to the positional one.
    extra_package_options: frozenset[str] = frozenset()
    # Make the launcher run an arbitrary shell command.
    forbidden_options: frozenset[str] = frozenset()


_NPX = _Launcher(
    ecosystem="npm",
    package_options=frozenset({"-p", "--package"}),
    value_options=frozenset({"--cache", "--registry", "--userconfig", "-w", "--workspace"}),
    forbidden_options=frozenset({"-c", "--call", "--shell-mode"}),
)
_BUNX = _Launcher(
    ecosystem="npm",
    package_options=frozenset({"-p", "--package"}),
    value_options=frozenset({"--cwd"}),
)
_UVX = _Launcher(
    ecosystem="python",
    package_options=frozenset({"--from"}),
    extra_package_options=frozenset({"--with"}),
    value_options=frozenset({"--python", "-p", "--index", "--index-url", "--extra-index-url", "--with-requirements"}),
)
_PIPX_RUN = _Launcher(
    ecosystem="python",
    package_options=frozenset({"--spec"}),
    value_options=frozenset({"--python", "--index-url", "--pip-args"}),
)

# (command basename, required leading subcommand args) -> launcher
_LAUNCHERS: dict[tuple[str, tuple[str, ...]], _Launcher] = {
    ("npx", ()): _NPX,
    ("npm", ("exec",)): _NPX,
    ("pnpm", ("dlx",)): _NPX,
    ("pnpx", ()): _NPX,
    ("yarn", ("dlx",)): _NPX,
    ("bunx", ()): _BUNX,
    ("bun", ("x",)): _BUNX,
    ("uvx", ()): _UVX,
    ("uv", ("tool", "run")): _UVX,
    ("pipx", ("run",)): _PIPX_RUN,
}

ACKNOWLEDGEMENT_REQUIRED = (
    "A custom STDIO MCP server runs as a process on the PipesHub server with that server's "
    "network and file access. Set acknowledgeUnsandboxedExecution to true to confirm you trust "
    "this command."
)


def _command_name(command: str) -> str:
    name = os.path.basename(command.strip()).lower()
    for suffix in (".cmd", ".exe"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _resolve_launcher(command: str, args: list[str]) -> tuple[_Launcher, list[str]] | None:
    name = _command_name(command)
    for (launcher_cmd, subcommand), launcher in _LAUNCHERS.items():
        if name == launcher_cmd and tuple(args[: len(subcommand)]) == subcommand:
            return launcher, args[len(subcommand):]
    return None


def is_pinned_npm_spec(spec: str) -> bool:
    """`name@1.2.3` or `@scope/name@1.2.3`; no tags, ranges, aliases, git or URLs."""
    at = spec.rfind("@")
    if at <= 0:
        return False
    name, version = spec[:at], spec[at + 1:]
    return bool(_NPM_NAME.match(name)) and bool(_SEMVER_EXACT.match(version))


def is_pinned_python_spec(spec: str) -> bool:
    """`name==1.2.3` or `name@1.2.3` (uvx form); no ranges, `latest`, VCS or URLs."""
    if "==" in spec:
        name, _, version = spec.partition("==")
    elif "@" in spec:
        name, _, version = spec.partition("@")
    else:
        return False
    return bool(_PY_NAME.match(name.strip())) and bool(_PEP440_EXACT.match(version.strip()))


def _package_specs(launcher: _Launcher, launcher_args: list[str]) -> tuple[list[str], str | None]:
    """Return (package specs to check, error). Only launcher options before the package count."""
    specs: list[str] = []
    replaces_positional = False
    positional: str | None = None
    i = 0
    while i < len(launcher_args):
        arg = launcher_args[i]
        if arg == "--":
            positional = launcher_args[i + 1] if i + 1 < len(launcher_args) else None
            break
        if not arg.startswith("-"):
            positional = arg
            break
        option, has_inline, inline_value = arg.partition("=")
        if option in launcher.forbidden_options:
            return [], f"The launcher option {option} runs an arbitrary shell command and is not allowed."
        takes_value = option in launcher.value_options
        names_package = option in launcher.package_options or option in launcher.extra_package_options
        if takes_value or names_package:
            if has_inline:
                value = inline_value
            elif i + 1 < len(launcher_args):
                i += 1
                value = launcher_args[i]
            else:
                return [], f"The launcher option {option} is missing its value."
            if names_package:
                specs.append(value)
                replaces_positional = replaces_positional or option in launcher.package_options
        i += 1

    if not replaces_positional:
        if positional is None:
            return [], "The launcher arguments do not name a package to run."
        specs.append(positional)
    return specs, None


def unpinned_launcher_package(command: str, args: list[str]) -> str | None:
    """Error message when `command args` fetches a package that is not pinned to an exact
    version, or None when it is pinned or the command is not a package launcher."""
    resolved = _resolve_launcher(command, args)
    if resolved is None:
        return None
    launcher, launcher_args = resolved
    specs, error = _package_specs(launcher, launcher_args)
    if error:
        return error
    is_pinned = is_pinned_npm_spec if launcher.ecosystem == "npm" else is_pinned_python_spec
    example = "name@1.2.3 or @scope/name@1.2.3" if launcher.ecosystem == "npm" else "name==1.2.3"
    for spec in specs:
        if not is_pinned(spec):
            return (
                f"Package '{spec}' must be pinned to an exact version (for example {example}). "
                "Tags such as 'latest', version ranges, git and URL specs are not allowed."
            )
    return None


def self_hosted_stdio_policy(payload: MCPServerInstanceConfig) -> StdioPolicyDenial | None:
    """Custom STDIO allowed with explicit acknowledgement and an exactly pinned launcher package."""
    if not payload.acknowledge_unsandboxed_execution:
        return StdioPolicyDenial(HttpStatusCode.BAD_REQUEST.value, ACKNOWLEDGEMENT_REQUIRED)
    error = unpinned_launcher_package(payload.command or "", list(payload.args or []))
    if error:
        return StdioPolicyDenial(HttpStatusCode.BAD_REQUEST.value, error)
    return None


def deny_custom_stdio_policy(payload: MCPServerInstanceConfig) -> StdioPolicyDenial | None:
    return StdioPolicyDenial(
        HttpStatusCode.FORBIDDEN.value,
        "Custom STDIO MCP servers are not available on this deployment. "
        "Use a catalog server or a remote (streamable HTTP) MCP server.",
    )


def allow_existing_stdio_launch(instance: dict[str, Any]) -> StdioPolicyDenial | None:
    """Self-hosted: stored custom instances keep launching; they are re-validated on their next edit."""
    return None


def deny_custom_stdio_launch(instance: dict[str, Any]) -> StdioPolicyDenial | None:
    """Called only for STDIO launches not sourced from a catalog template."""
    return StdioPolicyDenial(
        HttpStatusCode.FORBIDDEN.value,
        f"Custom STDIO MCP server '{instance.get('name') or instance.get('_id')}' is not allowed to run "
        "on this deployment.",
    )


def template_override_denial(
    payload: MCPServerInstanceConfig, template: MCPServerTemplate
) -> StdioPolicyDenial | None:
    """Catalog templates declare no configurable launch parameters, so command/args are fixed."""
    if payload.command and payload.command != template.command:
        return StdioPolicyDenial(
            HttpStatusCode.BAD_REQUEST.value,
            f"The command of the catalog MCP server '{template.type_id}' cannot be changed. "
            "Remove 'command' from the request, or create a custom MCP server instead.",
        )
    if payload.args and list(payload.args) != list(template.args):
        return StdioPolicyDenial(
            HttpStatusCode.BAD_REQUEST.value,
            f"The arguments of the catalog MCP server '{template.type_id}' cannot be changed. "
            "Remove 'args' from the request, or create a custom MCP server instead.",
        )
    return None
