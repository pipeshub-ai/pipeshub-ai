import asyncio
import base64
import os
from urllib.parse import unquote, urlparse

from app.utils.logger import create_logger
from app.utils.url_fetcher import FetchError, fetch_url

logger = create_logger(__name__)

HTTP_STATUS_OK = 200


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

def get_extension_from_mimetype(mime_type) -> str | None:
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
