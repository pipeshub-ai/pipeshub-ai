"""Release data for engines: the pinned allowlist, the registry built from it, and the default profile.

``allowlist.json`` is the one record of which engine versions a release runs, by
the digest of the binary inside each image; the image build verifies against the
same file. An engine may have several versions side by side (C14.01): its first
entry is the version new conversations start on, and a conversation keeps the
version it started on while that version stays allow-listed. Every engine starts from the release default profile (``policy/release.json``);
org ceilings, the agent and the request only narrow it (E4a, ``harness_core.engines.profiles``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING, Literal, cast, get_args, override

from harness_core.contracts import dump_contract
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.engines.binary import PinnedBinary
from harness_core.engines.isolation import OVERRIDE_ENV, override_on, release_floor
from harness_core.engines.profiles import profile_from_wire
from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.engines.snapshots import DEFAULT_MAX_BYTES, SnapshotPolicy
from harness_core.engines.types import EngineProfile, EngineVersion, is_acp_kind
from harness_core.sandbox.types import ImageResources, is_image_digest
from harness_engines.acp.adapter import acp_adapter
from harness_engines.acp.registry import (
    packaged_acp_agents,
    packaged_unverified_acp_agents,
    release_signed,
)
from harness_engines.claude_code import launch as claude_code_launch
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.codex import launch as codex_launch
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.opencode import launch as opencode_launch
from harness_engines.opencode.adapter import OpencodeAdapter
from harness_engines.opencode.launch import CACHE_HOME
from harness_engines.profile_trust import profile_test_root, unsigned_profiles_allowed
from harness_engines.vendor_rules import RELEASE_KEYS

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from pydantic import JsonValue

    from harness_core.access.types import AccessRouteRef, ModelApi
    from harness_core.engines.ports import EngineAdapter
    from harness_core.engines.types import EngineKind
    from harness_core.logs import HarnessLog
    from harness_engines.acp.profiles import AcpAgentProfile

ALLOWLIST_RESOURCE = "allowlist.json"
Supply = Literal["release", "admin"]
_SUPPLIES: frozenset[str] = frozenset(get_args(Supply))
# The tag suffix of an image built by ``engine-build.sh --candidate`` (CC1): a version under test, never a run image.
CANDIDATE_TAG_SUFFIX = "-candidate"
# The label the same build puts on the image: the broker refuses any image carrying it, whatever its tag (EH1).
CANDIDATE_LABEL = "io.pipeshub.engine.candidate"
# ADR-OPS-01 D9: the registry every engine image is run from, ``{registry}/{bare name}`` (a mirror, an air-gapped
# install's own registry). Unset, the allowlist's bare names.
ENGINE_IMAGE_REGISTRY_ENV = "HARNESS_ENGINE_IMAGE_REGISTRY"


class EngineImageRegistryInvalid(ValueError):
    """``HARNESS_ENGINE_IMAGE_REGISTRY`` is not a registry host with an optional path."""

    reason_code = "engine_image_registry_invalid"


def engine_image_registry(env: Mapping[str, str] | None = None) -> str | None:
    """The configured registry without its trailing ``/``; ``None`` when unset or blank."""
    raw = ((os.environ if env is None else env).get(ENGINE_IMAGE_REGISTRY_ENV) or "").strip().rstrip("/")
    if not raw:
        return None
    _, _, path = raw.partition("/")
    # Docker takes a reference, never a URL: a scheme, a tag or a digest here would name an image no one built.
    if "://" in raw or raw.startswith("/") or "//" in raw or "@" in raw or ":" in path or any(c.isspace() for c in raw):
        raise EngineImageRegistryInvalid(f"{ENGINE_IMAGE_REGISTRY_ENV} must be host[:port][/path], got {raw!r}")
    return raw


def run_image(image: str, env: Mapping[str, str] | None = None) -> str:
    """The name a sandbox of ``image`` is started from: under the configured registry, else ``image`` itself. The
    allowlist (``EngineRelease.image``) keeps the bare name, which the ACP profiles and the image build compare."""
    registry = engine_image_registry(env)
    return f"{registry}/{image}" if registry is not None else image


class CandidateImagePinned(ValueError):
    """An allowlist names a candidate image: a version is admitted by its normal build, never by its candidate."""

    reason_code = "candidate_image_pinned"


def _repository(image: str) -> str:
    name, sep, tag = image.rpartition(":")
    return name if sep and "/" not in tag else image


def candidate_image(image: str, version: str) -> str:
    """The tag ``engine-build.sh --candidate`` gives ``version`` of the engine whose image (or variant) is ``image``."""
    return f"{_repository(image)}:{version}{CANDIDATE_TAG_SUFFIX}"


def _is_candidate(image: str) -> bool:
    return _repository(image) != image and image.endswith(CANDIDATE_TAG_SUFFIX)


@dataclass(frozen=True, slots=True)
class EngineRelease:
    """One allow-listed engine version and the image every run of it uses.

    ``variants`` names every image built for the version (same binary, different runtimes around it);
    ``image`` is one of them, chosen in the allowlist and nowhere else (E0b). ``supply`` says who provides
    the binary: the release (downloaded and verified at build) or the customer's admin (a proprietary
    engine PipesHub may not redistribute, D29; the admin builds the image from their own copy).
    """

    version: EngineVersion
    image: str
    variants: Mapping[str, str] = field(default_factory=dict[str, str])
    supply: Supply = "release"
    # How the coding-engine list names it when the product has no name of its own for the kind (an ACP agent's).
    name: str | None = None
    # The content digest ``image`` must have (C2.20), for a release whose images are published and pulled by digest;
    # unset (images built on each deployment, the admin's for Claude Code), the broker reports the digest it finds
    # and each run pins that one at claim.
    image_digest: str | None = None
    # The CPUs a run's sandbox may use, when the engine needs other than the controller's default (opencode: its
    # boot is CPU-bound, WP-17); unset, the default.
    cpus: float | None = None
    # The least memory a run's sandbox gets, when the engine needs more than the controller's default (Qwen Code:
    # V8's tier-up of its tree-sitter-bash parser peaks near 1.7 GB, R23PERF-FU-2); unset, the default.
    min_memory_mb: int | None = None
    # Earlier versions of the same kind whose sessions this version continues (``resumes``, UP-02, ADR-OPS-01 D6):
    # the release declares its adapter compatible with them, so a run pinned to one that the release no longer
    # lists resumes on this version's image.
    resumes: tuple[str, ...] = ()


def _variants(entry: Mapping[str, object], image: str) -> dict[str, str]:
    raw = cast("dict[str, object]", entry.get("variants") or {})
    variants = {name: str(tag) for name, tag in raw.items() if not name.startswith("_")}
    if variants and image not in variants.values():
        raise ValueError(f"{entry['kind']}@{entry['version']}: image {image} is none of its variants")
    return variants


class DuplicateEngineVersion(ValueError):
    """An allowlist names one engine version twice: which entry's digest and image a run gets would be ambiguous."""

    reason_code = "duplicate_engine_version"


