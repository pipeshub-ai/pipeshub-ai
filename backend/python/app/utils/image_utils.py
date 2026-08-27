import asyncio
import base64
import os
import re
from urllib.parse import unquote, urlparse

from app.utils.logger import create_logger
from app.utils.url_fetcher import FetchError, fetch_url

logger = create_logger(__name__)

HTTP_STATUS_OK = 200
_BASE64_CHARSET_RE = re.compile(r"[A-Za-z0-9+/=_-]+")


def normalize_image_to_base64(image_uri: str | None) -> str | None:
    """Strip a ``data:...;base64,`` prefix (if present) and pad to a valid
    base64 length. Returns ``None`` for empty/non-base64 input.

    Shared by every multimodal image-embedding provider so each one doesn't
    reimplement the same data-URI parsing / padding logic.
    """
    try:
        if not image_uri or not isinstance(image_uri, str):
            return None
        uri = image_uri.strip()
        if uri.startswith("data:"):
            comma_index = uri.find(",")
            if comma_index == -1:
                return None
            # A data URI without `;base64` carries percent-encoded text, not
            # base64 — `data:image/svg+xml,<svg…>` would otherwise be handed
            # to an embedding provider as if it were image bytes.
            if ";base64" not in uri[:comma_index].lower():
                return None
            candidate = uri[comma_index + 1:]
        else:
            candidate = uri
        candidate = candidate.strip().replace("\n", "").replace("\r", "").replace(" ", "")
        if not _BASE64_CHARSET_RE.fullmatch(candidate):
            return None
        missing = (-len(candidate)) % 4
        if missing:
            candidate += "=" * missing
        return candidate
    except Exception:
        return None


