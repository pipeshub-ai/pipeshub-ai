"""The deployment's machine credentials by role, in Python (SEC-C-10, ADR-CRED-01; Node: ``credential-roles.ts``).

Node generates one credential set per role and admits each only where that role calls:

- ``worker``: the harness worker, the inline claimer, the grant auditor and Node; claims runs, calls the broker and
  the egress control plane;
- ``scrape``: the deployment's Prometheus; reads ``/metrics`` only;
- ``egress-proxy``: mints a run's gateway token and reads the operator egress stops;
- ``sandbox-broker``: registers a sandbox's bindings with the egress proxy.

A process with ``SECRET_KEY`` reads any role's set from configuration. The egress proxy and the broker hold no
``SECRET_KEY`` (ADR-KEY-02): they read the one document Node seals for them under their own key, which holds their
own credential and only digests of the credentials they admit, so neither can present another role's credential.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import TYPE_CHECKING, Protocol

from app.config.constants.service import config_node_constants
from app.harness.journal_node.credentials import (
    ConfiguredWorkerCredential,
    WorkerCredentialUnavailable,
)
from app.utils.encryption.component_key import COMPONENT_DOCUMENTS, component_identity
from harness_core.sandbox.auth import AUTHORIZATION, bearer
from harness_core.telemetry.exposition import METRICS_PATH

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.config.configuration_service import ConfigurationService

ROLES = ("worker", "scrape", "egress-proxy", "sandbox-broker")
ROLE_DOCUMENTS = {
    "worker": config_node_constants.AGENT_HARNESS_WORKER_CREDENTIAL.value,
    "scrape": "/services/agentHarness/roleCredentials/scrape",
    "egress-proxy": "/services/agentHarness/roleCredentials/egressProxy",
    "sandbox-broker": "/services/agentHarness/roleCredentials/sandboxBroker",
}
# Who each component's own listener admits: control routes, then ``/metrics``. Node seals digests of exactly these.
COMPONENT_ACCEPTS: dict[str, dict[str, tuple[str, ...]]] = {
    "egress-proxy": {"control": ("worker", "sandbox-broker"), "metrics": ("scrape",)},
    "sandbox-broker": {"control": ("worker",), "metrics": ("scrape",)},
}
STALE_LIMIT_S = 15 * 60


class CredentialHolder(Protocol):
    async def worker_credential(self) -> str: ...


class RoleVerifier(Protocol):
    async def accepts(self, roles: tuple[str, ...], presented: str | None) -> bool: ...


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PlatformRoleVerifier:
    """Verifies against the role sets in configuration; only a process holding ``SECRET_KEY`` can read them."""

    def __init__(self, config_service: ConfigurationService) -> None:
        self._sets = {role: ConfiguredWorkerCredential(config_service, key=key) for role, key in ROLE_DOCUMENTS.items()}

    async def accepts(self, roles: tuple[str, ...], presented: str | None) -> bool:
        for role in roles:
            if await self._sets[role].accepts(presented):
                return True
        return False


class ComponentCredentials:
    """The document Node seals for one sandbox-plane component: its own credential and the digests it admits.

    Read on each use (cached briefly), so a rotation needs no restart; an unreadable store serves the last read for
    ``STALE_LIMIT_S`` (as the worker credential does) and then refuses."""

    def __init__(
        self,
        config_service: ConfigurationService,
        component: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], float] = lambda: time.time() * 1000,
        stale_limit_s: float = STALE_LIMIT_S,
    ) -> None:
        self._config_service = config_service
        self._key = COMPONENT_DOCUMENTS[component]
        self.component = component
        self._clock = clock
        self._wall_ms = wall_ms
        self._stale_limit_s = stale_limit_s
        self._last: tuple[dict[str, object], float] | None = None

    async def document(self) -> dict[str, object]:
        try:
            document = await self._config_service.get_config(self._key, raise_on_error=True)
        except Exception:
            if self._last is not None and self._clock() - self._last[1] <= self._stale_limit_s:
                return self._last[0]
            return {}
        current = document if isinstance(document, dict) else {}
        self._last = (current, self._clock())
        return current

    async def worker_credential(self) -> str:
        """This component's own credential (the name keeps the ``WorkerCredentialProvider`` port)."""
        credential = (await self.document()).get("credential")
        current = credential.get("current") if isinstance(credential, dict) else None
        if not isinstance(current, str) or not current:
            raise WorkerCredentialUnavailable(f"the {self.component} credential is not provisioned yet")
        return current

    async def accepts(self, roles: tuple[str, ...], presented: str | None) -> bool:
        if not presented:
            return False
        accepts = (await self.document()).get("accepts")
        if not isinstance(accepts, dict):
            return False
        digest = _digest(presented)
        now = self._wall_ms()
        matched = False
        for role in roles:
            for entry in accepts.get(role) or []:
                if not isinstance(entry, dict):
                    continue
                until = entry.get("validUntil")
                if until is not None and (not isinstance(until, int | float) or now >= until):
                    continue
                value = entry.get("digest")
                # Every entry is compared, so the time taken does not say which role or slot matched.
                matched |= isinstance(value, str) and hmac.compare_digest(value, digest)
        return matched


