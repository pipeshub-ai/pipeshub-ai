"""The engine releases the Docker lanes run: the allowlist's, or one candidate under test (CC1).

``PXH_ENGINE_CANDIDATE=<kind>=<version>@sha256:<hex>`` swaps that kind's release for the image
``engine-build.sh --candidate`` built, so the conformance, controller and matrix lanes gate a version before it
is admitted. Only tests read the variable: production loads the packaged allowlist, which refuses candidate tags.
"""

from __future__ import annotations

import dataclasses
import os
import re
from importlib import resources
from typing import TYPE_CHECKING

from harness_core.engines.types import EngineVersion
from harness_engines.acp.profiles import parse_acp_agents
from harness_engines.acp.registry import AGENTS_RESOURCE
from harness_engines.release import EngineRelease, candidate_image, release_allowlist

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_engines.acp.profiles import AcpAgentProfile

CANDIDATE_ENV = "PXH_ENGINE_CANDIDATE"
_SHIPPED_ACP = resources.files("harness_engines").joinpath(AGENTS_RESOURCE)
# A kind is a native engine's (``claude_code``) or an ACP profile's (``acp-gemini``).
_SPEC = re.compile(r"(?P<kind>[a-z_]+(?:-[a-z_]+)?)=(?P<version>[A-Za-z0-9_][A-Za-z0-9_.-]{0,100})@(?P<digest>sha256:[0-9a-f]{64})")


def _candidate(release: EngineRelease, version: str, digest: str) -> EngineRelease:
    return dataclasses.replace(
        release,
        version=EngineVersion(kind=release.version.kind, version=version, binary_digest=digest),
        image=candidate_image(release.image, version),
        variants={name: candidate_image(tag, version) for name, tag in release.variants.items()},
    )


def lane_releases(environ: Mapping[str, str] | None = None) -> tuple[EngineRelease, ...]:
    raw = (os.environ if environ is None else environ).get(CANDIDATE_ENV, "").strip()
    releases = release_allowlist()
    if not raw:
        return releases
    match = _SPEC.fullmatch(raw)
    if match is None:
        raise ValueError(f"{CANDIDATE_ENV}={raw!r} is not <kind>=<version>@sha256:<64 hex>")
    kind = match["kind"]
    if kind not in {r.version.kind for r in releases}:
        raise ValueError(f"{CANDIDATE_ENV}: {kind} is not an allow-listed engine")
    return tuple(
        _candidate(r, match["version"], match["digest"]) if r.version.kind == kind else r for r in releases
    )


def lane_admits_candidates(environ: Mapping[str, str] | None = None) -> bool:
    """Whether a lane's broker runs candidate-labelled images: only while a candidate is under test."""
    return bool((os.environ if environ is None else environ).get(CANDIDATE_ENV, "").strip())


def lane_release(kind: str, environ: Mapping[str, str] | None = None) -> EngineRelease:
    return next(r for r in lane_releases(environ) if r.version.kind == kind)


def lane_acp_profile(agent_id: str, environ: Mapping[str, str] | None = None) -> AcpAgentProfile:
    """The shipped ACP profile ``agent_id`` as the lanes drive it: on its kind's lane release, so a candidate of that
    kind runs the candidate's image and is verified against its version and digest."""
    shipped = next(p for p in parse_acp_agents(_SHIPPED_ACP.read_bytes()) if p.id == agent_id)
    release = lane_release(shipped.kind, environ)
    return dataclasses.replace(
        shipped,
        agent_version=release.version.version,
        binary_digest=release.version.binary_digest,
        image=release.image,
    )


def lane_images(environ: Mapping[str, str] | None = None) -> dict[EngineVersion, str]:
    """Every lane engine on its run image, as ``EngineControllerConfig.images`` takes them."""
    return {r.version: r.image for r in lane_releases(environ)}
