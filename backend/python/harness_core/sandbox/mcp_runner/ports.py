"""The MCP runner port (D23): STDIO MCP servers run in runners, never in a trusted process."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harness_core.sandbox.mcp_runner.types import RunnerInfo, RunnerRequest
    from harness_core.sandbox.ports import ByteStream


class MCPRunner(Protocol):
    async def open(self, request: RunnerRequest) -> ByteStream:
        """A new MCP session with the server of ``request.key``: newline-delimited JSON-RPC both ways.

        Starts the key's runner on first use and reuses it after. Raises an
        ``MCPRunnerRefused`` before anything starts for a request it will not
        serve (unpinned command, undeclared secret, another owner's secret).
        Closing the stream ends the session; the runner stays until it idles out.
        """
        ...

    async def sweep(self) -> int:
        """Stop runners idle past their TTL or past their max age with no session; the number stopped."""
        ...

    async def stop_all(self) -> int:
        """Stop every runner this adapter started; the number stopped."""
        ...

    def runners(self) -> tuple[RunnerInfo, ...]: ...


__all__ = ["MCPRunner"]
