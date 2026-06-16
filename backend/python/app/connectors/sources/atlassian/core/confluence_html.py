"""Helpers for Confluence v1 HTML content (streaming / indexing)."""

from __future__ import annotations

import base64
import html as html_module
from logging import Logger
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.utils.image_utils import get_extension_from_mimetype

_URL_ATTRIBUTES = frozenset({
    "href",
    "src",
    "action",
    "poster",
    "data",
    "formaction",
    "cite",
    "background",
    "xlink:href",
})

_SKIP_URL_PREFIXES = (
    "http://",
    "https://",
    "mailto:",
    "javascript:",
    "data:",
    "tel:",
)


def extract_content_base_url(response_data: dict[str, Any]) -> Optional[str]:
    """Return Confluence instance base URL from a v1 content API response."""
    links = response_data.get("_links") or {}
    base = links.get("base")
    if base:
        return str(base)

    self_link = links.get("self")
    if isinstance(self_link, str) and "/rest/api/" in self_link:
        return self_link.split("/rest/api/")[0]

    return None


def _should_resolve_url(url: str) -> bool:
    stripped = url.strip()
    if not stripped or stripped.startswith("#"):
        return False
    lower = stripped.lower()
    return not lower.startswith(_SKIP_URL_PREFIXES)


def _resolve_url(url: str, base_url: str) -> str:
    if not _should_resolve_url(url):
        return url
    return urljoin(base_url, url)


def _resolve_srcset(value: str, base_url: str) -> str:
    resolved_parts: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        tokens = item.split()
        if tokens:
            tokens[0] = _resolve_url(tokens[0], base_url)
            resolved_parts.append(" ".join(tokens))
    return ", ".join(resolved_parts)


def resolve_relative_urls_in_html(html: str, base_url: str) -> str:
    """Rewrite root-relative and path-relative URLs in HTML to absolute URLs."""
    if not html or not base_url:
        return html

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(True):
        for attr, value in list(tag.attrs.items()):
            if attr == "srcset" and isinstance(value, str):
                tag[attr] = _resolve_srcset(value, base_url)
                continue

            if attr not in _URL_ATTRIBUTES:
                continue

            if isinstance(value, list):
                tag[attr] = [_resolve_url(str(v), base_url) for v in value]
            elif isinstance(value, str):
                tag[attr] = _resolve_url(value, base_url)

    return str(soup)


# ---------------------------------------------------------------------------
# Image inlining helpers (authenticated downloads)
# ---------------------------------------------------------------------------


def is_data_uri(url: str) -> bool:
    """Check if URL is a data URI (already base64-encoded)."""
    return url.strip().lower().startswith("data:")


def is_same_origin(url: str, base_url: str) -> bool:
    """Check if URL is same-origin as base_url after resolution.
    
    Args:
        url: Potentially relative or absolute URL to check
        base_url: Base URL to compare against
        
    Returns:
        True if URL resolves to same scheme+netloc as base_url, False otherwise
    """
    if not url or not base_url:
        return False
    
    try:
        # Resolve relative URLs first
        absolute_url = urljoin(base_url, url)
        
        parsed_url = urlparse(absolute_url)
        parsed_base = urlparse(base_url)
        
        return (
            parsed_url.scheme == parsed_base.scheme
            and parsed_url.netloc == parsed_base.netloc
        )
    except Exception:
        return False


def is_image_content_type(content_type: str) -> bool:
    """Check if content-type indicates an image."""
    if not content_type:
        return False
    return content_type.lower().startswith("image/")


def bytes_to_data_uri(
    content: bytes,
    content_type: str,
    url: str = "",
) -> str | None:
    """Convert image bytes to a data URI.
    
    Args:
        content: Raw image bytes
        content_type: MIME type from Content-Type header
        url: Original URL (used for extension fallback)
        
    Returns:
        Data URI string (data:image/{ext};base64,...) or None if not an image
    """
    if not is_image_content_type(content_type):
        return None
    
    content_type_lower = content_type.lower()
    
    # Handle SVG - pass through as-is (SVG data URIs work natively in browsers)
    if content_type_lower == 'image/svg+xml' or 'svg' in content_type_lower:
        svg_base64 = base64.b64encode(content).decode('utf-8')
        return f"data:image/svg+xml;base64,{svg_base64}"
    
    # Get extension from MIME type
    extension = get_extension_from_mimetype(content_type)
    
    # Fallback: try to extract from URL path
    if not extension and url:
        try:
            parsed = urlparse(url)
            path = parsed.path
            if '.' in path:
                extension = path.split('.')[-1].lower()
        except Exception:
            pass
    
    # Default to png if we still don't have an extension
    if not extension:
        extension = 'png'
    
    # Encode to base64
    image_base64 = base64.b64encode(content).decode('utf-8')
    return f"data:image/{extension};base64,{image_base64}"


