"""Who may decide what an STDIO MCP server runs on the PipesHub host.

A STDIO server is a process spawned by the connectors (tool discovery) and query (agent
loop) services, as the service user. Org admins pick servers; the deployment operator owns
the host. So:

- catalog servers always run the registry's command/args, never a stored or submitted one;
- custom STDIO servers run only when the operator sets ``MCP_ALLOW_CUSTOM_STDIO=true`` in
  the process environment (compose ``.env`` / Helm values), which an org admin cannot reach.
  Turning them on lets administrators run programs on those hosts: an allowed launcher such
  as ``npx -y <package>`` runs whatever that package contains, so nothing here is a sandbox.
  The checks are guard rails against the obvious ways to run arbitrary code: shells, a
  launcher's inline-command flag, an interpreter run through a launcher;
- env var names handed to the child are restricted so a permitted command can't be turned
  into a different one (``NODE_OPTIONS``, ``LD_PRELOAD``, ``PATH``, ...).
"""
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Optional

from app.agents.mcp.models import MCPServerConfig, MCPTransport
from app.agents.mcp.registry import MCPRegistry, get_mcp_registry

ALLOWED_COMMANDS_ENV = "MCP_STDIO_ALLOWED_COMMANDS"
CUSTOM_STDIO_ENV = "MCP_ALLOW_CUSTOM_STDIO"
CUSTOM_STDIO_DISABLED_REASON = "custom_stdio_disabled"
CUSTOM_STDIO_DISABLED_MESSAGE = (
    "Custom STDIO MCP servers are disabled on this deployment because they run a command on the "
    f"PipesHub server. Use a remote (HTTP) MCP server, or ask the operator to set {CUSTOM_STDIO_ENV}=true."
)
_DEFAULT_ALLOWED_COMMANDS = ("npx", "uvx")

# Never allowed, even when listed: a shell turns the argument list back into arbitrary code.
_SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "cmd", "powershell", "pwsh"})
# Launcher flags that execute an inline command instead of a package.
_INLINE_EXEC_FLAGS = {"npx": frozenset({"-c", "--call"})}
# npm options allowed before the package. Anything else may load code (`--node-options`,
# `--script-shell`, `--userconfig`, `--git`), and npm expands abbreviations (`--node-opt`) and
# combined short flags (`-yc`), so only a list of what is allowed holds. Arguments after the
# package go to the server, not to npm.
_NPX_FLAG_OPTIONS = frozenset({
    "--yes", "--no", "--quiet", "--silent", "--verbose", "--prefer-offline", "--prefer-online",
    "--offline", "--ignore-existing", "--no-install", "--legacy-peer-deps", "--no-audit", "--no-fund",
    "--no-update-notifier",
})
# A private registry is allowed: it runs packages, as the public one does.
_NPX_VALUE_OPTIONS = frozenset({"--package", "--loglevel", "--registry", "--cache"})
_NPX_SHORT_FLAGS = frozenset("yqs")
_NPX_SHORT_PACKAGE = "p"
# Run through a launcher (`npx node -e …`, `uvx python -c …`) these execute inline code
# rather than an MCP server package.
_INTERPRETERS = _SHELLS | frozenset({
    "node", "nodejs", "deno", "bun", "tsx", "ts-node", "ruby", "perl", "php", "lua", "osascript",
})
_PYTHON = re.compile(r"(python|pypy)[0-9.]*w?")
# Launcher flags whose value names a package to install or run.
_PACKAGE_FLAGS = {"npx": frozenset({"-p", "--package"}), "uvx": frozenset({"--from", "--with"})}
# Launcher flags that take a separate value which is not a package.
_VALUE_FLAGS = {"uvx": frozenset({"-p", "--python", "--index", "--index-url", "--extra-index-url"})}
_UVX_PYTHON_FLAGS = frozenset({"-p", "--python"})
# On Windows a `.cmd` launcher (npx is `npx.cmd`) runs under cmd.exe, which expands `%VAR%` and
# treats these as command syntax even inside a quoted argument.
_CMD_METACHARACTERS = frozenset('"%^&|<>!\r\n')

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# Variables that load code or redirect where packages come from, so a credential value typed
# into them later would execute on the host.
_DENIED_ENV_NAMES = frozenset({
    "PATH", "HOME", "SHELL", "ENV", "BASH_ENV", "IFS", "CDPATH", "PS4", "PROMPT_COMMAND",
    "CLASSPATH", "JDK_JAVA_OPTIONS", "SHELLOPTS", "COMSPEC",
})
_DENIED_ENV_PREFIXES = (
    "LD_", "DYLD_", "NODE_", "NPM_CONFIG_", "COREPACK_", "DENO_", "BUN_", "PYTHON", "UV_", "PIP_",
    "GIT_", "PERL", "RUBY", "JAVA_", "GCONV_", "YARN_", "PNPM_",
)


