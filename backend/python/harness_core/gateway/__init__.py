"""The run-scoped tool gateway (E2, ARCHITECTURE §4.4.2): the core an MCP transport serves.

Product-neutral. Ports: ``ToolEndpoint`` (what a transport serves),
``CitationSink`` (the run's citation state), ``GatewayKeySource`` (the issuer's
public keys). Adapters: ``RunToolEndpoint`` over the Invoker,
``InMemoryCitationSink``, ``StaticGatewayKeySource``. Contract suites:
``harness_core.testing.contracts.tool_endpoint`` and ``.gateway``.
"""

from typing import TYPE_CHECKING

from harness_core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from harness_core.gateway.disclosure import (
        CALL_TOOL,
        SEARCH_TOOLS,
        LazyDisclosure,
        Ranked,
    )
    from harness_core.gateway.endpoint import BoundedHold, RunToolEndpoint
    from harness_core.gateway.errors import (
        TOKEN_REFUSAL_CODES,
        BindingMismatch,
        ForbiddenArguments,
        GatewayRefusal,
        TokenRefused,
    )
    from harness_core.gateway.memory import (
        InMemoryCitationSink,
        StaticGatewayKeySource,
        refs_in,
    )
    from harness_core.gateway.ports import (
        CitationSink,
        GatewayKeySource,
        Progress,
        PublishedKey,
        PublishedKeys,
        ToolEndpoint,
    )
    from harness_core.gateway.token import (
        GATEWAY_TOKEN_AUDIENCE,
        GATEWAY_TOKEN_ISSUER,
        GATEWAY_TOKEN_TTL_S,
        GatewayTokenVerifier,
    )
    from harness_core.gateway.types import (
        PROGRESS_INTERVAL_S,
        Citation,
        GatewayBinding,
        GatewayClaims,
        McpResult,
        McpTool,
        RunAttemptBinding,
        grant_digest,
    )

# Each name's submodule, imported when the name is first read (R25OPS-FU-1).
_EXPORTS = {
    "CALL_TOOL": "disclosure",
    "SEARCH_TOOLS": "disclosure",
    "LazyDisclosure": "disclosure",
    "Ranked": "disclosure",
    "BoundedHold": "endpoint",
    "RunToolEndpoint": "endpoint",
    "TOKEN_REFUSAL_CODES": "errors",
    "BindingMismatch": "errors",
    "ForbiddenArguments": "errors",
    "GatewayRefusal": "errors",
    "TokenRefused": "errors",
    "InMemoryCitationSink": "memory",
    "StaticGatewayKeySource": "memory",
    "refs_in": "memory",
    "CitationSink": "ports",
    "GatewayKeySource": "ports",
    "Progress": "ports",
    "PublishedKey": "ports",
    "PublishedKeys": "ports",
    "ToolEndpoint": "ports",
    "GATEWAY_TOKEN_AUDIENCE": "token",
    "GATEWAY_TOKEN_ISSUER": "token",
    "GATEWAY_TOKEN_TTL_S": "token",
    "GatewayTokenVerifier": "token",
    "PROGRESS_INTERVAL_S": "types",
    "Citation": "types",
    "GatewayBinding": "types",
    "GatewayClaims": "types",
    "McpResult": "types",
    "McpTool": "types",
    "RunAttemptBinding": "types",
    "grant_digest": "types",
}
__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)

__all__ = [
    "CALL_TOOL",
    "SEARCH_TOOLS",
    "LazyDisclosure",
    "Ranked",
    "GATEWAY_TOKEN_AUDIENCE",
    "GATEWAY_TOKEN_ISSUER",
    "GATEWAY_TOKEN_TTL_S",
    "PROGRESS_INTERVAL_S",
    "TOKEN_REFUSAL_CODES",
    "BindingMismatch",
    "BoundedHold",
    "Citation",
    "CitationSink",
    "ForbiddenArguments",
    "GatewayBinding",
    "GatewayClaims",
    "GatewayKeySource",
    "GatewayRefusal",
    "GatewayTokenVerifier",
    "InMemoryCitationSink",
    "McpResult",
    "McpTool",
    "Progress",
    "PublishedKey",
    "PublishedKeys",
    "RunAttemptBinding",
    "RunToolEndpoint",
    "StaticGatewayKeySource",
    "TokenRefused",
    "ToolEndpoint",
    "grant_digest",
    "refs_in",
]
