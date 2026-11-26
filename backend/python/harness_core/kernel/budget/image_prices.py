"""Image-generation prices, as data (USD per generated image), WP-55.

An image model is charged per image, not per token. OpenAI: the per-image table of
https://developers.openai.com/api/docs/guides/image-generation (cost and latency), read
2026-11-01. A ``gpt-image-*`` call is priced at the high-quality row because PipesHub's
adapter sends no ``quality`` and the provider's ``auto`` may pick high; ``dall-e-3`` at its
default (standard) quality, from the earlier DALL-E price list. Google: the per-image rows
of https://ai.google.dev/gemini-api/docs/pricing (standard tier, 1K images), read
2026-11-01. An image model not listed here is charged ``UNLISTED_IMAGE_USD``, the highest
listed price, so an unknown model never costs a run less than it may.
"""

from __future__ import annotations

from dataclasses import dataclass

IMAGE_PRICES_READ_ON = "2026-11-01"


@dataclass(frozen=True, slots=True)
class ImagePrice:
    square: float
    # Portrait and landscape (1024x1536 / 1536x1024, the tool's 1024x1792 / 1792x1024).
    oblong: float


IMAGE_PRICES: dict[str, ImagePrice] = {
    "gpt-image-2": ImagePrice(0.211, 0.165),
    "gpt-image-1.5": ImagePrice(0.133, 0.20),
    "gpt-image-1": ImagePrice(0.167, 0.25),
    "gpt-image-1-mini": ImagePrice(0.036, 0.052),
    "dall-e-3": ImagePrice(0.04, 0.08),
    "dall-e-2": ImagePrice(0.02, 0.02),
    "gemini-3-pro-image": ImagePrice(0.134, 0.134),
    "gemini-3.1-flash-image": ImagePrice(0.067, 0.067),
    "gemini-3.1-flash-lite-image": ImagePrice(0.0336, 0.0336),
    "gemini-nano-banana-2.1": ImagePrice(0.0336, 0.0336),
    "gemini-2.5-flash-image": ImagePrice(0.039, 0.039),
}

UNLISTED_IMAGE_USD = max(max(p.square, p.oblong) for p in IMAGE_PRICES.values())

_SQUARE = frozenset({"1024x1024", "512x512", "256x256", "1:1"})


def image_price_usd(model: str | None, size: str) -> tuple[float, bool]:
    """USD for one image of ``size`` from ``model``, and whether the model is listed. A provider-prefixed name
    (``openai/gpt-image-1``) is priced by its last part."""
    name = (model or "").strip().lower().rsplit("/", 1)[-1]
    price = IMAGE_PRICES.get(name)
    if price is None:
        return UNLISTED_IMAGE_USD, False
    return (price.square if size in _SQUARE else price.oblong), True


__all__ = ["IMAGE_PRICES", "IMAGE_PRICES_READ_ON", "UNLISTED_IMAGE_USD", "ImagePrice", "image_price_usd"]
