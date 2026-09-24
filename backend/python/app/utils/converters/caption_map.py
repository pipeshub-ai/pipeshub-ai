"""Shared helper for attaching base64 image data to blocks post-construction.

Used by the Docling-backed Markdown/HTML parsers (which pre-convert image URLs
to base64 before Docling ever sees the content, since Docling doesn't resolve
remote URLs itself) to reattach that data once blocks have been built.
"""
from __future__ import annotations

import logging
from typing import Dict

from app.models.blocks import BlockType, BlocksContainer


def apply_caption_map(
    container: BlocksContainer,
    caption_map: Dict[str, str],
    logger: logging.Logger,
) -> None:
    """Attach base-64 URIs to image blocks using normalised caption keys."""
    for block in container.blocks:
        if block.type == BlockType.IMAGE and block.image_metadata:
            captions = block.image_metadata.captions
            if captions:
                caption = captions[0]
                uri = caption_map.get(caption)
                if uri:
                    if block.data is None:
                        block.data = {}
                    if isinstance(block.data, dict):
                        block.data["uri"] = uri
                    else:
                        block.data = {"uri": uri}
                else:
                    logger.warning(
                        "Skipping image with caption '%s' - no valid base64 data available",
                        caption,
                    )
