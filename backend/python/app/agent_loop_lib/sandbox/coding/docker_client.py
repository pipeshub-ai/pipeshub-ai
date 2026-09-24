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
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

__all__ = [
    "DockerClientProvider",
    "get_default_provider",
    "reset_default_provider",
]

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DockerClientProvider:
    """One lazily-created ``docker.DockerClient`` per process, plus caches
    for ``ensure_image``/``ensure_egress_network`` and a bounded executor
    for blocking docker-py calls.

    Thread-safe: the underlying ``docker-py`` client uses ``requests.Session``
    which is thread-safe for read-only operations; container operations are
    already serialized by the Docker daemon itself."""

    def __init__(self, *, max_workers: int = 4) -> None:
        self._client: Any | None = None
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="docker-sandbox",
        )
        self._image_cache: set[str] = set()
        self._network_cache: set[str] = set()
        self._closed = False

    @property
    def client(self) -> Any:
        """Lazy, thread-safe access to the shared ``DockerClient``."""
        if self._client is not None:
            return self._client
        if self._closed:
            raise RuntimeError("DockerClientProvider is closed")
        with self._lock:
            if self._client is None:
                import docker
                self._client = docker.from_env()
                logger.debug("DockerClientProvider: created shared docker client")
            return self._client

    async def run_blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
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

    async def pull_image(self, image: str) -> None:
        """Pull ``image`` from the registry and update the cache."""
        await self.run_blocking(self.client.images.pull, image)
        self._image_cache.add(image)
        logger.info("DockerClientProvider: pulled image %s", image)

    async def ensure_egress_network(self, network_name: str) -> str:
        """Ensure the install-phase egress network exists; return its name.

        A user-defined bridge, never the caller's default Docker network,
        so sibling services on a compose deployment (mongo, arango, redis)
        stay unreachable by name from an install container.

        Cached per process after the first success — this used to run a
        `networks.list` on every single install.
        """
        if network_name in self._network_cache:
            return network_name
        try:
            if await self._network_exists(network_name):
                self._network_cache.add(network_name)
                return network_name
            await self.run_blocking(
                self.client.networks.create,
                name=network_name,
                driver="bridge",
                internal=False,
                labels={"agent_loop.sandbox": "egress"},
                check_duplicate=True,
            )
            logger.info("DockerClientProvider: created egress network %s", network_name)
        except Exception as exc:
            # Another process may have created it between our list and our
            # create; a second look distinguishes that from a real failure.
            logger.debug("egress network creation raised %s; re-checking", exc)
            if not await self._network_exists(network_name):
                raise
        self._network_cache.add(network_name)
        return network_name

    async def _network_exists(self, network_name: str) -> bool:
        """Exact-name check.

        Docker's `names` filter matches on SUBSTRING, so asking for
        `sandbox_egress` happily returns `pipeshub_sandbox_egress`. Trusting
        it means concluding the network already exists, skipping creation,
        and then failing every container start with an opaque
        `network sandbox_egress not found` — which is what happens on any
        host that also runs the PipesHub compose stack.
        """
        networks = await self.run_blocking(
            self.client.networks.list, names=[network_name],
        )
        return any(getattr(n, "name", None) == network_name for n in networks)

    async def ping(self) -> bool:
        """Check Docker daemon reachability."""
        try:
            await self.run_blocking(self.client.ping)
            return True
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
            except Exception:
                pass
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
    global _default_provider
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