class InvalidEngineSuccessor(ValueError):
    """An entry's ``resumes`` is not a list of other, distinct, no longer listed versions of its own kind."""

    reason_code = "invalid_engine_successor"


def _resumes(entry: Mapping[str, object]) -> tuple[str, ...]:
    raw = entry.get("resumes", [])
    where = f"{entry['kind']}@{entry['version']}"
    if not isinstance(raw, list):
        raise InvalidEngineSuccessor(f"{where}: resumes must be a list of versions")
    versions: list[str] = []
    for old in cast("list[object]", raw):
        if not isinstance(old, str) or not old:
            raise InvalidEngineSuccessor(f"{where}: resumes holds {old!r}, not a version")
        if old == str(entry["version"]):
            raise InvalidEngineSuccessor(f"{where}: resumes names its own version")
        if old in versions:
            raise InvalidEngineSuccessor(f"{where}: resumes names {old} twice")
        versions.append(old)
    return tuple(versions)


def _check_successors(releases: Iterable[EngineRelease]) -> None:
    chosen = tuple(releases)
    listed = {r.version.ref for r in chosen}
    for release in chosen:
        for old in release.resumes:
            if f"{release.version.kind}@{old}" in listed:
                # A listed version always runs itself: moving its runs would put them on another binary unasked.
                raise InvalidEngineSuccessor(
                    f"{release.version.ref}: resumes {old}, which the release still lists; remove it from the "
                    "allowlist first"
                )