async def inline_authenticated_images_in_html(
    html: str,
    base_url: str,
    download: Callable[[str], Awaitable[tuple[bytes, str] | None]],
    *,
    logger: Logger | None = None,
) -> str:
    """Inline same-origin images in HTML by downloading and converting to data URIs.
    
    Security: Only downloads same-origin URLs to prevent SSRF attacks.
    SVG images are passed through as SVG data URIs (natively supported by browsers).
    
    Args:
        html: HTML content to process
        base_url: Confluence instance base URL (for origin checking)
        download: Async callable that takes a URL and returns (bytes, content_type) or None
        logger: Optional logger for warnings
        
    Returns:
        Modified HTML with inlined images (failures leave src unchanged)
    """
    if not html or not base_url:
        return html
    
    soup = BeautifulSoup(html, "html.parser")
    
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src or not isinstance(src, str):
            continue
        
        # Skip data URIs (already base64)
        if is_data_uri(src):
            continue
        
        # Skip external origin (security: prevent SSRF)
        if not is_same_origin(src, base_url):
            continue
        
        # Resolve to absolute URL
        absolute_src = urljoin(base_url, src)
        
        # Download with authentication
        try:
            result = await download(absolute_src)
            if result is None:
                # Download failed, leave src unchanged
                if logger:
                    logger.warning(f"Failed to download image: {absolute_src[:100]}")
                continue
            
            content, content_type = result
            
            # Convert to data URI
            data_uri = bytes_to_data_uri(
                content,
                content_type,
                absolute_src,
            )
            
            if data_uri:
                img['src'] = data_uri
            else:
                # Not a valid image content-type, leave unchanged
                if logger:
                    logger.warning(
                        f"Image has invalid content-type '{content_type}': {absolute_src[:100]}"
                    )
        except Exception as e:
            # Unexpected error, leave src unchanged
            if logger:
                logger.warning(f"Error inlining image {absolute_src[:100]}: {e}")
            continue
    
    return str(soup)


def prepend_title_to_html(html: str, title: str | None) -> str:
    """Prepend document title as H1 so indexing includes it in searchable content."""
    if not title or not str(title).strip():
        return html
    safe_title = html_module.escape(str(title).strip())
    return f"<h1>{safe_title}</h1>\n{html}"


async def prepare_streaming_html(
    html: str,
    response_data: dict[str, Any],
    download: Callable[[str], Awaitable[tuple[bytes, str] | None]],
    *,
    title: str | None = None,
    logger: Logger | None = None,
) -> str:
    """Prepare HTML for streaming by resolving URLs, inlining images, and prepending title.
    
    Pipeline:
    1. Extract base URL from Confluence API response
    2. Resolve relative URLs to absolute
    3. Inline same-origin images via authenticated downloads (SVGs passed through as-is)
    4. Prepend page/blogpost/comment title as H1 (export_view omits title)
    
    Args:
        html: Raw HTML from Confluence body.export_view
        response_data: Full API response JSON (for extracting base URL and title)
        download: Async callable for authenticated image downloads
        title: Optional title override (e.g. record.record_name for comments)
        logger: Optional logger
        
    Returns:
        Processed HTML ready for indexing
    """
    base_url = extract_content_base_url(response_data)

    if base_url:
        html = resolve_relative_urls_in_html(html, base_url)
        html = await inline_authenticated_images_in_html(
            html,
            base_url,
            download,
            logger=logger,
        )

    effective_title = str(title or response_data.get("title") or "").strip()
    if effective_title:
        html = prepend_title_to_html(html, effective_title)

    return html
