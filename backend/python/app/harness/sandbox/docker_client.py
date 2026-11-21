"""Process-wide Docker client provider for the coding sandbox.

Replaces the per-call ``docker.from_env()`` / ``client.close()`` pattern in
``DockerCodingSandbox`` with a lazily-created, thread-safe singleton client,
an image-presence cache, an egress-network cache, and a dedicated bounded
``ThreadPoolExecutor`` so blocking ``docker-py`` calls never starve
``asyncio``'s default pool (which other subsystems share).

Usage::

    provider = DockerClientProvider()       # or the module-level default
    client = provider.client                # lazy, thread-safe
    await provider.ensure_image("my-img")   # cached
    result = await provider.run_blocking(client.containers.run, ...)
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, TypeVar

from app.core.org_context import spawn_detached
from harness_core.supply_chain.images import layers_of

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DockerClientProvider",
    "get_default_provider",
    "reset_default_provider",
]

logger = logging.getLogger(__name__)

T = TypeVar("T")


def proxied_docker_client(token_env: str | None = None) -> Any:  # noqa: ANN401 - docker-py is untyped
    """``docker.from_env()``, carrying a caller credential of the Docker API policy proxy when ``DOCKER_HOST`` points at
    it over TCP: ``token_env`` names it (the legacy executor's ``DOCKER_PROXY_TOKEN`` by default, SEC-A-04; the sandbox
    broker's ``DOCKER_PROXY_BROKER_TOKEN``, R38IDENT-FU-1). A client on a unix socket sends none."""
    import docker

    from app.sandbox.docker_proxy import (
        ENV_CALLER_TOKEN,
        caller_token,
        executor_calls_anonymously,
    )
    from app.utils.security_profile import warn_once

    client = docker.from_env()
    if (os.environ.get("DOCKER_HOST") or "").startswith("tcp://"):
        name = token_env or ENV_CALLER_TOKEN
        if name == ENV_CALLER_TOKEN and executor_calls_anonymously():
            # R51-COMPAT: a release before SEC-A-04 called the proxy with no credential; the compatibility proxy
            # admits it only while no credential is configured on either side.
            warn_once(
                f"{name} is not set: the code sandbox calls the Docker API proxy without a credential, as releases "
                "before SEC-A-04 did. Run ./install.sh --upgrade once to generate it."
            )
            return client
        client.api.headers["Authorization"] = f"Bearer {caller_token(name=name)}"
    return client


class DockerClientProvider:
    """One lazily-created ``docker.DockerClient`` per process, plus caches
    for ``ensure_image``/``ensure_egress_network`` and a bounded executor
    for blocking docker-py calls.

    Thread-safe: the underlying ``docker-py`` client uses ``requests.Session``
    which is thread-safe for read-only operations; container operations are
    already serialized by the Docker daemon itself."""

    def __init__(self, *, max_workers: int = 4, caller_token_env: str | None = None) -> None:
        self._token_env = caller_token_env
        self._client: Any | None = None
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="docker-sandbox",
        )
        self._image_cache: set[str] = set()
        self._pulls: dict[str, asyncio.Future[None]] = {}
        self._network_cidrs: dict[str, list[str]] = {}
        self._closed = False

    @property
    def client(self) -> Any:  # noqa: ANN401 - docker-py is untyped
        """Lazy, thread-safe access to the shared ``DockerClient``."""
        if self._client is not None:
            return self._client
        if self._closed:
            raise RuntimeError("DockerClientProvider is closed")
        with self._lock:
            if self._client is None:
                self._client = proxied_docker_client(self._token_env)
                logger.debug("DockerClientProvider: created shared docker client")
            return self._client

    async def run_blocking(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T:
        """Run a blocking docker-py call on the dedicated executor,
        preventing it from monopolizing asyncio's default pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, lambda: fn(*args, **kwargs),
        )

    async def ensure_image(self, image: str) -> bool:
        """Check (and cache) whether ``image`` is present on the daemon.
        Returns True if present, False if not.  Does NOT pull — callers
        that want to pull should call ``pull_image`` separately."""
        if image in self._image_cache:
            return True
        try:
            await self.run_blocking(self.client.images.get, image)
            self._image_cache.add(image)
            logger.debug("DockerClientProvider: image %s present (cached)", image)
            return True
        except Exception:
            return False

    async def image_labels(self, image: str) -> dict[str, str]:
        """The labels of the image ``image`` names now: never cached, since a tag can be moved to other bytes."""
        inspected: dict[str, Any] = await self.run_blocking(self.client.api.inspect_image, image)
        config: dict[str, Any] = inspected.get("Config") or {}
        labels: dict[str, Any] = config.get("Labels") or {}
        return {str(k): str(v) for k, v in labels.items()}

    async def image_digests(self, image: str) -> tuple[str, ...]:
        """Every content digest the image ``image`` names now answers to: its id and the digests of its repo
        digests (a registry's manifest digest). Never cached, for the same reason as the labels."""
        inspected: dict[str, Any] = await self.run_blocking(self.client.api.inspect_image, image)
        repo_digests: list[Any] = inspected.get("RepoDigests") or []
        found = [str(inspected.get("Id") or ""), *(str(ref).rpartition("@")[2] for ref in repo_digests)]
        return tuple(dict.fromkeys(d for d in found if d))

    async def image_layers(self, image: str) -> tuple[str, ...]:
        """The layer digests of the image ``image`` names now (``RootFS.Layers``), bottom first; empty when the
        daemon does not have it. An image's base is read from these (ADR-SEC-01 D5), never from a label."""
        try:
            inspected: dict[str, Any] = await self.run_blocking(self.client.api.inspect_image, image)
        except Exception:
            return ()
        return layers_of(inspected)

    async def pull_image(self, image: str) -> None:
        """Pull ``image`` from the registry and update the cache. One pull per image at a time (C5.01): an acquire
        that finds the broker's start-up pre-pull still running waits for it rather than starting another."""
        running = self._pulls.get(image)
        if running is None:
            # Detached: every acquire of the image waits on this one pull, whoever asked first.
            running = self._pulls[image] = spawn_detached(self._pull(image))
            running.add_done_callback(lambda _: self._pulls.pop(image, None))
        await asyncio.shield(running)

    async def _pull(self, image: str) -> None:
        await self.run_blocking(self.client.images.pull, image)
        self._image_cache.add(image)
        logger.info("DockerClientProvider: pulled image %s", image)

    async def ensure_egress_network(self, network_name: str) -> str:
        """Ensure the egress bridge exists; return its name.

        A user-defined bridge, never the caller's default Docker network,
        with inter-container traffic off. It filters nothing by address:
        that happens inside each container (see ``egress_firewall``).
        Cached per process after the first success.
        """
        await self.egress_network_cidrs(network_name)
        return network_name

    async def egress_network_cidrs(self, network_name: str) -> list[str]:
        """IPv4 subnets of the egress bridge, creating it if needed. Empty
        when they can't be read; callers must then not join the bridge,
        since the gateway may sit outside the statically blocked ranges."""
        cached = self._network_cidrs.get(network_name)
        if cached is not None:
            return cached
        # Imported here: the legacy coding package imports this module through its
        # docker_client shim, and the broker process never loads that package otherwise.
        from app.agent_loop_lib.sandbox.coding.egress_firewall import (
            ensure_egress_network_sync,
        )

        cidrs = await self.run_blocking(
            ensure_egress_network_sync, self.client, network_name,
            {"agent_loop.sandbox": "egress"},
        )
        if cidrs:
            self._network_cidrs[network_name] = cidrs
        return cidrs

    @staticmethod
    def docker_host() -> str:
        """The endpoint `docker.from_env()` builds the client from, named in
        errors so the reader knows which address did not answer."""
        return os.environ.get("DOCKER_HOST") or "unix:///var/run/docker.sock"

    async def ping(self) -> bool:
        """Check Docker daemon reachability.

        `ensure_image` returning False cannot tell an absent image from an
        endpoint nothing is listening on, since a dead `DOCKER_HOST` fails
        `images.get` the same way; callers about to blame the image ask
        this first.
        """
        try:
            # docker-py's own `ping` is `self._result(...) == 'OK'`, so a 200
            # with another body is a daemon that answered but is not one;
            # discarding the result would send the caller back to blaming the
            # image, which is the confusion this method exists to end.
            return bool(await self.run_blocking(self.client.ping))
        except Exception:
            return False

    def close(self) -> None:
        """Release the client and executor. Idempotent, and terminal — a
        closed provider is not reusable, since its executor is gone."""
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                logger.debug("DockerClientProvider: closing the client failed: %s", type(exc).__name__)
            self._client = None
        self._executor.shutdown(wait=False)
        logger.debug("DockerClientProvider: closed")


# The singleton gets the atexit hook; instances created directly (tests,
# an injected provider) are owned by whoever made them.
_default_provider: DockerClientProvider | None = None
_default_lock = threading.Lock()


def get_default_provider(*, max_workers: int = 4) -> DockerClientProvider:
    """The process-wide provider.

    Shared deliberately: `max_workers` doubles as the ceiling on concurrent
    blocking docker operations, which only bounds anything if every sandbox
    in the process draws from the same pool.
    """
    global _default_provider  # noqa: PLW0603 - the process-wide singleton
    if _default_provider is not None:
        return _default_provider
    with _default_lock:
        if _default_provider is None:
            _default_provider = DockerClientProvider(max_workers=max_workers)
            atexit.register(_default_provider.close)
        return _default_provider


def reset_default_provider() -> None:
    """Drop the process-wide provider. For tests only."""
    global _default_provider
    with _default_lock:
        provider, _default_provider = _default_provider, None
    if provider is not None:
        provider.close()
