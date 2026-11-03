"""What the fetchers that read pages on another server (Firecrawl, Crawl4AI) share.

Such a server fetches whatever URL it is given from where it runs, often inside the deployment's
network, so a URL is only sent once its host resolves to public addresses, and the address the
server ends up on is checked too. A redirect the server follows to an internal address in between
can't be seen from here; run it where it can't reach internal services (the Compose profile does).
"""

import re

from app.utils.html_to_blocks import TextBlock
from app.web.errors import AccessDenied, NoText
from app.web.fetch.http import check_public
from app.web.models import WebPage
from app.web.ports import HopCheck

MAX_BLOCK_CHARS = 2000
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


async def ensure_final_allowed(requested: str, final: str, allow_hop: HopCheck | None) -> None:
    """The page the server landed on must pass the same checks as a redirect would."""
    if final and final != requested:
        await check_public(final)
        if allow_hop is not None and not await allow_hop(final):
            raise AccessDenied(f"The page redirected to {final}, which is not allowed")


def _pieces(paragraph: str) -> list[str]:
    """``paragraph`` cut between words into pieces of at most ``MAX_BLOCK_CHARS``."""
    pieces = []
    while len(paragraph) > MAX_BLOCK_CHARS:
        cut = paragraph.rfind(" ", 0, MAX_BLOCK_CHARS)
        cut = cut if cut > 0 else MAX_BLOCK_CHARS
        pieces.append(paragraph[:cut].strip())
        paragraph = paragraph[cut:].strip()
    return [*pieces, paragraph] if paragraph else pieces


def markdown_blocks(markdown: str) -> list[TextBlock]:
    """Paragraphs of ``markdown`` grouped into blocks of at most ``MAX_BLOCK_CHARS``, so each
    citation points at a passage, not the whole page."""
    blocks: list[str] = []
    current = ""
    for paragraph in markdown.split("\n\n"):
        for piece in _pieces(paragraph.strip()):
            if current and len(current) + len(piece) + 2 > MAX_BLOCK_CHARS:
                blocks.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        blocks.append(current)
    return [TextBlock(content=block) for block in blocks]


def markdown_page(url: str, markdown: str, title: str, fetched_by: str) -> WebPage:
    blocks = markdown_blocks(markdown or "")
    if not blocks:
        raise NoText(f"No readable content could be extracted from {url}", source=fetched_by)
    if not title:
        heading = _HEADING.search(markdown)
        title = heading.group(1).strip() if heading else ""
    return WebPage(url=url, title=title[:300], blocks=tuple(blocks), fetched_by=fetched_by)
