"""What an engine run's spec pins about the image it runs on (C2.07, C2.20, SL-14).

Node pins them at claim (or at a child's admission) and never re-resolves them, so a re-claimed run (a resume)
holds the same ones: the image digest (core ``pins.sandboxImage``) and the ``/pipeshub`` contract version its image
must declare (``x.pipeshub.release.pipeshubContract``). The engine controller refuses an image that no longer
matches either (``engine_image_changed``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from harness_core.sandbox.types import is_image_digest

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec


@dataclass(frozen=True, slots=True)
class ImagePins:
    image: str | None = None
    contract: int | None = None


def image_pins(spec: RunSpec) -> ImagePins:
    image = spec.pins.sandbox_image if spec.pins is not None else None
    extension = cast("dict[str, JsonValue]", (spec.extensions or {}).get("x.pipeshub") or {})
    release = extension.get("release")
    contract = release.get("pipeshubContract") if isinstance(release, dict) else None
    return ImagePins(
        image=image if image is not None and is_image_digest(image) else None,
        contract=contract if isinstance(contract, int) and not isinstance(contract, bool) and contract > 0 else None,
    )


__all__ = ["ImagePins", "image_pins"]
