"""Which pinned engines the Docker sandbox broker cannot run because their image is missing (H1, E9 availability).

An engine's image is on the daemon, or its registry can hand it over on acquire. Neither, and every run of that
engine would fail at sandbox acquisition: an admin-supplied image (Claude Code, D29) that was never built here
is the case this exists for. The broker reports such engines in its health (``ENGINES_UNAVAILABLE``), so the
engine controller and Node's admission refuse them before anything is created.

A registry lookup reads the manifest only (``get_registry_data``); nothing is pulled. A present image is
remembered; a missing one is looked at again after ``recheck_s``, so building it takes effect without a restart.
A lookup that does not answer in time decides nothing: the engine is not listed and acquisition decides.

Each image on the daemon is also reported by its content digest (C2.20, ``digests``): a run of its engine pins
that digest and the broker holds the run's sandbox to it. An image the release pins a digest for (allowlist
``imageDigest``) that answers to another digest is not the release's image: its engines are unavailable.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.clock import Clock

RECHECK_S = 60.0
REGISTRY_TIMEOUT_S = 15.0
_LOG = HarnessLog.named("app.harness.sandbox.engine_images")


class EngineImageAvailability:
    def __init__(
        self,
        provider: DockerClientProvider,
        engines: Mapping[str, str | tuple[str, ...]],
        clock: Clock,
        *,
        recheck_s: float = RECHECK_S,
        registry_timeout_s: float = REGISTRY_TIMEOUT_S,
        uninstalled: Iterable[str] = (),
        untrusted: Iterable[str] = (),
        pins: Mapping[str, str] | None = None,
    ) -> None:
        """``engines``: image -> the engine (or engines: an ACP agent shares a native engine's image) it runs, as
        ``kind@version``. ``uninstalled``: engines this install never runs whatever its images (an ACP engine
        without an installed profile), always reported. ``untrusted``: those of them whose profile is there but signed
        by no key this install trusts. ``pins``: image -> the digest the release pins for it."""
        self._provider = provider
        self._engines = {image: (refs,) if isinstance(refs, str) else tuple(refs) for image, refs in engines.items()}
        self._uninstalled = tuple(uninstalled)
        self.untrusted: tuple[str, ...] = tuple(sorted(untrusted))
        self._clock = clock
        self._recheck_s = recheck_s
        self._registry_timeout_s = registry_timeout_s
        self._missing_since: dict[str, float] = {}
        self._present: set[str] = set()
        self._pins = dict(pins or {})

    async def unavailable(self) -> tuple[str, ...]:
        """The engines whose image this daemon can neither find nor pull, or whose image is not the digest the
        release pins, sorted."""
        found = await asyncio.gather(*(self._missing(image) for image in self._engines))
        missing = {ref for image, gone in zip(self._engines, found, strict=True) if gone for ref in self._engines[image]}
        pinned = [image for image in self._pins if image in self._engines]
        digests = await asyncio.gather(*(self._digest(image) for image in pinned))
        foreign = {ref for image, digest in zip(pinned, digests, strict=True) if digest is None for ref in self._engines[image]}
        return tuple(sorted(missing | foreign | set(self._uninstalled)))

    async def digests(self) -> dict[str, str]:
        """Each engine whose image is on the daemon -> that image's digest (the release's pin when it has one)."""
        images = list(self._engines)
        found = await asyncio.gather(*(self._digest(image) for image in images))
        return {ref: digest for image, digest in zip(images, found, strict=True) if digest for ref in self._engines[image]}

    async def _digest(self, image: str) -> str | None:
        """The digest a sandbox of ``image`` is held to: its id, or the release's pin when the image answers to it.
        ``None``: not on the daemon, or not the pinned image."""
        try:
            answers = await self._provider.image_digests(image)
        except Exception:
            # Absent here (it may still be pulled on acquire): nothing to pin until it is on the daemon.
            return None
        pin = self._pins.get(image)
        if pin is None:
            return answers[0] if answers else None
        if pin in answers:
            return pin
        _LOG.warning("broker.engine_image_digest_mismatch", engine=",".join(self._engines.get(image, ())), image=image)
        return None

    async def _missing(self, image: str) -> bool:
        if image in self._present:
            return False
        checked = self._missing_since.get(image)
        if checked is not None and self._clock.monotonic() - checked < self._recheck_s:
            return True
        present = await self._provider.ensure_image(image)
        if not present:
            try:
                present = await asyncio.wait_for(self._in_registry(image), self._registry_timeout_s)
            except TimeoutError:
                _LOG.warning("broker.engine_image_lookup_timeout", engine=",".join(self._engines[image]))
                return False
        if present:
            self._present.add(image)
            _ = self._missing_since.pop(image, None)
            return False
        if checked is None:
            _LOG.warning("broker.engine_image_unavailable", engine=",".join(self._engines[image]), image=image)
        self._missing_since[image] = self._clock.monotonic()
        return True

    async def prepull(self) -> tuple[str, ...]:
        """Pulls every pinned engine image this daemon lacks and its registry has, so no session waits for a pull
        (C5.01: harness images pre-pulled, never pulled at session time); the images pulled. Run in the background
        when the broker starts: an acquire meanwhile joins the pull in flight. A failed pull is logged; acquisition
        decides for that image as before."""
        images = [image for image in self._engines if not await self._provider.ensure_image(image)]
        wanted = [image for image in images if await self._registry_has(image)]

        async def pull(image: str) -> str | None:
            try:
                await self._provider.pull_image(image)
            except Exception as exc:
                _LOG.warning("broker.engine_image_prepull_failed", engine=",".join(self._engines[image]), errorType=type(exc).__name__)
                return None
            self._present.add(image)
            _ = self._missing_since.pop(image, None)
            _LOG.info("broker.engine_image_prepulled", engine=",".join(self._engines[image]))
            return image

        return tuple(i for i in await asyncio.gather(*(pull(image) for image in wanted)) if i is not None)

    async def _registry_has(self, image: str) -> bool:
        try:
            return await asyncio.wait_for(self._in_registry(image), self._registry_timeout_s)
        except TimeoutError:
            return False

    async def _in_registry(self, image: str) -> bool:
        try:
            _ = await self._provider.run_blocking(self._provider.client.images.get_registry_data, image)
        except Exception:
            # The daemon answers a registry's "no such image", its refusal and its unreachability alike:
            # in each case an acquire could not pull it either.
            return False
        return True


__all__ = ["RECHECK_S", "REGISTRY_TIMEOUT_S", "EngineImageAvailability"]