def _parse(raw: str) -> tuple[EngineRelease, ...]:
    document = cast("dict[str, object]", json.loads(raw))
    entries = cast("list[dict[str, object]]", document["engines"])
    releases: list[EngineRelease] = []
    seen: set[str] = set()
    for entry in entries:
        version = EngineVersion(
            kind=cast("str", entry["kind"]),  # pyright: ignore[reportArgumentType] - checked by EngineVersion
            version=str(entry["version"]),
            binary_digest=str(entry["binaryDigest"]),
        )
        if version.ref in seen:
            raise DuplicateEngineVersion(f"{version.ref} is pinned twice")
        seen.add(version.ref)
        image = str(entry["image"])
        variants = _variants(entry, image)
        pinned = [tag for tag in (image, *variants.values()) if _is_candidate(tag)]
        if pinned:
            raise CandidateImagePinned(f"{entry['kind']}@{entry['version']}: {pinned[0]} is a candidate image")
        supply = str(entry.get("supply", "release"))
        if supply not in _SUPPLIES:
            raise ValueError(f"{entry['kind']}@{entry['version']}: unknown supply {supply}")
        name = entry.get("name")
        image_digest = entry.get("imageDigest")
        if image_digest is not None and not (isinstance(image_digest, str) and is_image_digest(image_digest)):
            raise ValueError(f"{entry['kind']}@{entry['version']}: imageDigest must be sha256:<64 hex>")
        cpus = entry.get("cpus")
        if cpus is not None and (isinstance(cpus, bool) or not isinstance(cpus, int | float) or cpus <= 0):
            raise ValueError(f"{entry['kind']}@{entry['version']}: cpus must be a positive number")
        min_memory = entry.get("minMemoryMb")
        if min_memory is not None and (isinstance(min_memory, bool) or not isinstance(min_memory, int) or min_memory <= 0):
            raise ValueError(f"{entry['kind']}@{entry['version']}: minMemoryMb must be a positive whole number")
        releases.append(
            EngineRelease(
                version=version,
                image=image,
                variants=variants,
                supply=cast("Supply", supply),
                name=name if isinstance(name, str) and name else None,
                image_digest=image_digest,
                cpus=float(cpus) if cpus is not None else None,
                min_memory_mb=min_memory,
                resumes=_resumes(entry),
            )
        )
    _check_successors(releases)
    return tuple(releases)


def _packaged() -> str:
    return resources.files("harness_engines").joinpath(ALLOWLIST_RESOURCE).read_text()


def release_allowlist(raw: str | None = None) -> tuple[EngineRelease, ...]:
    """The pinned releases; ``raw`` overrides the packaged file (tests, a deployment's own pin)."""
    return _parse(raw if raw is not None else _packaged())


def engines_without_gvisor(env: Mapping[str, str] | None = None) -> bool:
    """Whether this install runs coding engines below the gVisor floor: the dev and test override
    (``policy/release.json`` ``engineIsolation.devOverride``), off unless its variable is ``true``."""
    return override_on((os.environ if env is None else env).get(OVERRIDE_ENV))


