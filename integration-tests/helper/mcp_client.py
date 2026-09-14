"""
Read the surface a PipesHub MCP server presents to a host.

"Surface" is everything an MCP host sees before it calls a tool: the
``initialize`` result (server info, capabilities, instructions), the tool list
(names, descriptions, input schemas, annotations), and the prompt list.
The result is plain JSON so it can be stored as a golden file and diffed.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import requests
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

MCP_PATH = "/mcp"
ASSISTANT_PROMPT = "pipeshub-assistant"


def mcp_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}{MCP_PATH}"


def _dump(model: Any) -> dict:
    return model.model_dump(mode="json", exclude_none=True, by_alias=True)


async def _read_surface(url: str, token: str) -> dict:
    async with Client(StreamableHttpTransport(url, auth=token)) as client:
        init = client.initialize_result
        tools = await client.list_tools()
        prompts = await client.list_prompts()
        prompt_texts: dict[str, str] = {}
        for prompt in prompts:
            # The server validates `arguments` as an object and rejects a missing
            # one, while fastmcp's get_prompt drops an empty dict. Call the MCP
            # session directly so `{}` reaches the wire.
            rendered = await client.session.get_prompt(prompt.name, arguments={})
            prompt_texts[prompt.name] = "\n".join(
                getattr(m.content, "text", "") for m in rendered.messages
            )

    return {
        "protocolVersion": init.protocolVersion,
        "serverInfo": _dump(init.serverInfo),
        "capabilities": _dump(init.capabilities),
        "instructions": init.instructions or "",
        "tools": [_dump(t) for t in sorted(tools, key=lambda t: t.name)],
        "prompts": [
            {**_dump(p), "text": prompt_texts.get(p.name, "")}
            for p in sorted(prompts, key=lambda p: p.name)
        ],
    }


def read_mcp_surface(base_url: str, token: str) -> dict:
    """Connect once with a bearer token and return the surface as plain JSON."""
    return asyncio.run(_read_surface(mcp_url(base_url), token))


def mcp_initialize_raw(base_url: str, token: Optional[str], timeout: int = 30) -> requests.Response:
    """
    One raw ``initialize`` POST, for status and header assertions.

    Streamable HTTP wants both JSON and SSE in ``Accept``; the server answers
    a 401 before it looks at the body.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return requests.post(
        mcp_url(base_url),
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pipeshub-integration-tests", "version": "0"},
            },
        },
        timeout=timeout,
    )
