"""Egress proxy (P5.4): deny by default, per-sandbox allowlist, placeholder → run-bound token. Product-neutral.

The HTTP transport (``server``, ``client``) needs the ``broker-http`` extra (aiohttp); the
policy, ports, adapters and the proxy core do not.
"""

from typing import TYPE_CHECKING

from harness_core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from harness_core.sandbox.egress.addresses import (
        DEFAULT_SUBNET_POOL,
        EgressSubnet,
        SubnetPool,
    )
    from harness_core.sandbox.egress.bindings import (
        PLACEHOLDER_IN_USE,
        InMemoryEgressBindings,
    )
    from harness_core.sandbox.egress.errors import (
        EgressRefusalReason,
        EgressRefused,
        HostUnresolvable,
        TokenUnavailable,
    )
    from harness_core.sandbox.egress.policy import policy_errors
    from harness_core.sandbox.egress.ports import (
        EgressBindings,
        HostResolver,
        TokenSource,
    )
    from harness_core.sandbox.egress.proxy import EgressProxy, ProxyLimits
    from harness_core.sandbox.egress.resolver import StaticResolver, SystemResolver
    from harness_core.sandbox.egress.tokens import InMemoryTokenSource, TokenCache
    from harness_core.sandbox.egress.types import (
        EGRESS_CONTROL_PORT,
        EGRESS_PROXY_HOST,
        EGRESS_PROXY_PORT,
        EgressBinding,
        Forward,
        IssuedToken,
        ProxyRequest,
    )
    from harness_core.sandbox.egress.volume import EgressVolume, VolumeLimits

# Each name's submodule, imported when the name is first read (R25OPS-FU-1).
_EXPORTS = {
    "DEFAULT_SUBNET_POOL": "addresses",
    "EgressSubnet": "addresses",
    "SubnetPool": "addresses",
    "PLACEHOLDER_IN_USE": "bindings",
    "InMemoryEgressBindings": "bindings",
    "EgressRefusalReason": "errors",
    "EgressRefused": "errors",
    "HostUnresolvable": "errors",
    "TokenUnavailable": "errors",
    "policy_errors": "policy",
    "EgressBindings": "ports",
    "HostResolver": "ports",
    "TokenSource": "ports",
    "EgressProxy": "proxy",
    "ProxyLimits": "proxy",
    "StaticResolver": "resolver",
    "SystemResolver": "resolver",
    "InMemoryTokenSource": "tokens",
    "TokenCache": "tokens",
    "EGRESS_CONTROL_PORT": "types",
    "EGRESS_PROXY_HOST": "types",
    "EGRESS_PROXY_PORT": "types",
    "EgressBinding": "types",
    "Forward": "types",
    "IssuedToken": "types",
    "ProxyRequest": "types",
    "EgressVolume": "volume",
    "VolumeLimits": "volume",
}
__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)

__all__ = [
    "DEFAULT_SUBNET_POOL",
    "EGRESS_CONTROL_PORT",
    "EGRESS_PROXY_HOST",
    "EGRESS_PROXY_PORT",
    "PLACEHOLDER_IN_USE",
    "EgressBinding",
    "EgressBindings",
    "EgressProxy",
    "EgressRefusalReason",
    "EgressRefused",
    "EgressSubnet",
    "EgressVolume",
    "Forward",
    "HostResolver",
    "HostUnresolvable",
    "InMemoryEgressBindings",
    "InMemoryTokenSource",
    "IssuedToken",
    "ProxyLimits",
    "ProxyRequest",
    "StaticResolver",
    "SubnetPool",
    "SystemResolver",
    "TokenCache",
    "TokenSource",
    "TokenUnavailable",
    "VolumeLimits",
    "policy_errors",
]
