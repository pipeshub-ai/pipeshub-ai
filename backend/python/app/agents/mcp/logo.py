"""Logos people upload for their custom MCP servers.

A logo has its own key, apart from the instance record: org instances are listed by reading every
record under one prefix, and chat reads them on every turn, and neither needs the image.

An upload is decoded and drawn again as a small PNG, so what is stored and served is always a plain
raster image and never the uploaded bytes: no SVG (which can carry script), no metadata, nothing
appended after the image data. Browsers turn an SVG into a PNG before uploading it.
"""
from __future__ import annotations

import base64
import binascii
import io
import logging
from typing import TYPE_CHECKING, Optional

from PIL import Image

from app.agents.constants.mcp_server_constants import get_mcp_logo_path
from app.agents.mcp.models import MAX_LOGO_UPLOAD_BYTES

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

__all__ = [
    "LOGO_SIDE_PX",
    "LogoError",
    "delete_logo",
    "load_logo",
    "normalize_logo",
    "save_logo",
]

# The largest place a logo shows is 28 px; 96 keeps it sharp on 3x screens. A 96 px RGBA PNG is at
# most ~37 KB, whatever was uploaded.
LOGO_SIDE_PX = 96
# Checked before decoding: a small file can declare a huge canvas.
MAX_SOURCE_PIXELS = 4096 * 4096
_FORMATS = ("PNG", "JPEG", "WEBP", "GIF")
_PNG_DATA_URL_PREFIX = "data:image/png;base64,"
# What Pillow raises for a file it can't read as an image: unknown or truncated data, a broken
# chunk, a canvas over its own size limit, a mode it can't convert.
_UNREADABLE = (OSError, ValueError, SyntaxError, EOFError, Image.DecompressionBombError)


class LogoError(ValueError):
    """The upload isn't a logo we keep. The message says why, for the person who sent it."""


def normalize_logo(data_url: str) -> str:
    """The image in `data_url` as a PNG data URL that fits in `LOGO_SIDE_PX` square."""
    raw = _decode_data_url(data_url)
    try:
        with Image.open(io.BytesIO(raw), formats=_FORMATS) as image:
            fits = image.width * image.height <= MAX_SOURCE_PIXELS
            logo = image.convert("RGBA") if fits else None
    except _UNREADABLE as e:
        raise LogoError("The logo must be a PNG, JPEG, WebP or GIF image.") from e
    if logo is None:
        raise LogoError("The logo is too large. Use an image under 4096 × 4096 pixels.")
    logo.thumbnail((LOGO_SIDE_PX, LOGO_SIDE_PX), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    logo.save(out, format="PNG", optimize=True)
    return _PNG_DATA_URL_PREFIX + base64.b64encode(out.getvalue()).decode("ascii")


def _decode_data_url(data_url: str) -> bytes:
    header, comma, payload = data_url.partition(",")
    if not comma or not header.startswith("data:") or not header.endswith(";base64"):
        raise LogoError("Send the logo as a base64 data URL (data:image/png;base64,...).")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as e:
        raise LogoError("The logo isn't valid base64.") from e
    if not raw:
        raise LogoError("The logo is empty.")
    if len(raw) > MAX_LOGO_UPLOAD_BYTES:
        raise LogoError("The logo must be 1 MB or smaller.")
    return raw


async def save_logo(config_service: ConfigurationService, instance_id: str, logo: str) -> bool:
    """Store a logo `normalize_logo` made. Kept out of the config cache: logos are many and large."""
    return await config_service.set_config(get_mcp_logo_path(instance_id), {"dataUrl": logo}, keep_in_cache=False)


async def load_logo(config_service: ConfigurationService, instance_id: str) -> Optional[str]:
    """The instance's logo, or None when it has none or it can't be read."""
    try:
        record = await config_service.get_config(
            get_mcp_logo_path(instance_id), default=None, use_cache=False, keep_in_cache=False,
        )
    except Exception as e:
        logger.warning(f"Could not read the logo of MCP instance {instance_id}: {e}")
        return None
    data_url = record.get("dataUrl") if isinstance(record, dict) else None
    # Only what `normalize_logo` writes is served, whatever else ends up at the key.
    if isinstance(data_url, str) and data_url.startswith(_PNG_DATA_URL_PREFIX):
        return data_url
    return None


async def delete_logo(config_service: ConfigurationService, instance_id: str) -> bool:
    """Remove the instance's logo. True when it has none afterwards, also when it had none: the
    stores answer False for deleting a key that isn't there."""
    path = get_mcp_logo_path(instance_id)
    if await config_service.delete_config(path):
        return True
    try:
        left = await config_service.get_config(
            path, default=None, use_cache=False, keep_in_cache=False, raise_on_error=True,
        )
    except Exception as e:
        logger.warning(f"Could not check the logo of MCP instance {instance_id} was removed: {e}")
        return False
    return left is None
