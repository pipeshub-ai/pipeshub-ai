"""The egress proxy as the real lane runs it in its ``pxh-`` container: P5.4's core with Node-issued tokens.

``app.egress_proxy_main`` reads its configuration from the configuration
service; this entry takes the same pieces from the environment so the test
stack can point the proxy at its fake Node token route. Everything else is
production code: ``EgressProxy``, ``EgressServer``, ``NodeGatewayTokenSource``.
"""


from __future__ import annotations

import os

# WP-63: the test authority the rig's sites are verified with, set before aiohttp builds its default TLS context.
if os.getenv("EGRESS_TEST_UPSTREAM_CA"):
    with open("/tmp/upstream-ca.pem", "w", encoding="ascii") as _out:
        _ = _out.write(os.environ["EGRESS_TEST_UPSTREAM_CA"])
    os.environ["SSL_CERT_FILE"] = "/tmp/upstream-ca.pem"

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from app.harness.gateway.tokens import NodeGatewayTokens, NodeGatewayTokenSource
from app.harness.sandbox.egress_state import RedisEgressStateStore
from harness_core.clock import SystemClock
from harness_core.ids import RandomIds
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import server as egress_server
from harness_core.sandbox.egress.__main__ import parse_upstreams
from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
from harness_core.sandbox.egress.control import control_bind_addresses_from_env
from harness_core.sandbox.egress.interception import EgressAuthority
from harness_core.sandbox.egress.proxy import EgressProxy, ProxyLimits
from harness_core.sandbox.egress.resolver import StaticResolver, SystemResolver
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.state import DurableEgressBindings, StateSealer
from harness_core.sandbox.egress.types import EGRESS_CONTROL_PORT, EGRESS_PROXY_PORT
from harness_core.sandbox.egress.upstream_proxy import upstream_egress_from_env

if TYPE_CHECKING:
    from harness_core.sandbox.egress.ports import TokenSource


class _Credential:
    async def worker_credential(self) -> str:
        return os.environ["WORKER_CREDENTIAL"]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    silence = os.getenv("EGRESS_TEST_UPSTREAM_READ_TIMEOUT_S")
    if silence:
        # WP127-FU-1: the silence cut scaled down, so a lane can hold a model silent past it in seconds, not 300.
        egress_server.UPSTREAM_READ_TIMEOUT_S = float(silence)
    clock, ids = SystemClock(), RandomIds()
    tokens: TokenSource = NodeGatewayTokenSource(
        NodeGatewayTokens(base_url=os.environ["NODE_URL"], credentials=_Credential())
    )
    sealing = os.getenv("EGRESS_TEST_SEALING_KEY")
    if sealing:
        # Sealed routes (a repository's token, GIT-FLOW), opened as app.egress_proxy_main opens them with SECRET_KEY.
        tokens = RoutedTokenSource(tokens, SealedTokenSource(CredentialSealer(sealing.encode()), clock))
    instance = ids.new_id("egress")
    bindings = InMemoryEgressBindings()
    redis_url = os.getenv("EGRESS_TEST_REDIS_URL")
    if redis_url:
        # P5.4b: the role's durable state, over the production Redis adapter (loaded before serving).
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]
        sealer = StateSealer(os.environ["EGRESS_TEST_STATE_KEY"].encode())
        durable = DurableEgressBindings(RedisEgressStateStore(client, namespace="pxh-e0:"), sealer, clock)
        instance = (await durable.load(instance)).instance
        bindings = durable
    # WP-63: allowed websites on the lane. Names the rig serves resolve from EGRESS_TEST_HOSTS (no DNS on the rig),
    # the sites' test authority is the one TLS upstreams are verified against, and the proxy ends sandboxes' TLS as
    # app.egress_proxy_main does (intercepts on, one authority, EGRESS_PRIVATE_ALLOW read by upstream_egress_from_env).
    hosts = json.loads(os.getenv("EGRESS_TEST_HOSTS") or "{}")
    intercepts = os.getenv("EGRESS_TEST_INTERCEPT") == "1"
    resolver = StaticResolver(hosts) if hosts else SystemResolver()
    proxy = EgressProxy(
        bindings=bindings,
        tokens=tokens,
        resolver=resolver,
        upstreams=parse_upstreams(os.getenv("EGRESS_UPSTREAMS")),
        clock=clock,
        ids=ids,
        limits=ProxyLimits(intercepts=intercepts, connect_tunnels=os.getenv("EGRESS_CONNECT_TUNNELS") == "true"),
        upstream_egress=upstream_egress_from_env(),
        private_resolver=resolver,
    )
    server = EgressServer(
        proxy,
        StaticBearerAuth(os.environ["EGRESS_CONTROL_TOKEN"]),
        instance=instance,
        authority=EgressAuthority.generate(clock) if intercepts else None,
    )
    _ = await server.start(
        host="0.0.0.0",
        data_port=EGRESS_PROXY_PORT,
        control_port=EGRESS_CONTROL_PORT,
        control_host=control_bind_addresses_from_env(),
    )
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
