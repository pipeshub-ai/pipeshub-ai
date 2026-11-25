"""ADR-SEC-01 D2, D3, D5: the trust roots and the image trust are extension points (``TrustRootsProvider``,
``ImageTrustProvider``) read through ``harness_edition()``; this build's defaults trust the release's keys and check
only the release's engine images.

SCP-1..SCP-6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override
from unittest.mock import patch

import pytest

from app.harness.supply_chain.images import (
    ATTESTATIONS_ENV,
    RELEASE_IMAGE_TRUST,
    SIGNATURES_ENV,
    ImageTrustMisconfigured,
    ReleaseImageTrust,
    image_trust_from_env,
)
from app.harness.supply_chain.roots import (
    RELEASE_TRUST_ROOTS,
    ReleaseTrustRoots,
    trust_roots,
)
from harness_core.edition import HarnessEdition
from harness_core.supply_chain.images import (
    DirectoryAttestations,
    ImageTrust,
    StaticAttestations,
)
from harness_core.supply_chain.ports import ImageTrustProvider, TrustRootsProvider
from harness_core.supply_chain.trust import ArtefactKind, TrustRoots
from harness_engines.trust_roots import release_trust_roots

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

ENGINE = "pipeshub/engine-opencode:1.2.3-ph5"
EXTRA_KEY = {ArtefactKind.SKILL: {"publisher": b"\x01" * 32}}


class _AddedRoots(TrustRootsProvider):
    @override
    def roots(self, env: Mapping[str, str]) -> TrustRoots:
        return release_trust_roots().merged(TrustRoots(EXTRA_KEY))


class _AlwaysTrust(ImageTrustProvider):
    def __init__(self) -> None:
        self.asked: list[tuple[frozenset[str], frozenset[str], dict[str, str]]] = []

    @override
    def build(
        self,
        release_images: frozenset[str],
        base_images: frozenset[str],
        env: Mapping[str, str],
    ) -> ImageTrust | None:
        self.asked.append((release_images, base_images, dict(env)))
        return ImageTrust(
            roots=TrustRoots(),
            attestations=StaticAttestations(),
            release_images=release_images,
        )


@pytest.fixture
def added_roots() -> Iterator[None]:
    edition = HarnessEdition({TrustRootsProvider: _AddedRoots()})
    with patch("app.edition_services.harness_edition", return_value=edition):
        yield


def test_scp1_this_builds_trust_roots_are_the_releases_alone() -> None:
    assert isinstance(RELEASE_TRUST_ROOTS, ReleaseTrustRoots)
    roots = trust_roots({"HARNESS_UNREAD_SETTING": "/etc/anything.json"})
    release = release_trust_roots()
    assert {k: dict(roots.for_kind(k)) for k in ArtefactKind.declared()} == {
        k: dict(release.for_kind(k)) for k in ArtefactKind.declared()
    }
    assert roots.for_kind(ArtefactKind.SKILL) == {}
    assert roots.for_kind(ArtefactKind.MCP_SERVER) == {}


@pytest.mark.usefixtures("added_roots")
def test_scp2_an_editions_trust_roots_replace_the_default() -> None:
    assert (
        dict(trust_roots({}).for_kind(ArtefactKind.SKILL))
        == EXTRA_KEY[ArtefactKind.SKILL]
    )


def test_scp3_this_builds_image_trust_checks_nothing_until_engine_signatures_are_required() -> (
    None
):
    assert isinstance(RELEASE_IMAGE_TRUST, ReleaseImageTrust)
    for env in ({}, {SIGNATURES_ENV: "off"}, {SIGNATURES_ENV: " "}):
        assert (
            image_trust_from_env(frozenset({ENGINE}), frozenset({ENGINE}), env) is None
        )
    # A setting only an edition reads changes nothing here.
    assert (
        image_trust_from_env(
            frozenset({ENGINE}), frozenset(), {"HARNESS_UNREAD_SETTING": "/x.json"}
        )
        is None
    )
    with pytest.raises(ImageTrustMisconfigured):
        image_trust_from_env(frozenset(), frozenset(), {SIGNATURES_ENV: "maybe"})


def test_scp4_required_engine_signatures_check_the_release_images_against_the_release_roots(
    tmp_path: Path,
) -> None:
    trust = image_trust_from_env(
        frozenset({ENGINE}),
        frozenset({ENGINE}),
        {SIGNATURES_ENV: "required", ATTESTATIONS_ENV: str(tmp_path)},
    )
    assert trust is not None
    assert trust.kind_of(ENGINE) is ArtefactKind.ENGINE_IMAGE
    assert trust.kind_of("corp.example/tools:7") is None
    assert trust.added_images == frozenset()
    assert not trust.needs_bases(ArtefactKind.ENGINE_IMAGE)
    assert trust.attestations == DirectoryAttestations(tmp_path)
    assert dict(trust.roots.for_kind(ArtefactKind.ENGINE_IMAGE)) == dict(
        release_trust_roots().for_kind(ArtefactKind.ENGINE_IMAGE)
    )
    bare = image_trust_from_env(
        frozenset({ENGINE}), frozenset(), {SIGNATURES_ENV: "REQUIRED"}
    )
    assert bare is not None and isinstance(bare.attestations, StaticAttestations)


def test_scp5_an_editions_image_trust_is_asked_with_the_brokers_images_and_settings() -> (
    None
):
    supplied = _AlwaysTrust()
    with patch(
        "app.edition_services.harness_edition",
        return_value=HarnessEdition({ImageTrustProvider: supplied}),
    ):
        trust = image_trust_from_env(
            frozenset({ENGINE}), frozenset({"base"}), {"K": "v"}
        )
    assert trust is not None and trust.release_images == frozenset({ENGINE})
    assert supplied.asked == [(frozenset({ENGINE}), frozenset({"base"}), {"K": "v"})]


def test_scp6_a_port_that_does_not_implement_its_kind_is_refused() -> None:
    with pytest.raises(TypeError):
        HarnessEdition({TrustRootsProvider: _AlwaysTrust()})
    with pytest.raises(TypeError):
        HarnessEdition({ImageTrustProvider: _AddedRoots()})
