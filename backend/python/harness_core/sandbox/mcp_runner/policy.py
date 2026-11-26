"""What a runner may run, hold and reach, as pure functions. No I/O, no clock, no logging."""

from __future__ import annotations

import hashlib
import hmac
import posixpath
import re
from typing import TYPE_CHECKING

from harness_core.sandbox.egress.policy import policy_errors
from harness_core.sandbox.mcp_runner.errors import (
    RunnerEgressInvalid,
    RunnerEnvUndeclared,
    RunnerOwnerMismatch,
    RunnerSpecInvalid,
    RunnerUnpinned,
)
from harness_core.sandbox.mcp_runner.types import RunnerEgressHost
from harness_core.sandbox.types import (
    CredentialInjection,
    EgressDestination,
    EgressPolicy,
    TokenRoute,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from harness_core.sandbox.egress.sealed import CredentialSealer
    from harness_core.sandbox.mcp_runner.types import (
        MCPRunnerSpec,
        RunnerRequest,
        RunnerSecrets,
    )

# Launchers that download a package when they start; the first element of a tuple is the sub-command.
_ALWAYS_FETCH = frozenset({"npx", "pnpx", "bunx", "uvx"})
_FETCH_SUBCOMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "npm": (("exec",), ("x",)),
    "pnpm": (("dlx",),),
    "yarn": (("dlx",),),
    "pipx": (("run",),),
    "uv": (("tool", "run"),),
    "bun": (("x",),),
}
_REMOTE_SPECIFIER = re.compile(r"^(npm:|jsr:|https?://)", re.IGNORECASE)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
# Set by the runner, or able to change what the server runs or where it connects: never a secret's name.
RESERVED_ENV_NAMES = frozenset(
    {
        "HOME",
        "PATH",
        "PYTHONUNBUFFERED",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
        "NODE_USE_ENV_PROXY",
        "NODE_OPTIONS",
        "NODE_PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
    }
)


def fetching_launcher(command: str, args: Sequence[str]) -> str | None:
    """The launcher that would fetch code at start, or ``None`` when the command runs what the image holds."""
    name = posixpath.basename(command.strip())
    if name in _ALWAYS_FETCH:
        return name
    for sub in _FETCH_SUBCOMMANDS.get(name, ()):
        if tuple(args[: len(sub)]) == sub:
            return f"{name} {' '.join(sub)}"
    if name == "deno" and any(_REMOTE_SPECIFIER.match(arg) for arg in args):
        return "deno remote"
    return None


def check_spec(spec: MCPRunnerSpec) -> None:
    errors: list[str] = []
    if not spec.server_id or not spec.image.strip() or not spec.command.strip():
        errors.append("server id, image and command are required")
    if any("\x00" in part for part in (spec.command, *spec.args)):
        errors.append("argv holds a NUL byte")
    errors.extend(
        f"env name {name!r} is not a plain variable name" for name in sorted(spec.env_names) if not _ENV_NAME.match(name)
    )
    errors.extend(f"env name {name!r} is reserved by the runner" for name in sorted(spec.env_names & RESERVED_ENV_NAMES))
    if errors:
        raise RunnerSpecInvalid("; ".join(errors))
    launcher = fetching_launcher(spec.command, spec.args)
    if launcher is not None:
        raise RunnerUnpinned(f"{launcher!r} fetches code at start; the server must be in the runner image")


def runner_egress_policy(spec: MCPRunnerSpec, routes: Sequence[TokenRoute] = ()) -> EgressPolicy:
    """Deny by default; listed hosts only, each carrying the server's secrets as substitution routes (OWD-1)."""
    if not spec.egress:
        return EgressPolicy()
    policy = EgressPolicy(
        destinations=tuple(EgressDestination(h.host, h.ports, upstream=h.upstream) for h in spec.egress),
        token_routes=tuple(routes),
    )
    errors = policy_errors(policy)
    if errors:
        raise RunnerEgressInvalid("; ".join(errors))
    return policy


def secret_routes(
    spec: MCPRunnerSpec,
    secrets: Mapping[str, str],
    sealer: CredentialSealer,
    placeholder: Callable[[], str],
) -> tuple[dict[str, str], tuple[TokenRoute, ...]]:
    """The server's environment (each secret as a placeholder) and the routes that put the secret back on its way
    to every listed host. The server never holds a secret; the proxy adds it where the placeholder appears in a
    header or the URL (OWD-1). An empty value stays empty."""
    env: dict[str, str] = {}
    routes: list[TokenRoute] = []
    for name, value in sorted(secrets.items()):
        if not value:
            env[name] = value
            continue
        stand_in = placeholder()
        env[name] = stand_in
        sealed = sealer.seal(stand_in, value)
        routes.extend(TokenRoute(stand_in, h.host, sealed, CredentialInjection.SUBSTITUTE) for h in spec.egress)
    return env, tuple(routes)


def parse_egress_host(entry: str) -> RunnerEgressHost:
    """``host`` or ``host:port`` as an admin lists it; anything else is ``runner_egress_invalid``."""
    text = entry.strip()
    host, sep, port = text.rpartition(":") if text.count(":") == 1 else (text, "", "")
    if not sep:
        return RunnerEgressHost(text)
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise RunnerEgressInvalid(f"egress host {entry!r} has a bad port")
    return RunnerEgressHost(host, (int(port),))


def check_request(request: RunnerRequest) -> None:
    """Refuse before anything starts: the spec, the owner, and the secrets' names."""
    check_spec(request.spec)
    if request.key.server_id != request.spec.server_id:
        raise RunnerSpecInvalid("the key and the spec name different servers")
    if request.principal != request.key.owner:
        raise RunnerOwnerMismatch("the secrets belong to someone other than the runner's owner")
    undeclared = request.secrets.names() - request.spec.env_names
    if undeclared:
        raise RunnerEnvUndeclared(f"env names not declared by the server: {sorted(undeclared)}")
    _ = runner_egress_policy(request.spec)


def secrets_fingerprint(secrets: RunnerSecrets, key: bytes) -> str:
    """Tells a rotated secret from the running one without keeping or revealing either."""
    mac = hmac.new(key, digestmod=hashlib.sha256)
    for name, value in sorted(secrets.values().items()):
        mac.update(name.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00")
    return mac.hexdigest()


__all__ = [
    "RESERVED_ENV_NAMES",
    "check_request",
    "check_spec",
    "fetching_launcher",
    "parse_egress_host",
    "runner_egress_policy",
    "secret_routes",
    "secrets_fingerprint",
]