def release_default_wire(raw: str | None = None, *, env: Mapping[str, str] | None = None) -> dict[str, JsonValue]:
    """The release default profile's dimensions (no engine): ``policy/release.json``'s ``engineProfile``. Its
    isolation floor is gVisor, so a production install without ``runsc`` refuses engine runs; the documented dev
    override lowers it to hardened runc (``harness_core.engines.isolation``). A deployment pin (``raw``) that
    carries its own ``defaultProfile`` keeps it."""
    if raw is not None:
        document = cast("dict[str, JsonValue]", json.loads(raw))
        if "defaultProfile" in document:
            defaults = cast("dict[str, JsonValue]", document["defaultProfile"])
            return {key: value for key, value in defaults.items() if not key.startswith("_")}
    wire = cast("dict[str, JsonValue]", dump_contract(RELEASE_DEFAULTS.engine_profile))
    wire["isolationMin"] = release_floor(override=engines_without_gvisor(env)).value
    return wire


def release_image_pins(releases: Iterable[EngineRelease] | None = None) -> dict[str, str]:
    """Run image -> the digest the release pins for it (``imageDigest``), for the images that have one."""
    return {
        run_image(r.image): r.image_digest
        for r in (release_allowlist() if releases is None else releases)
        if r.image_digest
    }


def default_versions(releases: Iterable[EngineRelease] | None = None) -> dict[EngineKind, EngineVersion]:
    """Each engine's default version: its first allowlist entry, the one a new conversation starts on."""
    defaults: dict[EngineKind, EngineVersion] = {}
    for release in release_allowlist() if releases is None else releases:
        defaults.setdefault(release.version.kind, release.version)
    return defaults


def release_successors(releases: Iterable[EngineRelease] | None = None) -> dict[str, EngineRelease]:
    """``kind@old`` -> the release whose entry declares it resumes ``old`` (ADR-OPS-01 D6): where a run pinned to a
    version the release no longer lists continues. Two entries declaring one version: the first, as Node's
    ``RELEASE_ENGINE_SUCCESSORS`` reads it (the kind's earlier entry, its default first)."""
    successors: dict[str, EngineRelease] = {}
    for release in release_allowlist() if releases is None else releases:
        for old in release.resumes:
            _ = successors.setdefault(f"{release.version.kind}@{old}", release)
    return successors


class ByEngineRef[V](_Mapping[EngineVersion, V]):
    """A per-version table that also answers for the versions a successor resumes: an exact key first, else the
    value of the successor of ``kind@version`` (``aliases``). A resumed run is pinned to the old version with its
    old binary digest, which the release no longer records, so the alias is by ref. Iterates the exact keys."""

    __slots__ = ("_aliases", "_exact")

    def __init__(self, exact: _Mapping[EngineVersion, V], aliases: _Mapping[str, EngineVersion]) -> None:
        super().__init__()
        self._exact = dict(exact)
        self._aliases = dict(aliases)

    @override
    def __getitem__(self, key: EngineVersion) -> V:
        found = self._exact.get(key)
        if found is not None:
            return found
        successor = self._aliases.get(key.ref)
        if successor is None:
            raise KeyError(key)
        return self._exact[successor]

    @override
    def __iter__(self) -> Iterator[EngineVersion]:
        return iter(self._exact)

    @override
    def __len__(self) -> int:
        return len(self._exact)


def release_images(releases: Iterable[EngineRelease] | None = None) -> frozenset[str]:
    """Images the sandbox broker must allow for the pinned engines: the run image of each, not every variant."""
    return frozenset(run_image(r.image) for r in (release_allowlist() if releases is None else releases))


def release_image_resources(releases: Iterable[EngineRelease] | None = None) -> dict[str, ImageResources]:
    """The run image of each engine whose release sets its sandbox's CPUs or least memory, and those settings."""
    return {
        run_image(r.image): ImageResources(cpus=r.cpus, min_memory_mb=r.min_memory_mb)
        for r in (release_allowlist() if releases is None else releases)
        if r.cpus is not None or r.min_memory_mb is not None
    }


