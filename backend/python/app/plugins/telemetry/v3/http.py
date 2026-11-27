"""aiohttp adapter for :class:`~app.plugins.telemetry.v3.sender.UsageHttpPort`."""

from __future__ import annotations

import asyncio

import aiohttp

from app.plugins.telemetry.v3.sender import CONTRACT_HEADER, HttpAnswer
from app.utils.request_context import HEADER_REQUEST_ID, get_context, new_system_root

TIMEOUT_S = 10
_DETAIL_CHARS = 200


def _retry_after(raw: str | None) -> float | None:
    try:
        seconds = float((raw or "").strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class AiohttpUsagePort:
    async def post(self, url: str, body: bytes, credential: str) -> HttpAnswer:
        ctx = get_context()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {credential}",
            HEADER_REQUEST_ID: ctx.root_id if ctx else new_system_root(),
        }
        try:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # A redirect is not followed: the credential goes to the configured collector only.
                async with session.post(url, data=body, headers=headers, allow_redirects=False) as resp:
                    text = await resp.text(errors="replace")
                    return HttpAnswer(
                        status=resp.status,
                        contract=resp.headers.get(CONTRACT_HEADER),
                        retry_after_s=_retry_after(resp.headers.get("Retry-After")),
                        detail=text[:_DETAIL_CHARS],
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as error:
            # The type only: an error text can quote the request.
            return HttpAnswer(status=None, detail=type(error).__name__)
