"""Value types of the sandbox broker ports (ARCHITECTURE §4.2).

Labels (but ``LIFETIME_LABEL``), mount sources and lease holders are opaque strings: the broker never
interprets them, so any embedding application (PipesHub, Loom) can use its
own tenancy scheme.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import (  # noqa: TC003 - the wire codec resolves these at runtime
    Mapping,
    Sequence,
)
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import ConfigDict, Field

# Every field of a request a peer sends narrows what a sandbox may do: one release's broker or proxy refuses a
# newer peer's field it does not know rather than enforce less than was asked (QA18-F1, VS-7/VS-8).
STRICT_WIRE: ConfigDict = ConfigDict(extra="forbid")
# The one label a broker reads: seconds after creation past which no live holder keeps the sandbox, so the broker's
# own sweep removes it even when the holder died (a crashed worker's MCP runners).
LIFETIME_LABEL = "pipeshub.sandbox.lifetimeS"
# The other label a broker reads: which of its configured farewells it runs in the sandbox before it removes one no
# holder released (a kept sign-in whose worker died: the engine's own logout, so revocation never needs that worker).
FAREWELL_LABEL = "pipeshub.sandbox.farewell"
# Namespaces a sandbox runtime reads as its own configuration (gVisor's flag and mount annotations, Kata's
# hypervisor config): a spec label there is refused, so no adapter that carries labels as annotations (a CRI pod,
# a future runtime) lets a caller reconfigure the boundary that contains it.
RUNTIME_RESERVED_LABEL_PREFIXES: tuple[str, ...] = ("dev.gvisor.", "io.katacontainers.")


@dataclass(frozen=True, slots=True)
class SandboxFarewell:
    """A command a broker runs in a sandbox it removes on its own (expiry, reap), best effort and bounded. Exit 0:
    done; ``NOTHING_TO_DO``: there was nothing to end; anything else: it failed. Its output is never read."""

    NOTHING_TO_DO: ClassVar[int] = 3
    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    timeout_s: float = 20.0
    # A sandbox carrying it that this broker neither holds nor keeps warm (left from before a broker restart) is
    # ended this long after the broker first saw it; ``None``: only expiry and reaps end it.
    orphan_after_s: int | None = None


class RuntimeMode(StrEnum):
    DOCKER_HARDENED = "docker"
    GVISOR = "gvisor"
    MICROVM = "microvm"
    K8S = "k8s"
    MANAGED = "managed"


@dataclass(frozen=True, slots=True)
class Resources:
    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    cpus: float = 1.0
    memory_mb: int = 1024
    pids: int = 256
    # Size of each scratch mount and of /tmp; scratch is memory-backed where the adapter can.
    scratch_mb: int = 512

    def __post_init__(self) -> None:
        if self.cpus <= 0 or self.memory_mb <= 0 or self.pids <= 0 or self.scratch_mb <= 0:
            raise ValueError("sandbox resources must be positive")


@dataclass(frozen=True, slots=True)
class ImageResources:
    """What an engine's release asks of its sandboxes, over the controller's default (``Resources``): ``cpus`` in
    place of the default (opencode: its boot is CPU-bound, WP-17), ``min_memory_mb`` as a floor (Qwen Code: V8's
    tier-up of its tree-sitter-bash parser peaks near 1.7 GB, R23PERF-FU-2). An org's ``ResourceCaps`` still apply
    after it."""

    cpus: float | None = None
    min_memory_mb: int | None = None

    def __post_init__(self) -> None:
        if (self.cpus is not None and self.cpus <= 0) or (self.min_memory_mb is not None and self.min_memory_mb <= 0):
            raise ValueError("an engine's sandbox resources must be positive")

    def apply(self, resources: Resources) -> Resources:
        return replace(
            resources,
            cpus=resources.cpus if self.cpus is None else self.cpus,
            memory_mb=max(resources.memory_mb, self.min_memory_mb or 0),
        )


@dataclass(frozen=True, slots=True)
class ResourceCaps:
    """Ceilings an org or agent puts on a sandbox (C4.10 ``sandbox.*``); None: the template's value."""

    cpus: float | None = None
    memory_mb: int | None = None
    pids: int | None = None
    scratch_mb: int | None = None

    def apply(self, resources: Resources) -> Resources:
        """``resources`` lowered by each cap; a cap never raises the template's value."""

        def low(cap: float | None, value: float) -> float:
            return value if cap is None else min(cap, value)

        return replace(
            resources,
            cpus=low(self.cpus, resources.cpus),
            memory_mb=int(low(self.memory_mb, resources.memory_mb)),
            pids=int(low(self.pids, resources.pids)),
            scratch_mb=int(low(self.scratch_mb, resources.scratch_mb)),
        )


