"""PUBLISH through the cluster provider reaches a subscriber on a real cluster.

``ClusterRedisProvider.publish`` sends PUBLISH with ``execute_command`` to the
default node because redis-py 5.x's async ``RedisCluster`` had no ``publish()``
(6+ does). Whichever redis-py is installed, a message published through the
provider must arrive on the dedicated pub/sub client.
"""
from __future__ import annotations

import asyncio

import pytest
from redis.asyncio.cluster import RedisCluster

from app.services.redis.connection_provider_factory import get_redis_provider

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _next_message(pubsub, timeout: float = 5.0) -> dict | None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if message is not None:
            return message
    return None


async def _publish_and_receive(config, channel: str, body: str) -> tuple[int, dict | None]:
    provider = get_redis_provider(config, mode="cluster")
    subscriber = provider.create_pubsub_client()
    pubsub = subscriber.pubsub()
    try:
        await pubsub.subscribe(channel)
        # The subscribe confirmation must arrive before publishing, or the
        # message can legitimately be missed.
        confirmation = await pubsub.get_message(timeout=5.0)
        assert confirmation is not None and confirmation["type"] == "subscribe"
        receivers = await provider.publish(channel, body)
        return receivers, await _next_message(pubsub)
    finally:
        await pubsub.aclose()
        await subscriber.aclose()


async def test_publish_reaches_a_subscriber_on_the_pubsub_client(
    redis_cluster_available, unique_suffix
) -> None:
    channel = f"it-pubsub-{unique_suffix}"
    receivers, message = await _publish_and_receive(
        redis_cluster_available, channel, "hello-cluster"
    )
    # PUBLISH counts subscribers on the node that ran it only; the subscriber
    # may sit on another node, so 0 is a valid count. Arrival is what matters.
    assert receivers >= 0
    assert message is not None, "the published message never arrived"
    assert message["channel"] in (channel, channel.encode())
    assert message["data"] in ("hello-cluster", b"hello-cluster")


async def test_publish_does_not_depend_on_the_clients_native_publish(
    redis_cluster_available, unique_suffix, monkeypatch
) -> None:
    """Older redis-py had no ``RedisCluster.publish``; the provider must work
    without it, so it is removed here rather than relied on."""
    redis_cluster_provider = get_redis_provider(redis_cluster_available, mode="cluster")
    # `publish` is inherited from a command mixin, so it cannot be deleted from
    # the subclass; a property that raises makes hasattr() report it missing.
    def _missing(self) -> None:
        raise AttributeError("publish")

    monkeypatch.setattr(RedisCluster, "publish", property(_missing), raising=False)
    assert not hasattr(redis_cluster_provider.get_client(), "publish")

    channel = f"it-pubsub-{unique_suffix}"
    receivers, message = await _publish_and_receive(
        redis_cluster_available, channel, "hello-without-native"
    )
    assert receivers >= 0
    assert message is not None, "the published message never arrived"
    assert message["data"] in ("hello-without-native", b"hello-without-native")
