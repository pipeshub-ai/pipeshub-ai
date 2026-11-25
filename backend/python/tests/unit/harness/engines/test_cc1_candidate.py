"""CR-1..CR-5, CV-1..CV-3 (CC1): a candidate engine build runs in the lanes that gate it and nowhere else.

The candidate here is synthetic (``9.9.9``), so these cases hold before and after any real version is admitted.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from typing import Any
from unittest.mock import patch

import pytest

from app.harness.engines.worker import controller_config
from app.harness.sandbox.docker_policy import HardeningPolicy
from app.sandbox_broker_main import allowed_images
from harness_core.engines.errors import EngineVersionNotAllowed
from harness_core.engines.types import EngineVersion
from harness_engines.release import (
    CANDIDATE_TAG_SUFFIX,
    CandidateImagePinned,
    _packaged,  # pyright: ignore[reportPrivateUsage]
    candidate_image,
    release_allowlist,
    release_images,
    release_registry,
)
from tests.unit.harness.engines.lane_releases import (
    CANDIDATE_ENV,
    lane_acp_profile,
    lane_release,
    lane_releases,
)
from tests.unit.harness_core.engines.test_controller import World, task

VERSION = "9.9.9"
DIGEST = "sha256:" + "e" * 64
SPEC = f"claude_code={VERSION}@{DIGEST}"


def _released(kind: str) -> EngineVersion:
    return next(r.version for r in release_allowlist() if r.version.kind == kind)


def _candidate(kind: str = "claude_code") -> EngineVersion:
    return EngineVersion(kind=_released(kind).kind, version=VERSION, binary_digest=DIGEST)


def _allowlist_with(kind: str, **changes: Any) -> str:  # noqa: ANN401
    document = json.loads(_packaged())
    for entry in document["engines"]:
        if entry["kind"] == kind:
            entry.update(changes)
    return json.dumps(document)


# CR-1
@pytest.mark.parametrize(
    ("kind", "changes"),
    [
        ("claude_code", {"image": f"pxh-claude-code:{VERSION}{CANDIDATE_TAG_SUFFIX}", "variants": None}),
        ("codex", {"image": f"registry.local:5000/pxh-codex:{VERSION}{CANDIDATE_TAG_SUFFIX}", "variants": None}),
        (
            "opencode",
            {
                "image": "pxh-opencode-tools:1.18.34",
                "variants": {"engine": f"pxh-opencode:{VERSION}{CANDIDATE_TAG_SUFFIX}", "tools": "pxh-opencode-tools:1.18.34"},
            },
        ),
    ],
)
def test_cr1_an_allowlist_naming_a_candidate_image_is_refused(kind: str, changes: dict[str, object]) -> None:
    with pytest.raises(CandidateImagePinned, match="candidate image") as refused:
        release_allowlist(_allowlist_with(kind, **changes))
    assert refused.value.reason_code == "candidate_image_pinned"


def test_cr1_the_packaged_allowlist_and_a_repository_named_candidate_load() -> None:
    assert not any(image.endswith(CANDIDATE_TAG_SUFFIX) for image in release_images())
    # Only the tag counts: a repository whose name ends in the suffix is not a candidate build.
    raw = _allowlist_with("codex", image="registry.local:5000/pxh-codex-candidate", variants=None)
    assert "registry.local:5000/pxh-codex-candidate" in release_images(release_allowlist(raw))


# CR-2
@pytest.mark.parametrize("environ", [{}, {CANDIDATE_ENV: SPEC}])
def test_cr2_the_release_registry_refuses_a_candidate(environ: dict[str, str]) -> None:
    with patch.dict(os.environ, environ):
        registry = release_registry()
        with pytest.raises(EngineVersionNotAllowed) as unknown:
            registry.adapter_for(_candidate())
        released = _released("claude_code")
        with pytest.raises(EngineVersionNotAllowed) as other_digest:
            registry.adapter_for(dataclasses.replace(released, binary_digest=DIGEST))
    assert unknown.value.reason_code == "version_not_allowed"
    assert other_digest.value.reason_code == "digest_mismatch"
    assert _candidate() not in registry.allowed()


# CR-3
@pytest.mark.parametrize("with_image", [False, True])
async def test_cr3_the_controller_refuses_a_run_pinned_to_a_candidate_before_a_sandbox(
    with_image: bool, caplog: pytest.LogCaptureFixture
) -> None:
    with patch.dict(os.environ, {CANDIDATE_ENV: SPEC}):
        images = dict(controller_config(release_allowlist()).images)
    if with_image:
        images[_candidate()] = candidate_image(lane_release("claude_code").image, VERSION)
    world = World(registry=release_registry(), images=images)
    run = world.run()
    pinned = dataclasses.replace(run, profile=dataclasses.replace(run.profile, engine=_candidate()))
    # Only the refused turn's lines: the release registry warns once per process (unsigned ACP profiles) when it is
    # first built, which under xdist can be in this test's setup.
    caplog.clear()
    with caplog.at_level(logging.WARNING), pytest.raises(EngineVersionNotAllowed):
        await world.controller.run_turn(pinned, task(0, "say ok"))
    assert world.broker.acquired == [] and world.host.mounted == 0
    assert [r.levelno for r in caplog.records if r.levelno >= logging.WARNING] == [logging.WARNING]


# CR-4
def test_cr4_the_broker_allows_no_candidate_image() -> None:
    with patch.dict(os.environ, {"SANDBOX_DOCKER_IMAGE": "pipeshub/sandbox:x", CANDIDATE_ENV: SPEC}):
        images = allowed_images()
    assert not any(image.endswith(CANDIDATE_TAG_SUFFIX) for image in images)
    policy = HardeningPolicy(user="1000:1000", allowed_networks=frozenset(), volume_allowed=lambda _: False, allowed_images=images)
    for release in release_allowlist():
        assert policy.image_allowed(release.image)
        assert not policy.image_allowed(candidate_image(release.image, VERSION))
        assert not policy.image_allowed(candidate_image(release.image, release.version.version))


# CR-5
@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("pxh-claude-code:2.1.289", "pxh-claude-code:2.1.294-candidate"),
        ("pxh-opencode-tools:1.18.34", "pxh-opencode-tools:2.1.294-candidate"),
        ("registry.local:5000/pxh-codex:0.160.0", "registry.local:5000/pxh-codex:2.1.294-candidate"),
        ("registry.local:5000/pxh-codex", "registry.local:5000/pxh-codex:2.1.294-candidate"),
        ("pxh-codex", "pxh-codex:2.1.294-candidate"),
    ],
)
def test_cr5_a_candidate_is_named_from_the_repository_of_the_allowlisted_image(image: str, expected: str) -> None:
    assert candidate_image(image, "2.1.294") == expected


# CV-1
def test_cv1_without_a_candidate_the_lanes_run_the_allowlist() -> None:
    assert lane_releases({}) == release_allowlist()
    assert lane_releases({CANDIDATE_ENV: "  "}) == release_allowlist()


# CV-2
@pytest.mark.parametrize("kind", ["claude_code", "codex", "opencode", "acp-gemini"])
def test_cv2_a_candidate_replaces_only_its_own_kind(kind: str) -> None:
    released = {r.version.kind: r for r in release_allowlist()}
    lanes = {r.version.kind: r for r in lane_releases({CANDIDATE_ENV: f"{kind}={VERSION}@{DIGEST}"})}
    candidate = lanes[kind]
    assert candidate.version == EngineVersion(kind=released[kind].version.kind, version=VERSION, binary_digest=DIGEST)
    assert candidate.image == candidate_image(released[kind].image, VERSION)
    assert dict(candidate.variants) == {n: candidate_image(t, VERSION) for n, t in released[kind].variants.items()}
    assert candidate.supply == released[kind].supply
    assert {k: r for k, r in lanes.items() if k != kind} == {k: r for k, r in released.items() if k != kind}


# CV-4 (WP-59): an ACP engine's lanes drive its shipped profile, so a candidate of its kind is that profile on the
# candidate's version, digest and image (the driver verifies the binary and the version against the profile).
def test_cv4_an_acp_candidate_is_its_shipped_profile_on_the_candidate() -> None:
    shipped = lane_acp_profile("gemini", {})
    assert (shipped.engine, shipped.image) == (lane_release("acp-gemini", {}).version, lane_release("acp-gemini", {}).image)
    environ = {CANDIDATE_ENV: f"acp-gemini={VERSION}@{DIGEST}"}
    candidate = lane_acp_profile("gemini", environ)
    assert candidate.engine == EngineVersion(kind="acp-gemini", version=VERSION, binary_digest=DIGEST)
    assert candidate.image == lane_release("acp-gemini", environ).image == candidate_image(shipped.image, VERSION)
    assert dataclasses.replace(candidate, agent_version=shipped.agent_version, binary_digest=shipped.binary_digest, image=shipped.image) == shipped
    # Another kind's candidate leaves the profile as shipped.
    assert lane_acp_profile("gemini", {CANDIDATE_ENV: f"opencode={VERSION}@{DIGEST}"}) == shipped


# CV-3
@pytest.mark.parametrize(
    "raw",
    [
        f"claude_code={VERSION}",
        f"claude_code@{DIGEST}",
        f"claude_code={VERSION}@sha256:{'E' * 64}",
        f"claude_code={VERSION}@{DIGEST[:-1]}",
        f"jcode={VERSION}@{DIGEST}",
        f"{SPEC},codex={VERSION}@{DIGEST}",
    ],
)
def test_cv3_a_malformed_candidate_stops_the_lane(raw: str) -> None:
    with pytest.raises(ValueError, match=CANDIDATE_ENV):
        lane_releases({CANDIDATE_ENV: raw})