def get_mime_type_from_base64(b64: str) -> str | None:
    # Decode only the first few bytes
    header = base64.b64decode(b64[:20])

    if header[:4] == b'\x89PNG':
        return 'image/png'
    if header[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if header[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return 'image/webp'
    if header[:2] == b'BM':
        return 'image/bmp'
    if header[:4] in (b'II*\x00', b'MM\x00*'):
        return 'image/tiff'
    if header[:4] == b'\x00\x00\x01\x00':
        return 'image/x-icon'

    return None

def read_image_dimensions(image_uri: str | None) -> tuple[int, int] | None:
    """`(width, height)` of an image, read from its header bytes alone.

    Selection needs a size signal for every image, but only the pdfplumber
    parser records `image_metadata.image_size` -- Docling, HTML and Markdown
    do not -- so most images arrive unmeasured. A full decode per image on the
    request path would be far too expensive; every format below states its
    dimensions within the first few dozen bytes, so ~200 decoded bytes answer
    the question.

    Returns None when the format is unrecognized or the header is truncated.
    Callers must treat None as "unknown", never as "too small" -- dropping a
    real figure because its header was unusual is the expensive mistake.
    """
    b64 = normalize_image_to_base64(image_uri)
    if not b64:
        return None
    try:
        # 512 base64 chars -> 384 bytes: past the JPEG APPn/EXIF blocks that
        # commonly precede the SOF marker, and a multiple of 4.
        header = base64.b64decode(b64[:512] + "=" * ((-len(b64[:512])) % 4))
    except Exception:
        return None

    try:
        if header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
            return (
                int.from_bytes(header[16:20], "big"),
                int.from_bytes(header[20:24], "big"),
            )
        if header[:6] in (b"GIF87a", b"GIF89a"):
            return (
                int.from_bytes(header[6:8], "little"),
                int.from_bytes(header[8:10], "little"),
            )
        if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
            return _webp_dimensions(header)
        if header[:2] == b"\xff\xd8":
            return _jpeg_dimensions(header)
        if header[:2] == b"BM" and len(header) >= 26:
            return (
                int.from_bytes(header[18:22], "little", signed=True),
                abs(int.from_bytes(header[22:26], "little", signed=True)),
            )
    except Exception:
        return None
    return None


def _webp_dimensions(header: bytes) -> tuple[int, int] | None:
    """WebP stores dimensions differently in each of its three chunk types."""
    chunk = header[12:16]
    if chunk == b"VP8X" and len(header) >= 30:
        return (
            int.from_bytes(header[24:27], "little") + 1,
            int.from_bytes(header[27:30], "little") + 1,
        )
    if chunk == b"VP8L" and len(header) >= 25:
        bits = int.from_bytes(header[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8 " and len(header) >= 30:
        return (
            int.from_bytes(header[26:28], "little") & 0x3FFF,
            int.from_bytes(header[28:30], "little") & 0x3FFF,
        )
    return None


# JPEG frame markers that carry dimensions. DHT/DAC/RST/SOS and the
# arithmetic-coded variants at 0xC4/0xC8/0xCC are not frame headers.
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def _jpeg_dimensions(header: bytes) -> tuple[int, int] | None:
    """Walk JPEG segment headers to the first SOF, which holds the size."""
    i = 2
    end = len(header)
    while i + 9 < end:
        if header[i] != 0xFF:
            i += 1
            continue
        marker = header[i + 1]
        if marker in _JPEG_SOF_MARKERS:
            return (
                int.from_bytes(header[i + 7:i + 9], "big"),
                int.from_bytes(header[i + 5:i + 7], "big"),
            )
        segment_length = int.from_bytes(header[i + 2:i + 4], "big")
        if segment_length < 2:
            return None
        i += 2 + segment_length
    return None


# What separates a figure from page furniture. Used on both sides of the
# system -- indexing asks it before paying a VLM to describe an image, the
# query path asks it before spending a scarce image slot -- so the two can
# never disagree about what counts as content.
#
# Deliberately loose: a false keep costs one model call or one slot, a false
# drop loses content with no trace.
MIN_CONTENT_SHORT_EDGE_PX = 64      # below this: bullets, inline icons, spacers
MIN_CONTENT_AREA_PX = 10_000        # favicons, badges, avatars
MAX_CONTENT_ASPECT_RATIO = 10.0     # rules, dividers, gradient strips


def is_below_content_size(width: int | None, height: int | None) -> bool:
    """Too small to be a figure. Unknown dimensions are never "too small"."""
    if not width or not height:
        return False
    return min(width, height) < MIN_CONTENT_SHORT_EDGE_PX or width * height < MIN_CONTENT_AREA_PX


def is_extreme_aspect_ratio(width: int | None, height: int | None) -> bool:
    """Long and thin: a rule or a divider, not content."""
    if not width or not height:
        return False
    short_edge = min(width, height)
    return short_edge > 0 and (max(width, height) / short_edge) > MAX_CONTENT_ASPECT_RATIO


def is_decorative_image(width: int | None, height: int | None) -> bool:
    return is_below_content_size(width, height) or is_extreme_aspect_ratio(width, height)


def downscale_to_limits(
    image_uri: str,
    *,
    max_long_edge_px: int,
    max_bytes: int,
) -> str:
    """Re-encode `image_uri` to fit a model's limits, or return it unchanged.

    Only does work when the image actually exceeds a limit: providers cap both
    dimensions and payload size (Bedrock rejects an image over 3.75 MB;
    Anthropic tightens per-image dimensions once a request carries more than
    20 image blocks), and every model downscales internally anyway, so
    resolution beyond its native raster costs tokens and buys nothing.

    Never raises: an image we cannot re-encode is still better sent as-is than
    dropped, and the provider's own limits are enforced again at the wire.
    """
    b64 = normalize_image_to_base64(image_uri)
    if not b64:
        return image_uri

    dimensions = read_image_dimensions(image_uri)
    too_large = bool(dimensions) and max(dimensions) > max_long_edge_px  # type: ignore[arg-type]
    # 4 base64 chars per 3 bytes; compare against the encoded size, which is
    # what actually travels.
    too_heavy = (len(b64) * 3) // 4 > max_bytes
    if not (too_large or too_heavy):
        return image_uri

    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(base64.b64decode(b64))) as source:
            image = source.convert("RGB") if source.mode not in ("RGB", "L") else source.copy()
        if max(image.size) > max_long_edge_px:
            ratio = max_long_edge_px / max(image.size)
            image = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                Image.LANCZOS,
            )
        # JPEG at 85 is the standard "no visible artifacts" setting; heavier
        # compression starts making small text in screenshots unreadable,
        # which is usually the thing the model was sent the image to read.
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        logger.debug("Could not downscale image; sending it unchanged", exc_info=True)
        return image_uri

    if len(encoded) >= len(b64) and not too_large:
        # Re-encoding made it bigger (already-optimized PNG line art): keep
        # the original rather than paying more bytes for fewer pixels.
        return image_uri
    return f"data:image/jpeg;base64,{encoded}"


def get_extension_from_mimetype(mime_type: str | None) -> str | None:
    return mime_to_extension.get(mime_type)

def get_image_info_from_url(url: str) -> tuple[str | None, str | None]:
    """
    Extract image extension and guessed MIME type from URL only.
    Does NOT make any network request.

    Returns:
        {
            "extension": ".png" | None,
            "mime": "image/png" | None
        }
    """
    if not url:
        return None, None

    # remove query params and decode URL
    path = unquote(urlparse(url).path)

    # extract extension
    _, ext = os.path.splitext(path)
    ext = ext.lower()

    if not ext:
        return None, None

    return ext, EXT_TO_MIME.get(ext)  # None if unknown


supported_mime_types = ["image/png", "image/jpeg", "image/webp"]


async def _fetch_image_as_base64(img_url: str) -> tuple[str, str] | None:
    """
    Fetch an image from http(s) URL and return (base64_string, mime_type).
    Returns None on failure.
    """
    try:

        _, mime_type = get_image_info_from_url(img_url)

        if mime_type and mime_type not in supported_mime_types:
            logger.warning("Image mime type not supported, skipping fetch: %s", mime_type)
            return None

        result = await asyncio.to_thread(
            fetch_url,
            img_url,
            max_retries=0,
            strategy="curl_cffi_h2",
            profile="chrome120",
            block_private_hosts=False,
        )
        if result.status_code != HTTP_STATUS_OK or not result.content:
            logger.warning("Failed to fetch image as base64 from %s: %s", img_url, f"status_code: {result.status_code}, content: {result.content[:100]}")
            return None
        b64 = base64.b64encode(result.content).decode("utf-8")

        mime_type = None
        if b64.startswith("data:image/"):
            mime_type = b64.split(";")[0].split(":")[1]

        if not mime_type:
            mime_type = get_mime_type_from_base64(b64)


        if not mime_type or not mime_type.startswith("image/") or mime_type not in supported_mime_types:
            logger.warning("Failed to fetch image as base64 from %s: %s", img_url, f"mime_type not found/supported, mimeType: {mime_type}")
            return None

        return (b64, mime_type)
    except (FetchError, Exception) as e:
        logger.warning("Failed to fetch image as base64 from %s: %s", img_url, e)
        return None



# extension -> MIME mapping
EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
}


mime_to_extension = {
    # PNG
    "image/png": "png",
    "image/x-png": "png",
    "image/png; charset=binary": "png",
    "application/png": "png",
    "application/x-png": "png",
    "image/vnd.mozilla.apng": "png",

    # JPEG/JPG
    "image/jpeg": "jpeg",
    "image/jpg": "jpg",
    "image/x-jpeg": "jpeg",
    "image/x-jpg": "jpg",
    "image/pjpeg": "jpeg",
    "image/jpeg; charset=binary": "jpeg",
    "image/jpg; charset=binary": "jpg",

    # WEBP
    "image/webp": "webp",
    "image/x-webp": "webp",

    # SVG
    "image/svg+xml": "svg",
    "image/svg": "svg",
    "image/svg+xml; charset=utf-8": "svg",
    "application/svg+xml": "svg",
    "text/xml-svg": "svg",
    "application/xml-svg": "svg",

    # PDF
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "application/acrobat": "pdf",
    "application/vnd.pdf": "pdf",
    "text/pdf": "pdf",
    "text/x-pdf": "pdf",

    # DOCX
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document+xml": "docx",

    # DOC
    "application/msword": "doc",
    "application/x-msword": "doc",
    "application/msword; charset=utf-8": "doc",
    "application/x-msword; charset=utf-8": "doc",
    "application/doc": "doc",
    "application/x-doc": "doc",
    "zz-application/zz-winassoc-doc": "doc",

    # XLSX
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet+xml": "xlsx",

    # XLS
    "application/vnd.ms-excel": "xls",
    "application/vnd.ms-excel.sheet.macroEnabled.12": "xls",
    "application/x-msexcel": "xls",
    "application/x-excel": "xls",
    "application/excel": "xls",
    "application/xls": "xls",
    "application/x-xls": "xls",
    "application/vnd.ms-excel; charset=utf-8": "xls",
    "zz-application/zz-winassoc-xls": "xls",

    # CSV
    "text/csv": "csv",
    "application/csv": "csv",
    "text/comma-separated-values": "csv",
    "text/x-comma-separated-values": "csv",
    "text/x-csv": "csv",
    "application/csv; charset=utf-8": "csv",
    "text/csv; charset=utf-8": "csv",
    "text/csv; charset=us-ascii": "csv",

    # TSV
    "text/tab-separated-values": "tsv",
    "text/tsv": "tsv",
    "application/tsv": "tsv",
    "text/tab-separated-values; charset=utf-8": "tsv",
    "text/tsv; charset=utf-8": "tsv",

    # PPTX
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation+xml": "pptx",

    # PPT
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.12": "ppt",
    "application/x-mspowerpoint": "ppt",
    "application/powerpoint": "ppt",
    "application/x-ppt": "ppt",
    "application/vnd.ms-powerpoint; charset=utf-8": "ppt",
    "zz-application/zz-winassoc-ppt": "ppt",

    # MDX
    "text/mdx": "mdx",
    "text/x-mdx": "mdx",
    "application/mdx": "mdx",
    "application/x-mdx": "mdx",
    "text/mdx; charset=utf-8": "mdx",

    "text/plain": "txt",
    "text/plain; charset=utf-8": "txt",
    "text/plain; charset=us-ascii": "txt",
    "text/plain; charset=iso-8859-1": "txt",
    "text/plain; charset=windows-1252": "txt",
    "text/plain; charset=ascii": "txt",
    "text/x-text": "txt",
    "text/txt": "txt",
    "application/text": "txt",
    "application/txt": "txt",
    "text/html": "html",
    "text/html; charset=utf-8": "html",
    "text/html; charset=us-ascii": "html",
    "text/html; charset=iso-8859-1": "html",
    "text/html; charset=windows-1252": "html",
    "text/html; charset=ascii": "html",
    "application/xhtml+xml": "html",
    "application/xhtml": "html",
    "text/xhtml": "html",
    "application/html": "html",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/x-md": "md",
    "application/markdown": "md",
    "application/x-markdown": "md",
    "text/markdown; charset=utf-8": "md",
    "text/markdown; charset=us-ascii": "md",
    "text/gmail_content": "html",
}
