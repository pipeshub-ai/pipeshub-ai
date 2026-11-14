"""The parsing service's parser for Outlook .msg files: the mail as an HTML page, parsed as HTML."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.modules.parsers import parse_pool
from app.modules.parsers.email.msg_to_html import msg_to_html
from app.services.resource_governor.kinds import ParseKind

if TYPE_CHECKING:
    from app.services.parsing.interface import IParser, ParseResult

MSG_PARSE_TIMEOUT_SECONDS = 60


class MsgParser:
    def __init__(self, html_parser: IParser) -> None:
        # Built with remote fetching off: a mail's remote images are never requested.
        self._html = html_parser

    async def parse(self, content: bytes, record_name: str, config: dict[str, Any] | None = None) -> ParseResult:
        html = await asyncio.to_thread(
            parse_pool.run_blocking, msg_to_html, bytes(content),
            label=record_name, size=len(content), timeout=MSG_PARSE_TIMEOUT_SECONDS, kind=ParseKind.TEXT,
        )
        return await self._html.parse(html.encode("utf-8"), record_name, config)

    def supported_formats(self) -> list[str]:
        return ["msg"]