class RoleCredentialAuth:
    """``BrokerAuth`` and ``BrokerAuthVerifier`` by role: presents the holder's credential; admits ``metrics`` roles
    on ``/metrics`` and ``control`` roles everywhere else."""

    def __init__(
        self,
        holder: CredentialHolder | None,
        verifier: RoleVerifier | None,
        *,
        control: tuple[str, ...],
        metrics: tuple[str, ...] = ("scrape",),
    ) -> None:
        self._holder = holder
        self._verifier = verifier
        self._control = control
        self._metrics = metrics

    async def headers(self) -> Mapping[str, str]:
        if self._holder is None:
            raise WorkerCredentialUnavailable("this process presents no credential")
        return {AUTHORIZATION: f"Bearer {await self._holder.worker_credential()}"}

    async def verify(self, headers: Mapping[str, str], path: str = "") -> bool:
        if self._verifier is None:
            return False
        roles = self._metrics if path == METRICS_PATH else self._control
        return bool(roles) and await self._verifier.accepts(roles, bearer(headers))


async def await_component_document(
    config_service: ConfigurationService, *, timeout_s: float = 300.0, poll_s: float = 2.0
) -> bool:
    """On the sandbox plane, waits (bounded) for the document Node seals for this component: in Compose the proxy and
    the broker start before Node. True at once off the sandbox plane; False when it never came (the listener then
    refuses every caller until it does)."""
    import asyncio

    from harness_core.logs import HarnessLog

    identity = component_identity()
    if identity is None:
        return True
    credentials = ComponentCredentials(config_service, identity[0])
    log = HarnessLog.named("app.harness.credential_roles")
    deadline = time.monotonic() + timeout_s
    logged = False
    while True:
        try:
            _ = await credentials.worker_credential()
            return True
        except WorkerCredentialUnavailable:
            if time.monotonic() >= deadline:
                log.error("component.document_missing", component=identity[0], choice="start_refusing_callers")
                return False
            if not logged:
                logged = True
                log.info("component.document_waiting", component=identity[0])
            await asyncio.sleep(poll_s)


def seal_component_document(component: str, key: str, document: dict[str, object]) -> str:
    """The ciphertext Node stores for a component (``ComponentCredentialPublisher``), for a Node-less stack (the
    sandbox-plane smoke) and for tests: the same cipher, the same key."""
    import json
    import logging

    from app.utils.encryption.component_key import component_cipher_key
    from app.utils.encryption.encryption_service import EncryptionService

    return EncryptionService("aes-256-gcm", component_cipher_key(component, key), logging.getLogger(__name__)).encrypt(
        json.dumps(document)
    )


def process_holder(config_service: ConfigurationService) -> CredentialHolder:
    """What this process presents: the component's own credential on the sandbox plane, else the worker's."""
    identity = component_identity()
    if identity is not None:
        return ComponentCredentials(config_service, identity[0])
    return ConfiguredWorkerCredential(config_service)


def worker_client_auth(config_service: ConfigurationService) -> RoleCredentialAuth:
    """A client of the broker or the egress control plane: presents this process's credential, admits nobody."""
    return RoleCredentialAuth(process_holder(config_service), None, control=(), metrics=())


def component_server_auth(config_service: ConfigurationService, component: str) -> RoleCredentialAuth:
    """A sandbox-plane listener: its own credential out, the digests Node sealed for it in."""
    credentials = ComponentCredentials(config_service, component)
    accepts = COMPONENT_ACCEPTS[component]
    return RoleCredentialAuth(credentials, credentials, control=accepts["control"], metrics=accepts["metrics"])


def listener_auth(config_service: ConfigurationService, component: str) -> RoleCredentialAuth:
    """The broker's or the proxy's own listener: by digests when it runs as its component (ADR-KEY-02), else (a
    from-source or legacy run holding ``SECRET_KEY``) against the role sets in configuration."""
    if component_identity() is not None:
        return component_server_auth(config_service, component)
    accepts = COMPONENT_ACCEPTS[component]
    return RoleCredentialAuth(
        process_holder(config_service), PlatformRoleVerifier(config_service),
        control=accepts["control"], metrics=accepts["metrics"],
    )


def scrape_only_auth(config_service: ConfigurationService) -> RoleCredentialAuth:
    """A gateway's ``/metrics``: the scraper's credential only."""
    return RoleCredentialAuth(None, PlatformRoleVerifier(config_service), control=(), metrics=("scrape",))


__all__ = [
    "COMPONENT_ACCEPTS",
    "ROLES",
    "ROLE_DOCUMENTS",
    "ComponentCredentials",
    "CredentialHolder",
    "PlatformRoleVerifier",
    "RoleCredentialAuth",
    "RoleVerifier",
    "await_component_document",
    "component_server_auth",
    "listener_auth",
    "process_holder",
    "seal_component_document",
    "scrape_only_auth",
    "worker_client_auth",
]