class StdioPolicyError(ValueError):
    """An STDIO launch or instance config is not allowed by the deployment's policy; the
    message says why and is safe to show the administrator."""


def custom_stdio_allowed() -> bool:
    # Process env only: ConfigurationService keys are writable by org admins.
    return os.getenv(CUSTOM_STDIO_ENV, "false").strip().lower() == "true"


def allowed_stdio_commands() -> frozenset[str]:
    configured = os.getenv(ALLOWED_COMMANDS_ENV)
    if configured is None:
        return frozenset(_DEFAULT_ALLOWED_COMMANDS)
    return frozenset(entry.strip() for entry in configured.split(",") if entry.strip())


def is_allowed_env_name(name: str) -> bool:
    return (
        bool(_ENV_NAME_RE.match(name))
        and name not in _DENIED_ENV_NAMES
        and not name.startswith(_DENIED_ENV_PREFIXES)
    )


def rejected_env_names(names: Iterable[str]) -> list[str]:
    return sorted({str(name) for name in names if not is_allowed_env_name(str(name))})


def is_custom_stdio(type_id: Optional[str], transport: Any) -> bool:  # noqa: ANN401
    transport_value = transport.value if isinstance(transport, MCPTransport) else transport
    return not type_id and transport_value == MCPTransport.STDIO.value


def instance_disabled_reason(instance: dict[str, Any]) -> Optional[str]:
    """Why a stored instance will not run on this deployment, for list/get responses."""
    if is_custom_stdio(instance.get("typeId"), instance.get("transport")) and not custom_stdio_allowed():
        return CUSTOM_STDIO_DISABLED_REASON
    return None


def resolve_stdio_launch(
    config: MCPServerConfig,
    registry: Optional[MCPRegistry] = None,
) -> tuple[str, list[str]]:
    """The (command, args) to spawn for an STDIO instance, or raise StdioPolicyError.

    Stored ``command``/``args`` are ignored for catalog instances, so records saved before
    this policy (or edited directly in the KV store) cannot override the template. A custom
    instance's command and env names are checked again at every launch, records saved before
    the checks existed included.
    """
    if config.type_id:
        if registry is None:
            registry = get_mcp_registry()
            registry.auto_discover_templates()
        template = registry.get_template(config.type_id)
        if template is None or template.transport != MCPTransport.STDIO or not template.command:
            raise StdioPolicyError(
                f"MCP instance {config.id} references catalog server '{config.type_id}', "
                "which is not a STDIO server in this release's catalog."
            )
        return template.command, list(template.args)

    if not custom_stdio_allowed():
        raise StdioPolicyError(CUSTOM_STDIO_DISABLED_MESSAGE)
    if not config.command:
        raise StdioPolicyError(f"MCP instance {config.id} is STDIO but has no command configured")
    check_stdio_command(config.command, config.args)
    check_env_names([*(config.required_env or []), *(config.optional_env or [])])
    return config.command.strip(), list(config.args or [])


def _executable_name(command: str) -> str:
    """`bash`, `/bin/bash` and `C:\\Windows\\System32\\cmd.exe` all name a shell; `npx.cmd` is npx."""
    name = PureWindowsPath(command).name if "\\" in command else PurePosixPath(command).name
    name = name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        name = name.removesuffix(suffix)
    return name


def check_stdio_command(command: Optional[str], args: Optional[list[str]]) -> None:
    if not command or not command.strip():
        raise StdioPolicyError("A custom STDIO MCP server requires a command.")
    command = command.strip()
    name = _executable_name(command)
    if name in _SHELLS:
        raise StdioPolicyError(f"Shells cannot be used as an MCP server command ({name}).")
    if command not in allowed_stdio_commands():
        raise StdioPolicyError(
            f"The command {command!r} is not allowed for custom STDIO MCP servers. Allowed: "
            f"{', '.join(sorted(allowed_stdio_commands())) or 'none'}. An operator can change this "
            f"with {ALLOWED_COMMANDS_ENV}."
        )
    args = list(args or [])
    blocked_flags = _INLINE_EXEC_FLAGS.get(name, frozenset())
    for arg in args:
        if "\x00" in arg:
            raise StdioPolicyError("MCP server arguments cannot contain NUL characters.")
        flag = arg.split("=", 1)[0]
        if flag in blocked_flags:
            raise StdioPolicyError(f"`{name} {flag}` runs an inline shell command and is not allowed.")
    packages = _npx_packages(args) if name == "npx" else _packages_run(name, args)
    for package in packages:
        _refuse_interpreter(name, package)
    if name == "uvx":
        _check_uvx_python(args)
    if _runs_through_cmd(command):
        for arg in args:
            if any(char in _CMD_METACHARACTERS for char in arg):
                raise StdioPolicyError(
                    f"On Windows `{name}` runs through cmd.exe, so its arguments cannot contain any of "
                    "\" % ^ & | < > ! or a line break."
                )


