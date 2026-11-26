"""Environment definitions and their setup (C20.18 / G-17).

An **environment** is what a run's sandbox needs before the engine starts: a setup script (the toolchain, the
dependencies), non-secret environment variables, and the folders outside the workspace the setup fills (its caches).
It is defined at agent level (the agent builder) or at repository level (the repository's ``devcontainer.json``,
of which ``parse_devcontainer`` reads a subset). The base image is the engine's own release image: an environment
never picks another (C2.07 pins it, ADR-SEC-01 verifies it).

The setup is a trusted job: it runs in the turn's fresh sandbox before the person's workspace is materialised and
before the engine starts, so nothing a person or a model wrote can shape its output. It runs in every session.

Keeping what a setup left in its cache folders for the next session is an extension point, ``EnvironmentSnapshotter``
(GA-09): given one, ``prepare_environment`` asks it to restore the definition's kept state instead of running the setup,
and to keep the state of a setup that ran. ``EnvironmentDefinition.digest`` is the key such a store reads: what the
setup is and the image it runs on, so a changed definition or a new engine image never reuses old state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

from harness_core.contracts.canonical_json import canonical_json
from harness_core.engines.errors import EngineRefusal
from harness_core.engines.git_checkout import run_in
from harness_core.engines.snapshots import SnapshotPolicy
from harness_core.sandbox.errors import SandboxError
from harness_core.security.token_patterns import VENDOR_TOKEN_PATTERNS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.logs import HarnessLog
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.workspace.types import ObjectRef

EnvironmentLevel = Literal["agent", "repository"]
PreparedHow = Literal["restored", "set_up", "no_setup"]

SETUP_TIMEOUT_S: Final = 900.0
MAX_SETUP_BYTES: Final = 64 * 1024
MAX_ENV_VARS: Final = 64
MAX_ENV_VALUE_BYTES: Final = 4096
SETUP_FAILED: Final = "environment_setup_failed"
PIN_INVALID: Final = "environment_invalid"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# What the harness sets for the engine (its proxy, its trust bundle, its gateways): an environment never overrides it.
_RESERVED: Final = frozenset({"PATH", "HOME", "USER", "SHELL", "PWD", "TMPDIR", "NO_PROXY", "no_proxy", "NODE_USE_ENV_PROXY"})
_RESERVED_PREFIXES: Final = ("PXH_", "PIPESHUB_", "GIT_", "HTTP_", "HTTPS_", "http_", "https_", "SSL_", "OPENAI_", "ANTHROPIC_")
_RESERVED_SUFFIXES: Final = ("_CA_BUNDLE", "_CA_CERTS", "_PROXY")
# Non-secret variables only (G-17): a secret goes through a credential route, never the environment.
_SECRET_NAME = re.compile(r"(TOKEN|SECRET|PASSW(OR)?D|PASSPHRASE|CREDENTIAL|API_?KEY|PRIVATE_?KEY|AUTH)", re.IGNORECASE)
# devcontainer.json keys read (the subset); every other key is reported, never silently applied.
_DEVCONTAINER_READ: Final = frozenset(
    {"name", "image", "containerEnv", "remoteEnv", "onCreateCommand", "updateContentCommand", "postCreateCommand"}
)
_DEVCONTAINER_COMMANDS: Final = ("onCreateCommand", "updateContentCommand", "postCreateCommand")


class EnvironmentInvalid(ValueError):
    """A definition the harness refuses to run: ``reason`` names why."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class EnvironmentSetupFailed(EngineRefusal):
    """The environment's setup exited non-zero: the engine does not start on a half-built toolchain."""

    reason_code = SETUP_FAILED


