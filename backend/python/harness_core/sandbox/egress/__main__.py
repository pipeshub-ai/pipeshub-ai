"""``python -m harness_core.sandbox.egress``: the proxy with a static control credential and in-memory tokens.

For embeddings without PipesHub's configuration service (Loom, the Docker
security tests). PipesHub runs ``app.egress_proxy_main`` instead. Configuration
is read from the environment:

- ``EGRESS_CONTROL_TOKEN`` (required): bearer the control plane accepts.
- ``EGRESS_CONTROL_HOST`` (required): the control plane's addresses, a host name or
  comma-separated addresses, never a wildcard (``control.py``); ``SANDBOX_EGRESS_SUBNETS``
  the sandbox pool whose addresses it never binds.
- ``EGRESS_TOKEN_SECRET`` (required): key of the in-memory token source.
- ``EGRESS_UPSTREAMS``: JSON ``{"<gateway host>": "http://<upstream base>"}``.
- ``EGRESS_TOKEN_TTL_S``, ``EGRESS_BLOCKED_CIDRS`` (comma-separated),
  ``EGRESS_DATA_PORT``, ``EGRESS_CONTROL_PORT``, ``EGRESS_BIND_HOST``.
- ``EGRESS_UPSTREAM_PROXY``, ``EGRESS_UPSTREAM_PROXY_AUTH``, ``EGRESS_UPSTREAM_NO_PROXY``, ``EGRESS_PRIVATE_ALLOW``:
  the deployment's forward proxy and private destinations (G-21, ``upstream_proxy.py``).
- ``EGRESS_SEALING_SECRET``: the key callers seal credentials with (``sealed.CredentialSealer``); set, the proxy
  opens sealed routes, ends TLS to their hosts with an authority of its own, and serves database routes on
  ``EGRESS_DATABASE_PORT`` (default ``POSTGRES_PORT``). Unset, sealed routes are refused.
- ``EGRESS_VOLUME_ALERT_UP_MB``, ``EGRESS_VOLUME_ALERT_DOWN_MB``, ``EGRESS_VOLUME_BASELINE_FACTOR``,
  ``EGRESS_VOLUME_BASELINE_MIN_RUNS``, ``EGRESS_VOLUME_BASELINE_FLOOR_MB``, ``EGRESS_RUN_MAX_MB``: the per-run
  volume alerts and the hard cap (C6.12, ``volume.py``); counts stay in memory here.

Bindings stay in memory and ``CONNECT`` is refused here; an embedding that needs
bindings to survive a restart builds ``DurableEgressBindings`` over its own
``EgressStateStore``, and one that offers tunnels passes
``ProxyLimits(connect_tunnels=True)`` (P5.4b).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from typing import cast

from harness_core.clock import SystemClock
from harness_core.ids import RandomIds
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
from harness_core.sandbox.egress.control import (
    control_bind_addresses_from_env,
    sandbox_subnet_pool_from_env,
)
from harness_core.sandbox.egress.interception import EgressAuthority
from harness_core.sandbox.egress.internals import internal_networks
from harness_core.sandbox.egress.policy import POSTGRES_PORT, parse_networks
from harness_core.sandbox.egress.proxy import EgressProxy, ProxyLimits
from harness_core.sandbox.egress.resolver import SystemResolver
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.tokens import DEFAULT_TOKEN_TTL_S, InMemoryTokenSource
from harness_core.sandbox.egress.types import EGRESS_CONTROL_PORT, EGRESS_PROXY_PORT
from harness_core.sandbox.egress.upstream_proxy import (
    UpstreamEgressInvalid,
    upstream_egress_from_env,
)
from harness_core.sandbox.egress.volume import (
    EgressVolume,
    VolumeLimitsInvalid,
    volume_limits_from_env,
)


def parse_upstreams(raw: str | None) -> dict[str, str]:
    parsed: object = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        raise SystemExit("EGRESS_UPSTREAMS must be a JSON object")
    return {str(k): str(v) for k, v in cast("dict[object, object]", parsed).items()}


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    control_hosts = control_bind_addresses_from_env()
    try:
        upstream_egress = upstream_egress_from_env()
        volume_limits = volume_limits_from_env()
    except (UpstreamEgressInvalid, VolumeLimitsInvalid) as exc:
        raise SystemExit(str(exc)) from None
    upstreams = parse_upstreams(os.getenv("EGRESS_UPSTREAMS"))
    resolver = SystemResolver()
    internals = await internal_networks(
        (*upstreams.values(), *control_hosts), resolver, (sandbox_subnet_pool_from_env(),)
    )
    clock, ids = SystemClock(), RandomIds()
    bindings = InMemoryEgressBindings()
    sealing = os.getenv("EGRESS_SEALING_SECRET")
    sealed = SealedTokenSource(CredentialSealer(sealing.encode()), clock) if sealing else None
    proxy = EgressProxy(
        bindings=bindings,
        tokens=RoutedTokenSource(
            InMemoryTokenSource(
                os.environ["EGRESS_TOKEN_SECRET"].encode(),
                clock,
                ttl_s=int(os.getenv("EGRESS_TOKEN_TTL_S") or DEFAULT_TOKEN_TTL_S),
            ),
            sealed,
        ),
        resolver=resolver,
        upstreams=upstreams,
        clock=clock,
        ids=ids,
        limits=ProxyLimits(
            blocked_networks=parse_networks((os.getenv("EGRESS_BLOCKED_CIDRS") or "").split(",")),
            intercepts=sealed is not None,
        ),
        upstream_egress=upstream_egress,
        internal_networks=internals,
        volume=EgressVolume(volume_limits, clock=clock, bindings=bindings),
    )
    server = EgressServer(
        proxy,
        StaticBearerAuth(os.environ["EGRESS_CONTROL_TOKEN"]),
        instance=ids.new_id("egress"),
        authority=EgressAuthority.generate(clock) if sealed is not None else None,
    )
    _ = await server.start(
        host=os.getenv("EGRESS_BIND_HOST") or "0.0.0.0",
        data_port=int(os.getenv("EGRESS_DATA_PORT") or EGRESS_PROXY_PORT),
        control_port=int(os.getenv("EGRESS_CONTROL_PORT") or EGRESS_CONTROL_PORT),
        control_host=control_hosts,
        database_port=int(os.getenv("EGRESS_DATABASE_PORT") or POSTGRES_PORT) if sealed is not None else None,
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stopped.set)
    try:
        _ = await stopped.wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
