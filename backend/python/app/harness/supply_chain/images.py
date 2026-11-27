"""The sandbox broker's image trust, from the deployment's settings (ADR-SEC-01 D3, D6).

The check comes from the edition's ``ImageTrustProvider``; this build's default reads two settings:

- ``HARNESS_IMAGE_SIGNATURES=required``: the release's engine images need the release key's attestation. Off until
  the first release signed with the new key (OB-1); then the release turns it on.
- ``HARNESS_IMAGE_ATTESTATIONS_DIR``: where attestation files are.

A setting that is set and cannot be used stops the broker at start, with the reason: an operator who configured it
meant it, and starting without it would admit nothing or the wrong thing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final, override

from app.harness.supply_chain.roots import trust_roots
from harness_core.supply_chain.images import (
    AttestationSource,
    DirectoryAttestations,
    ImageTrust,
    StaticAttestations,
)
from harness_core.supply_chain.ports import ImageTrustProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

SIGNATURES_ENV: Final = "HARNESS_IMAGE_SIGNATURES"
ATTESTATIONS_ENV: Final = "HARNESS_IMAGE_ATTESTATIONS_DIR"


class ImageTrustMisconfigured(ValueError):
    reason_code = "image_trust_misconfigured"


def setting_file(env: Mapping[str, str], name: str) -> bytes | None:
    """The bytes of the file setting ``name`` names; None when it is unset. A file it names that cannot be read is
    ``ImageTrustMisconfigured``."""
    path = env.get(name, "").strip()
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ImageTrustMisconfigured(
            f"{name} names {path}, which cannot be read: {exc}"
        ) from None


def engine_signatures_required(env: Mapping[str, str]) -> bool:
    mode = env.get(SIGNATURES_ENV, "").strip().lower()
    if mode not in ("", "off", "required"):
        raise ImageTrustMisconfigured(
            f"{SIGNATURES_ENV} is 'required' or 'off', not {mode!r}"
        )
    return mode == "required"


def attestation_source(env: Mapping[str, str]) -> AttestationSource:
    directory = env.get(ATTESTATIONS_ENV, "").strip()
    return DirectoryAttestations(Path(directory)) if directory else StaticAttestations()


class ReleaseImageTrust(ImageTrustProvider):
    """The release's engine images on the release roots, once engine signatures are required; no check before."""

    @override
    def build(
        self,
        release_images: frozenset[str],
        base_images: frozenset[str],
        env: Mapping[str, str],
    ) -> ImageTrust | None:
        if not engine_signatures_required(env):
            return None
        return ImageTrust(
            roots=trust_roots(env),
            attestations=attestation_source(env),
            release_images=release_images,
            engine_signatures_required=True,
            base_images=base_images,
        )


RELEASE_IMAGE_TRUST: Final = ReleaseImageTrust()


def image_trust_from_env(
    release_images: frozenset[str],
    base_images: frozenset[str],
    env: Mapping[str, str] | None = None,
) -> ImageTrust | None:
    """The broker's image check, or None when nothing asks for one (this build's default until OB-1)."""
    from app.edition_services import harness_edition

    provider = harness_edition().port(ImageTrustProvider, RELEASE_IMAGE_TRUST)
    return provider.build(
        release_images, base_images, os.environ if env is None else env
    )


__all__ = [
    "ATTESTATIONS_ENV",
    "RELEASE_IMAGE_TRUST",
    "SIGNATURES_ENV",
    "ImageTrustMisconfigured",
    "ReleaseImageTrust",
    "attestation_source",
    "engine_signatures_required",
    "image_trust_from_env",
    "setting_file",
]
