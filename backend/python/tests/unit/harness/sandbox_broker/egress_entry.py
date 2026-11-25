"""The P5.4b Docker lanes' proxy: the generic proxy plus what PipesHub's role adds (tunnels, Redis state).

``python -m harness_core.sandbox.egress`` keeps bindings in memory and refuses
``CONNECT``; this entry runs the same core with ``ProxyLimits(connect_tunnels=…)``
and, when ``EGRESS_TEST_REDIS_URL`` is set, ``DurableEgressBindings`` over the
production ``RedisEgressStateStore``, loaded before it serves (as
``app.egress_proxy_main`` does); with ``EGRESS_TEST_ATTEMPT_TOKENS=1`` it issues
tokens only for the attempt a binding names, as Node does (SIGNFIX). The rest of
its environment is the generic entry's, the upstream egress settings (G-21) and the volume bounds (C6.12) included;
volume counts are kept in the Redis state with the bindings.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import TYPE_CHECKING

from app.harness.sandbox.egress_config import kill_switch_on
from app.harness.sandbox.egress_state import RedisEgressStateStore
from harness_core.clock import SystemClock
from harness_core.ids import RandomIds
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.__main__ import parse_upstreams
from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
from harness_core.sandbox.egress.control import (
    control_bind_addresses_from_env,
    sandbox_subnet_pool_from_env,
)
from harness_core.sandbox.egress.interception import EgressAuthority
from harness_core.sandbox.egress.internals import internal_networks
from harness_core.sandbox.egress.policy import parse_networks
from harness_core.sandbox.egress.proxy import EgressProxy, ProxyLimits
from harness_core.sandbox.egress.resolver import SystemResolver
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.state import DurableEgressBindings, StateSealer
from harness_core.sandbox.egress.tokens import InMemoryTokenSource
from harness_core.sandbox.egress.types import EGRESS_CONTROL_PORT, EGRESS_PROXY_PORT
from harness_core.sandbox.egress.upstream_proxy import upstream_egress_from_env
from harness_core.sandbox.egress.volume import EgressVolume, volume_limits_from_env

if TYPE_CHECKING:
    from harness_core.sandbox.egress.ports import TokenSource


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    clock, ids = SystemClock(), RandomIds()
    instance = ids.new_id("egress")
    kill_switch = kill_switch_on()
    bindings = InMemoryEgressBindings(kill_switch=kill_switch)
    redis_url = os.getenv("EGRESS_TEST_REDIS_URL")
    store: RedisEgressStateStore | None = None
    sealer: StateSealer | None = None
    if redis_url:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]
        store = RedisEgressStateStore(client, namespace="pxh-p54b:")
        sealer = StateSealer(os.environ["EGRESS_TEST_STATE_KEY"].encode())
        durable = DurableEgressBindings(store, sealer, clock, kill_switch=kill_switch)
        instance = (await durable.load(instance)).instance
        bindings = durable
    secret = os.environ["EGRESS_TOKEN_SECRET"].encode()
    tokens: TokenSource = InMemoryTokenSource(secret, clock)
    if os.getenv("EGRESS_TEST_ATTEMPT_TOKENS") == "1":
        from harness_core.testing.sandbox.egress import AttemptTokenSource

        tokens = AttemptTokenSource(secret, clock)
    sealing = os.getenv("EGRESS_SEALING_SECRET")
    sealed = SealedTokenSource(CredentialSealer(sealing.encode()), clock) if sealing else None
    upstreams = parse_upstreams(os.getenv("EGRESS_UPSTREAMS"))
    resolver = SystemResolver()
    control_hosts = control_bind_addresses_from_env()
    proxy = EgressProxy(
        bindings=bindings,
        tokens=RoutedTokenSource(tokens, sealed),
        resolver=resolver,
        upstreams=upstreams,
        clock=clock,
        ids=ids,
        limits=ProxyLimits(
            connect_tunnels=os.getenv("EGRESS_TEST_TUNNELS") == "1",
            intercepts=sealed is not None,
            blocked_networks=parse_networks((os.getenv("EGRESS_BLOCKED_CIDRS") or "").split(",")),
        ),
        upstream_egress=upstream_egress_from_env(),
        internal_networks=await internal_networks(
            (*upstreams.values(), *control_hosts), resolver, (sandbox_subnet_pool_from_env(),)
        ),
        volume=EgressVolume(volume_limits_from_env(), clock=clock, bindings=bindings, store=store, sealer=sealer),
    )
    server = EgressServer(
        proxy,
        StaticBearerAuth(os.environ["EGRESS_CONTROL_TOKEN"]),
        instance=instance,
        authority=EgressAuthority.generate(clock) if sealed is not None else None,
    )
    _ = await server.start(
        host="0.0.0.0",
        data_port=EGRESS_PROXY_PORT,
        control_port=EGRESS_CONTROL_PORT,
        control_host=control_hosts,
    )
    stopped = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(signum, stopped.set)
    try:
        _ = await stopped.wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
