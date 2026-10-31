"""
Utility functions for processing HTML content in connector pipelines.
"""

import base64
import logging
from typing import Callable, Optional

import httpx
from bs4 import BeautifulSoup


async def embed_html_images_as_base64(
    html_content: str,
    http_client: httpx.AsyncClient,
    logger: logging.Logger,
    url_filter: Optional[Callable[[str], bool]] = None,
    max_bytes: Optional[int] = None,
) -> str:
    """
    Download external images referenced in HTML and replace their ``src``
    attributes with inline base64 data URIs.

    Args:
        html_content: Raw HTML string to process.
        http_client:  Shared ``httpx.AsyncClient`` used to fetch images.
        logger:       Logger instance for warnings/errors.
        url_filter:   Optional predicate; when provided, only ``<img>`` tags
                      whose ``src`` satisfies the predicate are processed.
                      Tags that do not match are left unchanged.
        max_bytes:    Optional size limit; an image larger than this is not
                      downloaded past the limit and its tag is left unchanged.

    Returns:
        HTML string with matching image ``src`` attributes replaced by data URIs.
        Returns the original string unchanged if it is empty or has no ``<img>``
        tags.
    """
    if not html_content:
        return html_content

    soup = BeautifulSoup(html_content, "html.parser")
    img_tags = soup.find_all("img")

    if not img_tags:
        return html_content

    for img_tag in img_tags:
        src = img_tag.get("src", "")
        if not src:
            continue

        if url_filter is not None and not url_filter(src):
            continue

        try:
            if max_bytes is None:
                response = await http_client.get(src)
                status_code, headers = response.status_code, response.headers
                image_bytes = response.content if status_code == 200 else b""
            else:
                image_bytes, status_code, headers = await _get_bounded(http_client, src, max_bytes)
                if image_bytes is None:
                    logger.warning("Skipping image larger than %d bytes: %s", max_bytes, src)
                    continue

            if status_code != 200:
                logger.error(
                    "Failed to download image: %s - Status: %s",
                    src,
                    status_code,
                )
                continue

            mime_type = headers.get("Content-Type", "image/png")

            # Fallback magic-byte detection when the server returns a generic
            # octet-stream content type.
            if "octet-stream" in mime_type:
                if image_bytes.startswith(b"\xff\xd8\xff"):
                    mime_type = "image/jpeg"
                elif image_bytes.startswith(b"\x89PNG"):
                    mime_type = "image/png"
                elif image_bytes.startswith(b"GIF"):
                    mime_type = "image/gif"

            b64 = base64.b64encode(image_bytes).decode("utf-8")
            img_tag["src"] = f"data:{mime_type};base64,{b64}"

        except Exception as e:
            logger.error("Error processing image %s: %s", src, e, exc_info=True)
            continue

    return str(soup)


async def _get_bounded(
    http_client: httpx.AsyncClient, url: str, max_bytes: int
) -> tuple[Optional[bytes], int, httpx.Headers]:
    """GET ``url`` reading at most ``max_bytes``: (body, status, headers), body None when larger."""
    async with http_client.stream("GET", url) as response:
        if response.status_code != 200:
            return b"", response.status_code, response.headers
        declared = response.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            return None, response.status_code, response.headers
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                return None, response.status_code, response.headers
            chunks.append(chunk)
        return b"".join(chunks), response.status_code, response.headers