class EgressDefault(StrEnum):
    DENY = "deny"
    # Unrestricted egress through the adapter's own egress network: today's
    # `SANDBOX_ALLOW_NETWORK` behaviour, kept so the legacy route is unchanged.
    ALLOW = "allow"


@dataclass(frozen=True, slots=True)
class GitAccess:
    """A git host reached only as one repository's smart-HTTP service (GIT-FLOW, GT-02/GT-03).

    ``repository`` is its path on the host (``owner/name``, a ``.git`` suffix optional in requests). Without
    ``push_branch`` the sandbox may only fetch (a setup phase's clone); with it, it may also push, and only
    fast-forwards of ``refs/heads/<push_branch>``: no other ref, no tag, no deletion, no force-push.
    """

    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    repository: str
    push_branch: str | None = None


class SiteMode(StrEnum):
    """How an allowed website is reached (WP-63): read-only, or every method."""

    READ = "read"
    READ_WRITE = "read-write"


@dataclass(frozen=True, slots=True)
class SiteAccess:
    """An allowed website an admin named (WP-63); its destination's ``host`` may be ``*.domain`` (subdomains only).

    The proxy ends the sandbox's TLS for it with its own authority and checks every request inside: ``read`` admits
    GET, HEAD, OPTIONS and git's fetch (``git-upload-pack``), never a push, and drops the sandbox's own credentials;
    ``read-write`` admits every method. ``paths`` limits it to path prefixes. ``opaque`` (read-write only): an
    uninspected tunnel for a client that cannot trust the authority, whose TLS name must still be the ``CONNECT``'s.
    ``held``: the run read private data (the flow rule, SEC-A-01), so every request that could carry it waits until
    the person allows the site for the run (the site card): a write is refused ``site_upload_held`` and a read
    ``site_request_held``, except a registry's fixed-content read, until the proxy holds a site grant.
    """

    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    mode: SiteMode = SiteMode.READ
    paths: tuple[str, ...] = ()
    opaque: bool = False
    held: bool = False

    def __post_init__(self) -> None:
        if self.held and self.opaque:
            raise ValueError("only an inspected website is held")


@dataclass(frozen=True, slots=True)
class EgressDestination:
    """``upstream``: where the proxy forwards this destination for this sandbox only.

    The run's tool endpoint is served by the process hosting the run (E2), so its
    address belongs to the sandbox, not to the deployment. The caller of the broker
    is trusted; the sandbox never sees the value.

    ``git``: the destination is a git host and every request to it is checked as git (``GitAccess``).
    """

    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    host: str
    ports: tuple[int, ...] = (443,)
    upstream: str | None = None
    # Left off the wire when unset, so a peer one release older still accepts every binding without git.
    git: Annotated[GitAccess | None, Field(exclude_if=lambda v: v is None)] = None
    # WP-63: an allowed website, port 443; left off the wire when unset, as ``git`` is.
    site: Annotated[SiteAccess | None, Field(exclude_if=lambda v: v is None)] = None


class CredentialInjection(StrEnum):
    """How the egress proxy puts a route's credential on what the sandbox sent with the placeholder (OWD-1)."""

    # ``Authorization: Bearer <placeholder>`` becomes ``Bearer <token>`` (the gateways).
    BEARER = "bearer"
    # ``Authorization: Basic`` whose password is the placeholder becomes ``Basic`` of the credential, ``user:password``
    # (a package registry): the sandbox's own user name is not kept.
    BASIC = "basic"
    # The placeholder anywhere in a header value or the request target becomes the credential (a program's own
    # secret, as an MCP server reads it from its environment); several routes may share a destination.
    SUBSTITUTE = "substitute"
    # A PostgreSQL connection to the destination: the proxy signs in upstream with the credential (JSON ``user``,
    # ``password``) and the sandbox's client never sees an authentication request.
    POSTGRES = "postgres"


