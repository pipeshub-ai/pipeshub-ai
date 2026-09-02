"""Sandbox settings: a single place to parse env-based configuration for
the coding sandbox — replaces the duplicated env reads scattered across
``sandbox_bridge.py`` and ``app/sandbox/manager.py``.

Phase 1 (``EnvSandboxSettingsLoader``): reads from process environment,
same vars the legacy stack used (``SANDBOX_MODE``, ``SANDBOX_DOCKER_IMAGE``,
``SANDBOX_EGRESS_NETWORK``, ``SANDBOX_PIP_INDEX_URL``, ``SANDBOX_NPM_REGISTRY``,
``SANDBOX_ALLOW_NETWORK``, ``E2B_API_KEY``, plus new ``SANDBOX_MAX_TOTAL``,
``SANDBOX_MAX_PER_ORG``).

Phase 4 (``ConfigServiceSandboxSettingsLoader``): per-org settings through
``ConfigurationService`` — interface defined here, body raises
``NotImplementedError`` until wired.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.agent_loop_lib.sandbox.coding.base import SandboxContext

__all__ = [
    "SandboxSettings",
    "SharedSandboxConfig",
    "SandboxSettingsLoader",
    "EnvSandboxSettingsLoader",
    "ConfigServiceSandboxSettingsLoader",
]

logger = logging.getLogger(__name__)

_FALSY_ENV_VALUES = {"0", "false", "no", "off"}

# `load()` runs on every agent build, so this warns once per process — a
# per-request line would bury the logs and get filtered out, which is the
# same as not warning at all.
_warned_about_host_isolation = False

# `SANDBOX_MODE` value -> backend name. The only accepted spellings; anything
# else is a misconfiguration rather than a hint to guess from.
_SANDBOX_MODES = {"LOCAL": "local", "DOCKER": "docker", "E2B": "e2b"}


def _env_bool(key: str, default: bool = True) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSY_ENV_VALUES


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _env_cap(key: str, default: int) -> int | None:
    """A sandbox cap: a positive int, or `None` for unlimited.

    `<= 0` means unlimited rather than "block everything" — an operator
    zeroing a cap is disabling it, and the alternative reading would make
    the platform silently refuse every sandbox.
    """
    value = _env_int(key, default)
    return value if value > 0 else None


class GovernorSettings(BaseModel):
    """Mirrors ``GovernorLimits`` in ``sandbox/governor.py`` — kept as a
    separate settings model so ``SandboxSettings`` has no import-time
    dependency on the governor module."""

    max_total_sandboxes: int | None = 50
    max_per_org: int | None = 10


class SandboxSettings(BaseModel):
    """Unified, validated configuration for the coding sandbox subsystem.

    ``backend_options`` carries per-backend config dicts validated by each
    factory's ``config_model`` at registration time.  The typed
    ``local``/``e2b``/``docker`` fields in ``CodingSandboxConfig`` are
    merged as defaults (backward compat); explicit ``backend_options``
    entries win."""

    backend: str = "local"
    backend_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    allow_network: bool = True
    max_concurrent_per_request: int = 5
    max_lifetime_s: float = 1800.0
    provision_timeout_s: float = 60.0
    governor: GovernorSettings = Field(default_factory=GovernorSettings)


class SharedSandboxConfig(BaseModel):
    """Settings that apply to every backend, handed to each factory as
    ``shared``.

    A typed model rather than a duck-typed object: factories read these
    with `getattr`, so a renamed or misspelled field would otherwise
    degrade to `None` — silently dropping the package allowlist, which
    fails open.
    """

    package_allowlist: list[str] | None = None
    package_denylist: list[str] = Field(default_factory=list)
    allow_network_on_install: bool = True
    # Ceiling for provider-side TTLs (see `E2BCodingSandboxFactory._effective_ttl_s`).
    max_lifetime_s: float | None = None


@runtime_checkable
class SandboxSettingsLoader(Protocol):
    """Async settings loader — implementations read from env, config
    service, or test fixtures."""

    async def load(self, ctx: SandboxContext) -> SandboxSettings: ...


class EnvSandboxSettingsLoader:
    """Phase 1: reads ``SANDBOX_MODE``, ``SANDBOX_DOCKER_IMAGE``, etc. from
    the process environment.  ``ctx`` is accepted for interface compliance
    but ignored (env vars are process-global, not per-org)."""

    # Same env vars sandbox_bridge.py reads today
    _ENV_SANDBOX_MODE = "SANDBOX_MODE"
    _ENV_DOCKER_IMAGE = "SANDBOX_DOCKER_IMAGE"
    _ENV_EGRESS_NETWORK = "SANDBOX_EGRESS_NETWORK"
    _ENV_PIP_INDEX_URL = "SANDBOX_PIP_INDEX_URL"
    _ENV_NPM_REGISTRY = "SANDBOX_NPM_REGISTRY"
    _ENV_ALLOW_NETWORK = "SANDBOX_ALLOW_NETWORK"
    _ENV_E2B_API_KEY = "E2B_API_KEY"
    _ENV_MAX_TOTAL = "SANDBOX_MAX_TOTAL"
    _ENV_MAX_PER_ORG = "SANDBOX_MAX_PER_ORG"
    _ENV_MAX_CONCURRENT = "SANDBOX_MAX_CONCURRENT_PER_REQUEST"
    _ENV_MAX_LIFETIME_S = "SANDBOX_MAX_LIFETIME_S"
    _ENV_PROVISION_TIMEOUT_S = "SANDBOX_PROVISION_TIMEOUT_S"

    _DEFAULT_DOCKER_IMAGE = "pipeshub/sandbox:latest"
    _DEFAULT_EGRESS_NETWORK = "pipeshub_sandbox_egress"
    _DEFAULT_PIP_INDEX_URL = "https://pypi.org/simple"
    _DEFAULT_NPM_REGISTRY = "https://registry.npmjs.org"

    async def load(self, ctx: SandboxContext) -> SandboxSettings:
        backend = self._resolve_backend()

        backend_options: dict[str, dict[str, Any]] = {}

        if backend == "docker":
            backend_options["docker"] = {
                "image": os.environ.get(self._ENV_DOCKER_IMAGE, self._DEFAULT_DOCKER_IMAGE),
                "egress_network": os.environ.get(self._ENV_EGRESS_NETWORK, self._DEFAULT_EGRESS_NETWORK),
                "pip_index_url": os.environ.get(self._ENV_PIP_INDEX_URL, self._DEFAULT_PIP_INDEX_URL),
                "npm_registry": os.environ.get(self._ENV_NPM_REGISTRY, self._DEFAULT_NPM_REGISTRY),
            }
        # No E2B branch: the API key deliberately never enters
        # `SandboxSettings`. This model is logged and dumped freely, and
        # `backend_options` is `dict[str, Any]` with no `SecretStr` to hide
        # behind — `E2BCodingSandboxFactory` reads `E2B_API_KEY` itself.

        return SandboxSettings(
            backend=backend,
            backend_options=backend_options,
            allow_network=_env_bool(self._ENV_ALLOW_NETWORK, default=True),
            max_concurrent_per_request=_env_int(self._ENV_MAX_CONCURRENT, 5),
            max_lifetime_s=_env_float(self._ENV_MAX_LIFETIME_S, 1800.0),
            provision_timeout_s=_env_float(self._ENV_PROVISION_TIMEOUT_S, 60.0),
            governor=GovernorSettings(
                max_total_sandboxes=_env_cap(self._ENV_MAX_TOTAL, 50),
                max_per_org=_env_cap(self._ENV_MAX_PER_ORG, 10),
            ),
        )


    def _resolve_backend(self) -> str:
        """`SANDBOX_MODE` -> backend name, rejecting anything unrecognised.

        Mapping an unknown value to `local` is the most permissive possible
        reading of a value the operator clearly meant to be something else,
        and it silently downgrades to the weakest isolation: `SANDBOX_MODE=docekr`
        would run generated code as a subprocess of this service while the
        operator believes it is containerised. That is worse than an unset
        variable, where at least nobody thinks otherwise.

        Blank reads as unset rather than invalid, matching how shell and
        Compose `${VAR:-default}` treat an empty value.
        """
        configured = os.environ.get(self._ENV_SANDBOX_MODE)
        mode_raw = (configured or "").strip().upper()

        if not mode_raw:
            self._warn_about_implicit_host_isolation()
            return "local"

        backend = _SANDBOX_MODES.get(mode_raw)
        if backend is None:
            raise ValueError(
                f"{self._ENV_SANDBOX_MODE}={configured!r} is not a supported "
                f"coding sandbox. Use one of: "
                f"{', '.join(sorted(v for v in _SANDBOX_MODES.values()))}. "
                f"Leave it unset only if you accept running generated code as "
                f"a subprocess of this service."
            )
        return backend

    @staticmethod
    def _warn_about_implicit_host_isolation() -> None:
        """Say so when nobody chose this backend.

        `local` is `IsolationLevel.HOST`: model-generated code runs as a
        subprocess of the service, with the service's network and filesystem
        reach, bounded only by rlimits. Every shipped docker-compose file
        sets `SANDBOX_MODE=${SANDBOX_MODE:-docker}`, but the Helm chart sets
        it nowhere — so a Helm install silently lands here.

        Only the IMPLICIT fallback warns. An operator who typed `local` has
        made the call knowingly, and nagging them every run would train them
        to filter out the message that matters.
        """
        global _warned_about_host_isolation
        if _warned_about_host_isolation:
            return
        _warned_about_host_isolation = True
        logger.warning(
            "SANDBOX_MODE is not set — falling back to the 'local' coding "
            "sandbox, which runs generated code as a subprocess on this host "
            "(isolation=host: no network namespace, no filesystem boundary "
            "beyond rlimits). Set SANDBOX_MODE=docker for container isolation, "
            "or SANDBOX_MODE=local explicitly to silence this."
        )


class ConfigServiceSandboxSettingsLoader:
    """Phase 4 stub: per-org settings through ``ConfigurationService``.

    Not wired until Phase 4 — instantiating and calling ``load()`` raises
    ``NotImplementedError`` with a clear message rather than silently
    returning defaults, so misconfiguration is caught early."""

    async def load(self, ctx: SandboxContext) -> SandboxSettings:
        raise NotImplementedError(
            "ConfigServiceSandboxSettingsLoader is a Phase 4 stub — "
            "use EnvSandboxSettingsLoader for Phase 1."
        )