# Where each native engine's image holds the binary its adapter hashes and launches (EH1 FB-1); an ACP agent's
# profile names its own (``acp_profiles``).
BINARY_PATHS: Mapping[EngineKind, str] = {
    "opencode": opencode_launch.BINARY_PATH,
    "codex": codex_launch.BINARY_PATH,
    "claude_code": claude_code_launch.BINARY_PATH,
}


def acp_profiles(env: Mapping[str, str] | None = None) -> tuple[AcpAgentProfile, ...]:
    """The ACP agent profiles this install runs: those signed by the release key or a test stack's own test root, or
    on a dev install that asks for it, the unsigned packaged ones (``harness_engines.profile_trust``). Raises
    ``ProfileTestRootRefused`` on a test root this process must not use, so the process does not start."""
    values = os.environ if env is None else env
    root = profile_test_root(values, release_build=release_signed(), release_keys=frozenset(RELEASE_KEYS))
    return packaged_acp_agents(unsigned_ok=unsigned_profiles_allowed(values), test_root=root)


def installed_acp(
    releases: Iterable[EngineRelease] | None = None, profiles: Iterable[AcpAgentProfile] | None = None
) -> dict[EngineVersion, AcpAgentProfile]:
    """The allow-listed ACP engines with a profile that pins the same version, binary and image."""
    chosen = acp_profiles() if profiles is None else tuple(profiles)
    by_engine = {p.engine: p for p in chosen}
    installed: dict[EngineVersion, AcpAgentProfile] = {}
    for release in release_allowlist() if releases is None else releases:
        profile = by_engine.get(release.version)
        if profile is not None and profile.image == release.image:
            installed[release.version] = profile
    return installed


def uninstalled_engines(
    releases: Iterable[EngineRelease] | None = None, profiles: Iterable[AcpAgentProfile] | None = None
) -> tuple[str, ...]:
    """The allow-listed ACP engines (``kind@version``) without an installed profile: never runnable here."""
    chosen = tuple(release_allowlist() if releases is None else releases)
    installed = installed_acp(chosen, profiles)
    return tuple(sorted(r.version.ref for r in chosen if is_acp_kind(r.version.kind) and r.version not in installed))


def untrusted_engines(
    releases: Iterable[EngineRelease] | None = None, profiles: Iterable[AcpAgentProfile] | None = None
) -> tuple[str, ...]:
    """The allow-listed ACP engines whose packaged profile this install does not trust (``kind@version``): the
    profile is there, signed by no key it trusts. They are uninstalled too; this names why, so an admin reads "not
    signed for this deployment" and not "not installed"."""
    chosen = tuple(release_allowlist() if releases is None else releases)
    installed = installed_acp(chosen, profiles)
    packaged = installed_acp(chosen, packaged_unverified_acp_agents())
    return tuple(sorted(version.ref for version in packaged if version not in installed))


def binary_path(version: EngineVersion, profiles: Mapping[EngineVersion, AcpAgentProfile] | None = None) -> str | None:
    """Where the engine's image holds its binary; ``None`` for an ACP engine without an installed profile."""
    if not is_acp_kind(version.kind):
        return BINARY_PATHS[version.kind]
    profile = (installed_acp() if profiles is None else profiles).get(version)
    return profile.binary if profile is not None else None


def release_binary_pins(releases: Iterable[EngineRelease] | None = None) -> dict[str, tuple[PinnedBinary, ...]]:
    """What the sandbox broker checks in each image of the pinned releases (the run image and every variant)
    before it vouches for the binary (EH2)."""
    pins: dict[str, tuple[PinnedBinary, ...]] = {}
    chosen = tuple(release_allowlist() if releases is None else releases)
    acp = installed_acp(chosen)
    for release in chosen:
        path = binary_path(release.version, acp)
        if path is None:
            continue
        pinned = PinnedBinary(release.version, path)
        for image in sorted({run_image(release.image), *(run_image(v) for v in release.variants.values())}):
            pins[image] = (*pins.get(image, ()), pinned)
    return pins