@dataclass(frozen=True, slots=True)
class TokenRoute:
    """A placeholder the sandbox sends that the egress proxy swaps for a real token."""

    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    placeholder: str
    destination: str
    credential_ref: str
    injection: CredentialInjection = CredentialInjection.BEARER


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """What the sandbox may reach. Data only: the egress proxy (P5.4) enforces it.

    An adapter that cannot enforce a field refuses the spec
    (`PolicyUnenforceable`) rather than running with less isolation than asked.
    """

    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    default: EgressDefault = EgressDefault.DENY
    destinations: tuple[EgressDestination, ...] = ()
    token_routes: tuple[TokenRoute, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()

    @property
    def needs_proxy(self) -> bool:
        return bool(self.destinations or self.token_routes or self.forbidden_patterns)


@dataclass(frozen=True, slots=True)
class MountSpec:
    """A directory in the sandbox.

    ``source`` is an opaque relative key into the broker's persistent store
    (for example ``<tenant>/<session>/workspace``); ``None`` is an empty
    scratch directory that lives as long as the sandbox. ``size_mb``: a
    scratch directory's own size instead of ``Resources.scratch_mb`` (the
    handed-over repositories' folder, WP-63, is sized to the per-repository
    cap).
    """

    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    target: str
    source: str | None = None
    read_only: bool = False
    size_mb: int | None = None

    def __post_init__(self) -> None:
        if self.size_mb is not None and (self.size_mb <= 0 or self.source is not None):
            raise ValueError("only a scratch mount has a size of its own, and it is positive")


@dataclass(frozen=True, slots=True)
class LeaseRef:
    """Who holds the sandbox; a higher fence adopts it, a lower one is refused."""

    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    holder: str
    fence: int


def _frozen_labels(labels: Mapping[str, str]) -> Mapping[str, str]:
    # A private copy, so a caller mutating its own dict cannot change a spec in flight.
    return dict(labels)


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    __pydantic_config__: ClassVar[ConfigDict] = STRICT_WIRE
    image_digest: str
    mode: RuntimeMode
    resources: Resources = field(default_factory=Resources)
    egress: EgressPolicy = field(default_factory=EgressPolicy)
    mounts: Sequence[MountSpec] = ()
    # Opaque correlation and tenancy labels (org, session, run, attempt for
    # PipesHub): proxy binding, audit and `reap` filter on them.
    labels: Mapping[str, str] = field(default_factory=lambda: _frozen_labels({}))
    # C2.20: the content digest (``sha256:<hex>``) the image ``image_digest`` names must have. The broker checks
    # the image the sandbox was created from, never the tag, and refuses any other (``image_digest_mismatch``).
    image_pin: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mounts", tuple(self.mounts))
        object.__setattr__(self, "labels", _frozen_labels(self.labels))

    def validation_errors(self) -> list[str]:
        """Shape problems every adapter refuses; adapter-specific checks come on top."""
        errors: list[str] = []
        if not self.image_digest.strip():
            errors.append("image_digest is empty")
        if self.image_pin is not None and not is_image_digest(self.image_pin):
            errors.append(f"image_pin {self.image_pin!r} is not sha256:<64 hex>")
        targets: set[str] = set()
        for mount in self.mounts:
            target = mount.target
            if not target.startswith("/") or posixpath.normpath(target) != target or target == "/":
                errors.append(f"mount target {target!r} must be a normalised absolute path other than /")
            if target in targets:
                errors.append(f"mount target {target!r} is used twice")
            targets.add(target)
            if mount.source is not None and not is_safe_source_key(mount.source):
                errors.append(f"mount source {mount.source!r} is not a safe relative key")
        errors.extend(
            f"label key {key!r} is empty or has whitespace"
            for key in self.labels
            if not key or any(ch.isspace() for ch in key)
        )
        errors.extend(
            f"label key {key!r} is in a sandbox runtime's reserved namespace"
            for key in self.labels
            if key.lower().startswith(RUNTIME_RESERVED_LABEL_PREFIXES)
        )
        return errors

    def mount_for(self, path: str) -> MountSpec | None:
        """The mount that holds ``path``: the deepest target covering it, as a kernel resolves nested mounts."""
        path = posixpath.normpath(path)
        covering = [m for m in self.mounts if path == m.target or path.startswith(m.target + "/")]
        return max(covering, key=lambda m: len(m.target), default=None)

    def seedable(self, path: str) -> bool:
        """Where ``SandboxHandle.seed_tar`` may write: read-only scratch mounts only."""
        mount = self.mount_for(path)
        return mount is not None and mount.read_only and mount.source is None


_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def is_image_digest(value: str) -> bool:
    return _IMAGE_DIGEST.fullmatch(value) is not None


def is_safe_source_key(key: str) -> bool:
    if not key or key.startswith("/") or "\\" in key or "\x00" in key:
        return False
    parts = key.split("/")
    return all(part not in ("", ".", "..") for part in parts)


_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,199}")


@dataclass(frozen=True, slots=True)
class OrgSandboxErasure:
    """What erasing an org took from one broker (CLEANUP-G1): its sandboxes ended, its folder tree (0 or 1) removed."""

    sandboxes: int
    folders: int


def is_safe_segment(value: str) -> bool:
    """One path segment of a persistent folder (an install, org or session id): never ``..``, never a slash."""
    return _SEGMENT.fullmatch(value) is not None and ".." not in value


