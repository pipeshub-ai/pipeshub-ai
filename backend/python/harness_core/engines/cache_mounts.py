"""Shared read-only caches a run's sandbox mounts beside its workspace: an extension point.

Which caches a run mounts (read from the members its pinned environment carries that this build does not read), where
each is kept, and how each is checked before the engine starts is the ``SharedCacheMounts`` port's; the controller
only mounts what it answers, read-only, passes each mount's git configuration to the engine's git, and asks it to
verify the mounts inside the sandbox. Without the port (``EngineControllerConfig.shared_caches`` unset) a run mounts
none and its tools install without them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.logs import HarnessLog
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.sandbox.types import MountSpec, SandboxSpec

GitConfig = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CacheMount:
    """One cache a sandbox mounts: its name, the read-only mount and what the engine's git reads to use it."""

    name: str
    mount: MountSpec
    git_config: GitConfig = ()

    def __post_init__(self) -> None:
        if not self.mount.read_only:
            raise ValueError("a shared cache is mounted read-only")


class SharedCacheMounts(Protocol):
    """Which shared caches a run mounts, and their check before anything runs on them."""

    async def mounts(self, tenant: str, environment: Mapping[str, JsonValue]) -> Sequence[CacheMount]:
        """The mounts in ``tenant`` that ``environment`` (the pinned environment's members this build does not read)
        names; nothing to mount: empty."""
        ...

    async def verify(
        self, sandbox: SandboxHandle, mounts: Sequence[CacheMount], log: HarnessLog
    ) -> Mapping[str, str]:
        """Each mount checked inside ``sandbox`` before the engine starts; the variables that point the session's tools
        at them. Raises an ``EngineRefusal`` when one may not be used, so the turn stops before the engine starts."""
        ...


def with_mounts(spec: SandboxSpec, mounts: Sequence[CacheMount]) -> SandboxSpec:
    """``spec`` with each cache mounted at its folder."""
    return replace(spec, mounts=(*spec.mounts, *(cache.mount for cache in mounts)))


def mounts_git_config(mounts: Sequence[CacheMount]) -> GitConfig:
    """The git configuration of every mount, in mount order."""
    return tuple(pair for cache in mounts for pair in cache.git_config)


# What a repository name can be continued by: a rule's value followed by one of these is another repository's address.
_NAME_CHARS: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
# Where a session's git reads the mounts' rules: a file the worker writes in the sandbox and the environment includes
# (``include.path``), as the anchored rules are too many for an environment (WP100-FU-8).
MIRRORS_GITCONFIG: Final = "pxh-git/mirrors.gitconfig"


def mirrors_gitconfig(pairs: GitConfig) -> bytes:
    """``pairs`` (the mounts' ``git_config``) as a git config file, each ``insteadOf`` anchored to its whole address.

    git rewrites by the longest ``insteadOf`` prefix, so a rule for ``https://h/o/r`` also rewrote ``https://h/o/r2``
    to ``…/r.git2``, a folder that does not exist, and that repository's clone failed (WP100-FU-8). Each rule's value
    continued by any character a name can hold now maps to itself, a longer match than the rule, unless it is another
    rule's own address. Derived at launch rather than stored, so a mount recorded before the fix is anchored too."""
    spelled = {value for key, value in pairs if key.endswith(".insteadOf")}
    lines: list[str] = []
    for key, value in pairs:
        base, _, name = key.removeprefix("url.").rpartition(".")
        lines += [f'[url "{base}"]', f"\t{name} = {value}"]
    for value in sorted(spelled):
        for char in _NAME_CHARS:
            guard = value + char
            if guard not in spelled:
                lines += [f'[url "{guard}"]', f"\tinsteadOf = {guard}"]
    return ("\n".join(lines) + "\n").encode()


__all__ = [
    "MIRRORS_GITCONFIG",
    "CacheMount",
    "GitConfig",
    "SharedCacheMounts",
    "mirrors_gitconfig",
    "mounts_git_config",
    "with_mounts",
]
