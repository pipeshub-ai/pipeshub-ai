"""Image trust at acquire (ADR-SEC-01 D3): an image the check applies to is started only when a signed attestation
vouches for one of the image's own digests, by a key trusted for its kind.

The broker is the only component that sees an image's bytes, so images have no approval route: everything here is
checked against what the broker reads from the image itself (its digests and its layers), never a label or another
process's word. This build checks the release's engine images; ``ImageTrust`` is open to a subclass (supplied through
``ImageTrustProvider``) that checks more images, and more of each (``admit``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, cast

from harness_core.supply_chain.diff import fields_diff
from harness_core.supply_chain.trust import (
    SIGNATURE_INVALID,
    ArtefactKind,
    ArtefactUntrusted,
    Attestation,
    TrustRoots,
    parse_attestation,
    verify_attestation,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from harness_core.engines.types import JsonValue
    from harness_core.supply_chain.diff import FieldDiff

IMAGE_UNSIGNED: Final = "image_unsigned"


class AttestationSource(Protocol):
    def for_digests(self, digests: Sequence[str]) -> list[Attestation]: ...


@dataclass(frozen=True, slots=True)
class StaticAttestations:
    attestations: Sequence[Attestation] = ()

    def for_digests(self, digests: Sequence[str]) -> list[Attestation]:
        wanted = set(digests)
        return [a for a in self.attestations if a.digest in wanted]


@dataclass(frozen=True, slots=True)
class DirectoryAttestations:
    """Attestation files (``*.json``) in a directory the deployment mounts: the release's for its engine images, and any
    other signer's the check accepts. Read on each check, so a file dropped in takes effect without a restart;
    a file that is not an attestation is skipped (it vouches for nothing)."""

    directory: Path

    def for_digests(self, digests: Sequence[str]) -> list[Attestation]:
        wanted = set(digests)
        found: list[Attestation] = []
        if not self.directory.is_dir():
            return found
        for path in sorted(self.directory.glob("*.json")):
            try:
                attestation = parse_attestation(path.read_bytes())
            except (OSError, ArtefactUntrusted):
                continue
            if attestation.digest in wanted:
                found.append(attestation)
        return found


@dataclass(frozen=True, slots=True)
class ImageFacts:
    """What the broker read from the image a run asked for."""

    image: str
    digests: Sequence[str]
    layers: Sequence[str]


@dataclass(frozen=True, slots=True)
class VerifiedImage:
    key_id: str
    attestation: Attestation


@dataclass(frozen=True, slots=True)
class ImageTrust:
    """The broker's image check. ``release_images`` are verified only when ``engine_signatures_required`` (until
    the first release signed with the new key, OB-1).

    A subclass checks more: ``kind_of`` names the further images it applies to, ``added_images`` admits them past
    the broker's release allow list, ``needs_bases`` asks the broker for the base images' layers, and ``admit``
    checks a verified image further, refusing with ``ArtefactUntrusted``."""

    roots: TrustRoots
    attestations: AttestationSource
    release_images: frozenset[str] = frozenset()
    engine_signatures_required: bool = False
    # The PipesHub images whose layers a check may compare an image against (the release's engine images and the
    # sandbox image).
    base_images: frozenset[str] = frozenset()

    def kind_of(self, image: str) -> ArtefactKind | None:
        """What ``image`` is to the check, or None when the check does not apply to it."""
        if image in self.release_images and self.engine_signatures_required:
            return ArtefactKind.ENGINE_IMAGE
        return None

    @property
    def added_images(self) -> frozenset[str]:
        """Images this check admits beyond the broker's release allow list: none here."""
        return frozenset()

    def needs_bases(self, kind: ArtefactKind) -> bool:
        """Whether ``verify`` needs the base images' layers for an image of ``kind``: never here."""
        del kind
        return False

    def admit(
        self,
        kind: ArtefactKind,
        attestation: Attestation,
        facts: ImageFacts,
        bases: Mapping[str, Sequence[str]],
    ) -> None:
        """A further check of an image whose attestation verified: nothing more here."""
        del kind, attestation, facts, bases

    def verify(
        self, facts: ImageFacts, bases: Mapping[str, Sequence[str]] | None = None
    ) -> VerifiedImage | None:
        """The attestation that vouches for the image and its key, or ``ArtefactUntrusted``; None when the check
        does not apply.
        ``bases``: each PipesHub base image's layers, when ``needs_bases`` asked for them."""
        kind = self.kind_of(facts.image)
        if kind is None:
            return None
        candidates = [
            a for a in self.attestations.for_digests(facts.digests) if a.kind is kind
        ]
        if not candidates:
            raise ArtefactUntrusted(
                IMAGE_UNSIGNED,
                f"no attestation of kind {kind.value} names {facts.image}",
                kind=kind,
                subject=facts.image,
            )
        refusal: ArtefactUntrusted | None = None
        for attestation in candidates:
            try:
                key_id = verify_attestation(
                    attestation, self.roots, digests=facts.digests
                )
            except ArtefactUntrusted as refused:
                refusal = refused
                continue
            self.admit(kind, attestation, facts, bases or {})
            return VerifiedImage(key_id, attestation)
        raise refusal or ArtefactUntrusted(
            SIGNATURE_INVALID, "no attestation verifies", kind=kind, subject=facts.image
        )


def registration_diff(
    previous: Attestation | None, current: Attestation
) -> list[FieldDiff]:
    """What moved between two attestations of one image (D4): its digest and every claim, for the operator's log."""

    def view(a: Attestation | None) -> dict[str, JsonValue] | None:
        return None if a is None else {"digest": a.digest, **dict(a.claims)}

    return fields_diff(view(previous), view(current) or {})


def layers_of(inspected: Mapping[str, object]) -> tuple[str, ...]:
    """An image's layer digests from ``docker image inspect`` (``RootFS.Layers``)."""
    rootfs = inspected.get("RootFS")
    layers = (
        cast("dict[str, object]", rootfs).get("Layers")
        if isinstance(rootfs, dict)
        else None
    )
    return (
        tuple(str(layer) for layer in cast("Iterable[object]", layers))
        if isinstance(layers, list)
        else ()
    )


__all__ = [
    "IMAGE_UNSIGNED",
    "AttestationSource",
    "DirectoryAttestations",
    "ImageFacts",
    "ImageTrust",
    "StaticAttestations",
    "VerifiedImage",
    "layers_of",
    "registration_diff",
]
