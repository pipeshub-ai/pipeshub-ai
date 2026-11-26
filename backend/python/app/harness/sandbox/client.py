"""How a PipesHub process reaches its sandboxes: the deployment's provider (``SANDBOX_PROVIDER``, C2.14) through
``WORKER_PROVIDERS``; for Docker, the sandbox broker at its configured endpoint, with the worker credential."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, cast

from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.harness.credential_roles import worker_client_auth
from app.harness.placement import deployment_region
from harness_core.logs import HarnessLog
from harness_core.sandbox.failover import FailoverSandboxBroker, SandboxProvider
from harness_core.sandbox.provider import (
    PROVIDER_ENV,
    BrokerSandboxProvider,
    SandboxProviderFactory,
    SandboxProviderKind,
    provider_kind,
)
from harness_core.sandbox.remote.client import RemoteSandboxBroker

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from harness_core.sandbox.ports import SandboxBroker

_LOG = HarnessLog.named("app.harness.sandbox.client")


async def configured_endpoint(
    config_service: ConfigurationService, service: str, key: str = "endpoint", *, raise_on_error: bool
) -> str | None:
    """``/services/endpoints`` → ``<service>.<key>``, the same document as every other service's URL.

    ``raise_on_error`` is the caller's choice (F3): True when an outage read as "not configured"
    would send traffic to a fallback address or cache it."""
    endpoints = cast(
        "object",
        await config_service.get_config(config_node_constants.ENDPOINTS.value, raise_on_error=raise_on_error),  # pyright: ignore[reportUnknownMemberType]
    )
    entry = cast("dict[str, object]", endpoints).get(service) if isinstance(endpoints, dict) else None
    url = cast("dict[str, object]", entry).get(key) if isinstance(entry, dict) else None
    return url if isinstance(url, str) and url else None


async def broker_endpoint(config_service: ConfigurationService) -> str:
    # ProcessBrokerClient caches the client: an outage must not pin the fallback address for the process's life.
    found = await configured_endpoint(config_service, "sandboxBroker", raise_on_error=True)
    return found or DefaultEndpoints.SANDBOX_BROKER_ENDPOINT.value


async def open_broker_client(config_service: ConfigurationService) -> RemoteSandboxBroker:
    endpoint = await broker_endpoint(config_service)
    auth = worker_client_auth(config_service)
    client = await RemoteSandboxBroker.open(endpoint, auth, region=deployment_region())
    _LOG.info("sandbox_broker.client_opened", endpoint=endpoint, modes=sorted(client.modes()))
    return client


async def open_engine_broker(config_service: ConfigurationService) -> SandboxBroker:
    """The engine runs' sandbox broker, retrying a failing create (CH-16); when ``sandboxBroker.fallbackEndpoint``
    names a second broker (another provider), a create the first cannot serve goes there.

    A fallback that cannot be opened at start leaves the primary alone, logged: no run waits on it."""
    primary = await open_broker_client(config_service)
    providers = [SandboxProvider("primary", primary)]
    fallback_url = await configured_endpoint(config_service, "sandboxBroker", "fallbackEndpoint", raise_on_error=False)
    if fallback_url is not None and fallback_url.rstrip("/") != (await broker_endpoint(config_service)).rstrip("/"):
        try:
            auth = worker_client_auth(config_service)
            fallback = await RemoteSandboxBroker.open(fallback_url, auth, region=deployment_region())
        except Exception as exc:
            _LOG.warning("sandbox_broker.fallback_unavailable", endpoint=fallback_url, errorType=type(exc).__name__)
        else:
            _LOG.info("sandbox_broker.fallback_opened", endpoint=fallback_url, modes=sorted(fallback.modes()))
            providers.append(SandboxProvider("fallback", fallback))
    return FailoverSandboxBroker(providers)


# C2.14: the providers a worker can reach. A Docker deployment's sandboxes are behind the sandbox broker (its fallback
# broker too); Kubernetes, managed and microVM providers are refused by name until they are built.
WORKER_PROVIDERS: SandboxProviderFactory[ConfigurationService, SandboxBroker] = SandboxProviderFactory(
    {SandboxProviderKind.DOCKER: open_engine_broker}, where="this worker"
)


async def open_sandbox_provider(config_service: ConfigurationService) -> BrokerSandboxProvider[SandboxBroker]:
    """The deployment's sandbox provider: every PipesHub caller that runs sandboxes opens it here.

    ``SandboxProviderUnavailable`` when ``SANDBOX_PROVIDER`` names a kind this worker cannot reach."""
    return await WORKER_PROVIDERS.open(provider_kind(os.getenv(PROVIDER_ENV)), config_service)


class ProcessBrokerClient:
    """The process's sandbox provider, opened on first use.

    The legacy ``run_code`` path and the MCP runner have no DI container to hand it one, so it builds its own
    configuration service the way the harness worker does.
    """

    def __init__(self) -> None:
        self._client: BrokerSandboxProvider[SandboxBroker] | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> BrokerSandboxProvider[SandboxBroker]:
        async with self._lock:
            if self._client is None:
                from app.harness.assembly.services import HarnessServices

                self._client = await open_sandbox_provider(HarnessServices().config_service())
            return self._client

    async def aclose(self) -> None:
        """Close the provider's clients, if it was opened; the next ``get`` opens it again."""
        async with self._lock:
            client, self._client = self._client, None
        if client is not None:
            await client.aclose()


process_broker_client = ProcessBrokerClient()

__all__ = [
    "WORKER_PROVIDERS",
    "ProcessBrokerClient",
    "broker_endpoint",
    "configured_endpoint",
    "open_broker_client",
    "open_engine_broker",
    "open_sandbox_provider",
    "process_broker_client",
]