def check_env(env: Mapping[str, str]) -> dict[str, str]:
    """The definition's variables, refused when one is reserved for the harness or looks like a secret."""
    if len(env) > MAX_ENV_VARS:
        raise EnvironmentInvalid(f"an environment sets at most {MAX_ENV_VARS} variables", reason="environment_too_large")
    checked: dict[str, str] = {}
    for name, value in env.items():
        if not _NAME.fullmatch(name):
            raise EnvironmentInvalid(f"{name!r} is not a variable name", reason="environment_variable_invalid")
        if name in _RESERVED or name.startswith(_RESERVED_PREFIXES) or name.endswith(_RESERVED_SUFFIXES):
            raise EnvironmentInvalid(f"{name} is set by PipesHub", reason="environment_variable_reserved")
        if _SECRET_NAME.search(name) or any(p.pattern.search(value) for p in VENDOR_TOKEN_PATTERNS):
            raise EnvironmentInvalid(
                f"{name} looks like a secret; connect it as a credential instead", reason="environment_variable_secret"
            )
        if len(value.encode()) > MAX_ENV_VALUE_BYTES or "\x00" in value:
            raise EnvironmentInvalid(f"{name}'s value is too long or not text", reason="environment_variable_invalid")
        checked[name] = value
    return checked


@dataclass(frozen=True, slots=True)
class EnvironmentDefinition:
    """One environment: its setup script, its variables and the folders outside the workspace its setup fills."""

    environment_id: str
    level: EnvironmentLevel
    setup: str | None = None
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    cache_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.environment_id):
            raise EnvironmentInvalid("an environment id is a plain name", reason="environment_id_invalid")
        if self.setup is not None and (not self.setup.strip() or len(self.setup.encode()) > MAX_SETUP_BYTES):
            raise EnvironmentInvalid("a setup script is non-empty and at most 64 KiB", reason="environment_setup_invalid")
        object.__setattr__(self, "env", check_env(self.env))
        if self.cache_paths:
            try:
                _ = SnapshotPolicy(self.cache_paths)
            except ValueError as invalid:
                raise EnvironmentInvalid(str(invalid), reason="environment_cache_invalid") from None

    def policy(self, workspace_root: str, reserved: tuple[str, ...] = ()) -> SnapshotPolicy | None:
        """What the post-setup snapshot holds; ``None`` when the definition names no cache folders. A folder that is
        or holds the workspace (the person's durable files) or the engine's own state is refused."""
        if not self.cache_paths:
            return None
        policy = SnapshotPolicy(self.cache_paths)
        try:
            policy.check_reserved((workspace_root, *reserved))
        except ValueError as invalid:
            raise EnvironmentInvalid(str(invalid), reason="environment_cache_invalid") from None
        return policy

    def digest(self, image_digest: str) -> str:
        """The snapshot's key within the organisation: what the setup is and the image it runs on."""
        body = {
            "schema": 1,
            "image": image_digest,
            "setup": self.setup or "",
            "env": dict(sorted(self.env.items())),
            "caches": sorted(self.cache_paths),
        }
        return "sha256:" + hashlib.sha256(canonical_json(cast("dict[str, object]", body))).hexdigest()  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class DevcontainerSubset:
    """What ``parse_devcontainer`` read, and every key it did not apply (the person is told; nothing is guessed)."""

    definition: EnvironmentDefinition
    ignored: tuple[str, ...]


def _commands(value: object, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(part, str) for part in cast("list[object]", value)):
        # An array is one command with its arguments, never through a shell: quote each one.
        return [" ".join(_quote(part) for part in cast("list[str]", value))]
    if isinstance(value, dict):
        commands = cast("dict[str, object]", value)
        return [line for name in sorted(commands) for line in _commands(commands[name], key)]
    raise EnvironmentInvalid(f"{key} is not a command", reason="devcontainer_invalid")


def _quote(part: str) -> str:
    return "'" + part.replace("'", "'\"'\"'") + "'"