def release_registry(
    adapters: Iterable[EngineAdapter] | None = None,
    releases: Iterable[EngineRelease] | None = None,
    *,
    log: HarnessLog | None = None,
) -> AllowlistEngineRegistry:
    chosen = tuple(release_allowlist() if releases is None else releases)
    return AllowlistEngineRegistry(
        release_adapters(chosen, log=log) if adapters is None else adapters,
        (r.version for r in chosen),
        successors={old: release.version for old, release in release_successors(chosen).items()},
        log=log,
    )


def release_adapters(
    releases: Iterable[EngineRelease] | None = None, *, log: HarnessLog | None = None
) -> tuple[EngineAdapter, ...]:
    """The native adapters, and one ACP adapter per installed ACP profile."""
    acp = installed_acp(releases)
    return (
        OpencodeAdapter(),
        CodexAdapter(),
        ClaudeCodeAdapter(),
        *(acp_adapter(profile, log=log) for _, profile in sorted(acp.items(), key=lambda item: item[0].ref)),
    )


def release_model_apis(adapters: Iterable[EngineAdapter] | None = None) -> dict[str, frozenset[ModelApi]]:
    """The model APIs each released engine speaks, from its adapter (what the model gateway lets it call)."""
    released = {r.version.kind for r in release_allowlist()}
    chosen = release_adapters() if adapters is None else adapters
    return {a.kind: a.capabilities().model_apis for a in chosen if a.kind in released}


def release_default_profile(version: EngineVersion, access: AccessRouteRef, *, raw: str | None = None) -> EngineProfile:
    engine: JsonValue = {"kind": version.kind, "version": version.version, "binaryDigest": version.binary_digest}
    return profile_from_wire({**release_default_wire(raw), "engine": engine}, access=access)


def release_profiles(
    access: AccessRouteRef, releases: Iterable[EngineRelease] | None = None, *, raw: str | None = None
) -> tuple[EngineProfile, ...]:
    """The release default for every pinned engine, in allowlist order: the first is the default engine."""
    chosen = tuple(release_allowlist(raw) if releases is None else releases)
    return tuple(release_default_profile(r.version, access, raw=raw) for r in chosen)


# Both release images run with ``HOME=/tmp``; the tools image keeps matplotlib's font cache there too.
# opencode's ``XDG_STATE_HOME`` is left out: it holds process locks a restore would bring back stale.
RELEASE_SNAPSHOT_PATHS = ("/tmp/.cache", "/tmp/.config", "/tmp/.local", "/tmp/matplotlib", CACHE_HOME)


def release_snapshot_policy(max_bytes: int = DEFAULT_MAX_BYTES) -> SnapshotPolicy:
    """What a sandbox snapshot of a release engine holds (P5.9b): its ``HOME`` caches, never the workspace or
    ``/state`` (the engine checkpoint's)."""
    return SnapshotPolicy(RELEASE_SNAPSHOT_PATHS, max_bytes=max_bytes)


__all__ = [
    "BINARY_PATHS",
    "ByEngineRef",
    "acp_profiles",
    "binary_path",
    "installed_acp",
    "release_adapters",
    "uninstalled_engines",
    "untrusted_engines",
    "CANDIDATE_LABEL",
    "CANDIDATE_TAG_SUFFIX",
    "RELEASE_SNAPSHOT_PATHS",
    "CandidateImagePinned",
    "DuplicateEngineVersion",
    "InvalidEngineSuccessor",
    "ENGINE_IMAGE_REGISTRY_ENV",
    "EngineImageRegistryInvalid",
    "EngineRelease",
    "Supply",
    "candidate_image",
    "default_versions",
    "engine_image_registry",
    "engines_without_gvisor",
    "release_allowlist",
    "release_binary_pins",
    "release_default_profile",
    "release_default_wire",
    "release_image_resources",
    "release_images",
    "release_model_apis",
    "release_profiles",
    "release_registry",
    "release_snapshot_policy",
    "release_successors",
    "run_image",
]