def check_env_names(names: Optional[Iterable[str]]) -> None:
    """Refuses env var names a credential may not be stored under (see `is_allowed_env_name`)."""
    if rejected := rejected_env_names(names or []):
        raise StdioPolicyError(
            f"Env var names not allowed for STDIO MCP servers: {rejected}. Names must be "
            "upper-case letters, digits and underscores, and cannot change how the process "
            "is loaded (e.g. PATH, LD_*, NODE_*, PYTHON*)."
        )


def _refuse_interpreter(launcher: str, package: str) -> None:
    if _is_interpreter(package):
        raise StdioPolicyError(
            f"`{launcher} {package}` runs {package} itself rather than an MCP server package, which is not allowed."
        )


def _on_windows() -> bool:
    return os.name == "nt"


def _runs_through_cmd(command: str) -> bool:
    if not _on_windows():
        return False
    resolved = shutil.which(command) or command
    return resolved.lower().endswith((".cmd", ".bat"))


def _npx_packages(args: list[str]) -> list[str]:
    """The packages `npx` is asked to install or run, refusing any npm option that isn't
    allowed before the package."""
    packages: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if arg.startswith("--"):
            flag, has_value, value = arg.partition("=")
            if flag in _NPX_VALUE_OPTIONS:
                if not has_value:
                    index += 1
                    value = args[index] if index < len(args) else ""
                if flag == "--package":
                    packages.append(_package_name(value))
                    _refuse_interpreter("npx", packages[-1])
            elif flag not in _NPX_FLAG_OPTIONS:
                raise _npx_option_refused(flag)
        elif arg.startswith("-") and len(arg) > 1:
            letters, has_value, value = arg[1:].partition("=")
            if "c" in letters:
                raise StdioPolicyError(f"`npx {arg}` runs an inline shell command and is not allowed.")
            for position, letter in enumerate(letters):
                if letter == _NPX_SHORT_PACKAGE and position == len(letters) - 1:
                    if not has_value:
                        index += 1
                        value = args[index] if index < len(args) else ""
                    packages.append(_package_name(value))
                    _refuse_interpreter("npx", packages[-1])
                elif letter not in _NPX_SHORT_FLAGS or has_value:
                    raise _npx_option_refused(f"-{letter}")
        else:
            break
        index += 1
    if index < len(args):
        packages.append(_package_name(args[index]))
    return packages


def _npx_option_refused(flag: str) -> StdioPolicyError:
    allowed = sorted({"-y", "-q", "-s", "-p", *_NPX_FLAG_OPTIONS, *_NPX_VALUE_OPTIONS})
    return StdioPolicyError(
        f"The npm option `{flag}` is not allowed before the package of a custom STDIO MCP server. "
        f"Allowed: {', '.join(allowed)}. Options after the package are passed to the server."
    )


def _check_uvx_python(args: list[str]) -> None:
    """`uvx --python` names an interpreter version; a path would run any program on the host."""
    for index, arg in enumerate(args):
        if arg == "--" or not arg.startswith("-"):
            return
        flag, has_value, value = arg.partition("=")
        if flag not in _UVX_PYTHON_FLAGS:
            continue
        if not has_value:
            value = args[index + 1] if index + 1 < len(args) else ""
        if any(sep in value for sep in ("/", "\\")) or value.startswith((".", "~")):
            raise StdioPolicyError(
                f"`uvx {flag}` must name a Python version (such as 3.12), not a path to a program."
            )


def _package_name(spec: str) -> str:
    """`node@20` → node, `@scope/pkg@1.2` → @scope/pkg, `python>=3.12` → python."""
    spec = spec.strip().lower()
    head, at, _version = spec[1:].partition("@") if spec.startswith("@") else spec.partition("@")
    name = f"@{head}" if spec.startswith("@") else head
    return re.split(r"[=<>!~\[;]", name, maxsplit=1)[0].strip()


def _is_interpreter(package: str) -> bool:
    return package in _INTERPRETERS or bool(_PYTHON.fullmatch(package))


def _packages_run(launcher: str, args: list[str]) -> list[str]:
    """What the launcher is asked to install or run: every package-flag value and the first
    positional argument (the command)."""
    package_flags = _PACKAGE_FLAGS.get(launcher, frozenset())
    value_flags = _VALUE_FLAGS.get(launcher, frozenset())
    packages: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        flag, has_value, value = arg.partition("=")
        if arg == "--":
            index += 1
            break
        if flag in package_flags or flag in value_flags:
            if not has_value:
                index += 1
                value = args[index] if index < len(args) else ""
            if flag in package_flags:
                packages.append(_package_name(value))
        elif not arg.startswith("-"):
            break
        index += 1
    if index < len(args):
        packages.append(_package_name(args[index]))
    return packages