# `BrokerHealth.detail` flag: the broker keeps a `MountSpec.source` directory beyond
# the sandbox's life, so a later sandbox of the same session finds its files.
PERSISTENT_MOUNTS = "persistentMounts"
# `BrokerHealth.detail` key: the pinned engines (``kind@version``, comma-separated) whose image the broker can
# neither find nor pull. Absent: the broker does not say, and a run finds out when it acquires.
ENGINES_UNAVAILABLE = "enginesUnavailable"
# `BrokerHealth.detail` key (ADR-SEC-01 D7): the engines among ``ENGINES_UNAVAILABLE`` held back because their profile
# is signed by no key this install trusts, not for want of an image, comma-separated. Absent: the broker does not say.
ENGINES_UNTRUSTED = "enginesUntrusted"
# `BrokerHealth.detail` key: whether coding engines have their sandbox here, ``ready`` or ``missing``. An engine
# reaches its model and tool gateways only through the egress proxy; a broker with none (Helm today: no proxy, no
# agent-sandbox, P5.3) can run code sandboxes but no engine. Absent: the broker does not say.
ENGINE_SANDBOX = "engineSandbox"
ENGINE_SANDBOX_MISSING = "missing"
ENGINE_SANDBOX_READY = "ready"
# `BrokerHealth.detail` key (C2.20): ``kind@version=sha256:<hex>`` per pinned engine whose image the broker has,
# comma-separated: the digest a run of that engine pins (Node at claim) and the broker holds its sandbox to.
ENGINE_IMAGES = "engineImages"
# `BrokerHealth.detail` key, present only while gVisor is offered: ``on`` when the daemon registers ``runsc`` with
# ``--oci-seccomp`` (Docker's seccomp profile then filters the guest's syscalls inside the Sentry as well), ``off``
# when it does not. Off is still the gVisor boundary, one layer short: admins are warned (ADR-OWD-03, R32-SECCOMP).
GVISOR_SYSCALL_FILTER = "gvisorSyscallFilter"


@dataclass(frozen=True, slots=True)
class BrokerHealth:
    ok: bool
    modes: frozenset[RuntimeMode]
    active: int = 0
    # Probe results for operators (engine version, runtimes); never secrets.
    detail: Mapping[str, str] = field(default_factory=lambda: _frozen_labels({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", _frozen_labels(self.detail))


def engines_unavailable(health: BrokerHealth) -> frozenset[str] | None:
    """The engines ``health`` names as unrunnable here; ``None`` when the broker does not report it."""
    raw = health.detail.get(ENGINES_UNAVAILABLE)
    if raw is None:
        return None
    return frozenset(part for part in raw.split(",") if part)


def engine_image_digests(health: BrokerHealth) -> dict[str, str] | None:
    """Each engine's image digest as ``health`` reports it (``kind@version`` -> digest); ``None`` when it does not
    say. A malformed entry is dropped, never trusted."""
    raw = health.detail.get(ENGINE_IMAGES)
    if raw is None:
        return None
    images: dict[str, str] = {}
    for entry in raw.split(","):
        ref, sep, digest = entry.partition("=")
        if sep and ref and is_image_digest(digest):
            images[ref] = digest
    return images


def engine_images_detail(images: Mapping[str, str]) -> str:
    """``ENGINE_IMAGES``'s value for ``images`` (``kind@version`` -> digest), in a stable order."""
    return ",".join(f"{ref}={digest}" for ref, digest in sorted(images.items()))


__all__ = [
    "ENGINES_UNAVAILABLE",
    "ENGINES_UNTRUSTED",
    "ENGINE_IMAGES",
    "ENGINE_SANDBOX",
    "ENGINE_SANDBOX_MISSING",
    "ENGINE_SANDBOX_READY",
    "GVISOR_SYSCALL_FILTER",
    "FAREWELL_LABEL",
    "LIFETIME_LABEL",
    "PERSISTENT_MOUNTS",
    "RUNTIME_RESERVED_LABEL_PREFIXES",
    "BrokerHealth",
    "CredentialInjection",
    "EgressDefault",
    "EgressDestination",
    "EgressPolicy",
    "GitAccess",
    "ImageResources",
    "LeaseRef",
    "MountSpec",
    "OrgSandboxErasure",
    "ResourceCaps",
    "Resources",
    "RuntimeMode",
    "SandboxFarewell",
    "SandboxSpec",
    "SiteAccess",
    "SiteMode",
    "TokenRoute",
    "engine_image_digests",
    "engine_images_detail",
    "engines_unavailable",
    "is_image_digest",
    "is_safe_segment",
    "is_safe_source_key",
]