def parse_devcontainer(
    document: bytes, *, environment_id: str, cache_paths: tuple[str, ...] = (), workspace_root: str = "/workspace"
) -> DevcontainerSubset:
    """A repository's ``devcontainer.json`` (its JSON subset: no comments) as a repository-level environment.

    Read: ``containerEnv`` and ``remoteEnv`` (``${localEnv:…}`` and other substitutions are refused: nothing of the
    worker's own environment reaches a sandbox), and ``onCreateCommand``, ``updateContentCommand`` and
    ``postCreateCommand`` in that order as the setup, run in the workspace root. ``image``, ``build``, ``features``,
    ``mounts``, ``runArgs`` and the rest are reported in ``ignored``: the engine's release image is the base."""
    try:
        raw: object = json.loads(document)
    except (ValueError, UnicodeDecodeError):
        raise EnvironmentInvalid("devcontainer.json is not JSON", reason="devcontainer_invalid") from None
    if not isinstance(raw, dict):
        raise EnvironmentInvalid("devcontainer.json is not an object", reason="devcontainer_invalid")
    body = cast("dict[str, object]", raw)
    env: dict[str, str] = {}
    for key in ("containerEnv", "remoteEnv"):
        values = body.get(key) or {}
        if not isinstance(values, dict):
            raise EnvironmentInvalid(f"{key} is not an object", reason="devcontainer_invalid")
        for name, value in cast("dict[str, object]", values).items():
            if not isinstance(value, str) or "${" in value:
                raise EnvironmentInvalid(f"{key}.{name} is not a plain value", reason="devcontainer_invalid")
            env[name] = value
    lines = [line for key in _DEVCONTAINER_COMMANDS for line in _commands(body.get(key), key)]
    setup = f"set -eu\ncd {_quote(workspace_root)}\n" + "\n".join(lines) + "\n" if lines else None
    ignored = tuple(sorted(key for key in body if key not in _DEVCONTAINER_READ or key == "image"))
    definition = EnvironmentDefinition(environment_id, "repository", setup, env, cache_paths)
    return DevcontainerSubset(definition, ignored)


# The members of ``x.pipeshub.environment`` this build reads; any other is an edition's (``PinnedEnvironment.extension``).
_PIN_MEMBERS: Final = frozenset({"environmentId", "level", "setup", "env", "cachePaths", "devcontainer"})


@dataclass(frozen=True, slots=True)
class PinnedEnvironment:
    """What a run's spec pins (``x.pipeshub.environment``, WP-100): the definition, and the pin's members this build
    does not read (an edition's overlay of the environment adds them), handed to the controller as they are."""

    definition: EnvironmentDefinition
    extension: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])
    ignored: tuple[str, ...] = ()


def _strings(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in cast("list[object]", value)):
        raise EnvironmentInvalid(f"environment.{key} is not a list of names", reason=PIN_INVALID)
    return tuple(cast("list[str]", value))


def read_environment(pin: Mapping[str, JsonValue], *, workspace_root: str = "/workspace") -> PinnedEnvironment:
    """One pinned environment as the worker runs it. Node checked it against the schema at admission; every rule is
    checked again here (a stored definition an older Node accepted, or a spec written by hand, is refused, never run
    in part). A repository-level pin carries its ``devcontainer.json``, read through ``parse_devcontainer``."""
    environment_id, level = pin.get("environmentId"), pin.get("level")
    if not isinstance(environment_id, str) or level not in ("agent", "repository"):
        raise EnvironmentInvalid("the pinned environment has no id or level", reason=PIN_INVALID)
    caches = _strings(pin.get("cachePaths"), "cachePaths")
    extension = {name: value for name, value in pin.items() if name not in _PIN_MEMBERS}
    document = pin.get("devcontainer")
    if level == "repository":
        if not isinstance(document, str) or pin.get("setup") is not None or pin.get("env") is not None:
            raise EnvironmentInvalid(
                "a repository environment is its devcontainer.json and nothing else", reason=PIN_INVALID
            )
        subset = parse_devcontainer(
            document.encode(), environment_id=environment_id, cache_paths=caches, workspace_root=workspace_root
        )
        return PinnedEnvironment(subset.definition, extension, subset.ignored)
    if document is not None:
        raise EnvironmentInvalid("an agent environment carries no devcontainer.json", reason=PIN_INVALID)
    setup, env = pin.get("setup"), pin.get("env") or {}
    if (setup is not None and not isinstance(setup, str)) or not isinstance(env, dict):
        raise EnvironmentInvalid("the pinned environment's setup or variables are not text", reason=PIN_INVALID)
    if not all(isinstance(value, str) for value in env.values()):
        raise EnvironmentInvalid("an environment variable's value is text", reason=PIN_INVALID)
    definition = EnvironmentDefinition(environment_id, "agent", setup, cast("dict[str, str]", env), caches)
    return PinnedEnvironment(definition, extension)


def pinned_environment(spec: RunSpec, *, workspace_root: str = "/workspace") -> PinnedEnvironment | None:
    """The run's ``x.pipeshub.environment``; ``None`` when the spec pins none (the run sets nothing up)."""
    extension = cast("dict[str, JsonValue]", (spec.extensions or {}).get("x.pipeshub") or {})
    pin = extension.get("environment")
    if pin is None:
        return None
    if not isinstance(pin, dict):
        raise EnvironmentInvalid("the pinned environment is not an object", reason=PIN_INVALID)
    return read_environment(pin, workspace_root=workspace_root)


class EnvironmentSnapshotter(Protocol):
    """Keeps what an environment's setup left in its cache folders for the next session of the organisation, and
    restores it into a fresh sandbox (GA-09). ``digest`` is ``EnvironmentDefinition.digest`` on the run's image."""

    async def restore(
        self,
        sandbox: SandboxHandle,
        definition: EnvironmentDefinition,
        *,
        tenant: str,
        digest: str,
        policy: SnapshotPolicy,
        log: HarnessLog,
    ) -> ObjectRef | None:
        """Restore the kept state into ``sandbox``; ``None`` when there is none to use (the setup then runs)."""
        ...

    async def keep(
        self,
        sandbox: SandboxHandle,
        definition: EnvironmentDefinition,
        *,
        tenant: str,
        digest: str,
        policy: SnapshotPolicy,
        secrets: Callable[[], tuple[str, ...]],
        log: HarnessLog,
    ) -> ObjectRef | None:
        """Keep the state the setup just left; ``None`` when nothing was kept. ``secrets``: the run's own literals,
        which kept state must never hold."""
        ...


@dataclass(frozen=True, slots=True)
class PreparedEnvironment:
    how: PreparedHow
    env: Mapping[str, str]
    snapshot: ObjectRef | None = None


async def _setup(sandbox: SandboxHandle, definition: EnvironmentDefinition) -> None:
    assert definition.setup is not None
    argv = ["sh", "-c", definition.setup, "pxh-environment-setup"]
    try:
        code, _, err = await run_in(sandbox, argv, dict(definition.env))
    except (SandboxError, OSError, TimeoutError) as exc:
        raise EnvironmentSetupFailed(f"the environment's setup could not run: {type(exc).__name__}") from None
    if code != 0:
        raise EnvironmentSetupFailed(f"the environment's setup exited {code}: {err.strip()[-300:]}")


async def prepare_environment(
    sandbox: SandboxHandle,
    definition: EnvironmentDefinition,
    *,
    tenant: str,
    image_digest: str,
    workspace_root: str,
    snapshots: EnvironmentSnapshotter | None,
    secrets: Callable[[], tuple[str, ...]],
    log: HarnessLog,
    reserved: tuple[str, ...] = (),
) -> PreparedEnvironment:
    """Run the environment's setup in ``sandbox``, or restore its kept state when ``snapshots`` has some. Called on a
    fresh sandbox before the workspace is materialised (the setup reads no person's file)."""
    env = dict(definition.env)
    if definition.setup is None:
        return PreparedEnvironment("no_setup", env)
    policy = definition.policy(workspace_root, reserved)
    digest = definition.digest(image_digest)
    if snapshots is not None and policy is not None:
        ref = await snapshots.restore(sandbox, definition, tenant=tenant, digest=digest, policy=policy, log=log)
        if ref is not None:
            return PreparedEnvironment("restored", env, ref)
    await _setup(sandbox, definition)
    log.info("engine.environment.set_up", environment=definition.environment_id)
    if snapshots is None or policy is None:
        return PreparedEnvironment("set_up", env)
    ref = await snapshots.keep(sandbox, definition, tenant=tenant, digest=digest, policy=policy, secrets=secrets, log=log)
    return PreparedEnvironment("set_up", env, ref)


__all__ = [
    "SETUP_FAILED",
    "DevcontainerSubset",
    "EnvironmentDefinition",
    "EnvironmentInvalid",
    "EnvironmentLevel",
    "EnvironmentSetupFailed",
    "EnvironmentSnapshotter",
    "PIN_INVALID",
    "PinnedEnvironment",
    "PreparedEnvironment",
    "check_env",
    "parse_devcontainer",
    "pinned_environment",
    "prepare_environment",
    "read_environment",
]
