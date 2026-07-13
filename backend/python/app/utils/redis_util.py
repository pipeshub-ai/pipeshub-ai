from typing import Any, AsyncIterator, List, Optional, Tuple, Union

import redis.asyncio as redis  # type: ignore
from redis.asyncio.cluster import ClusterNode, RedisCluster  # type: ignore

from app.config.constants.service import RedisConfig

RedisClient = Union[redis.Redis, RedisCluster]


def build_redis_url(redis_config: dict) -> str:
    """Builds a Redis connection URL from a configuration dictionary."""
    host = redis_config.get('host', 'localhost')
    port = redis_config.get('port', 6379)
    username = redis_config.get('username')
    password = redis_config.get('password')
    db = redis_config.get('db', RedisConfig.REDIS_DB.value)
    tls = redis_config.get('tls', False)

    # Use rediss:// scheme for TLS (equivalent to redis-cli --tls)
    scheme = "rediss" if tls else "redis"

    # Build auth part of URL
    auth_part = ""
    if username and username.strip():
        auth_part = username
        if password and password.strip():
            auth_part += f":{password}"
    elif password and password.strip():
        auth_part = f":{password}"

    if auth_part:
        auth_part += "@"

    return f"{scheme}://{auth_part}{host}:{port}/{db}"


def _parse_port(port_str: str, entry: str) -> int:
    """Parse a port string with an empty-default and a descriptive error.

    `port_str or 6379` covers malformed inputs like "host:" (trailing colon,
    empty port). A non-numeric port raises a clear startup error naming the
    offending entry, mirroring the Node.js parser.
    """
    try:
        return int(port_str or 6379)
    except (TypeError, ValueError):
        raise ValueError(
            f"REDIS_NODES entry has non-numeric port: '{entry}'"
        )


def parse_redis_nodes(raw: str | None) -> List[Tuple[str, int]]:
    """Parse REDIS_NODES (comma-separated host:port) into a list of (host, port) tuples."""
    if not raw:
        return []
    nodes: List[Tuple[str, int]] = []
    for entry in raw.split(','):
        entry = entry.strip()
        if not entry:
            continue
        if ':' in entry:
            host, port_str = entry.rsplit(':', 1)
            nodes.append((host, _parse_port(port_str, entry)))
        else:
            nodes.append((entry, 6379))
    return nodes


def _resolve_nodes(redis_config: dict) -> List[Tuple[str, int]]:
    """Extract cluster nodes from a config dict — accepts a `str` (REDIS_NODES
    raw env), a list of dicts ({host, port}), or a list of (host, port) tuples."""
    raw = redis_config.get('nodes')
    if isinstance(raw, str):
        return parse_redis_nodes(raw)
    if isinstance(raw, list):
        parsed: List[Tuple[str, int]] = []
        for item in raw:
            if isinstance(item, dict):
                host = item.get('host')
                if not host:
                    continue
                # `or 6379` guards against a null/empty port from the config
                # source (int(None) would raise TypeError).
                parsed.append((host, int(item.get('port') or 6379)))
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                parsed.append((str(item[0]), int(item[1] or 6379)))
        return parsed
    return []


def _as_config_dict(redis_config: Any) -> dict:
    """Normalize a config into a plain dict.

    Accepts a dict (returned as-is), a pydantic model (`.model_dump()`), or any
    object with `__dict__`. Lets callers pass a `RedisStreamsConfig`/`RedisConfig`
    model straight through instead of repeating the
    `model_dump() if hasattr(...) else __dict__` boilerplate at every call site.
    """
    if isinstance(redis_config, dict):
        return redis_config
    if hasattr(redis_config, "model_dump"):
        return redis_config.model_dump()
    return dict(getattr(redis_config, "__dict__", {}) or {})


def is_cluster_mode(redis_config: Any) -> bool:
    return str(_as_config_dict(redis_config).get('mode', 'standalone')).lower() == 'cluster'


def build_redis_client(
    redis_config: Any,
    *,
    decode_responses: bool = False,
    socket_connect_timeout: Optional[float] = None,
    socket_timeout: Optional[float] = None,
    retry: Any = None,
    retry_on_error: Any = None,
    health_check_interval: Optional[int] = None,
    single_connection_client: bool = False,
) -> RedisClient:
    """Build an async Redis client honoring REDIS_MODE.

    `redis_config` may be a plain dict OR a pydantic model (e.g.
    RedisStreamsConfig) — it is normalized via `_as_config_dict`.

    Returns `redis.asyncio.Redis` for standalone mode and
    `redis.asyncio.cluster.RedisCluster` for cluster mode. Callers should treat
    the return as the union type — both expose the command surface we use
    (get/set/del/scan_iter/pubsub/xadd/xreadgroup/etc.). For SCAN that must be
    cluster-correct, use `cluster_aware_scan_iter` rather than `scan_iter`
    directly.
    """
    redis_config = _as_config_dict(redis_config)
    cluster = is_cluster_mode(redis_config)
    common: dict = {
        "password": redis_config.get("password"),
        "decode_responses": decode_responses,
    }
    # Redis 6+ ACL users need both username and password; only set username
    # when it's actually configured to avoid sending a stray "username=None"
    # to a server that authenticates the default user with just a password.
    username = redis_config.get("username")
    if username:
        common["username"] = username
    if socket_connect_timeout is not None:
        common["socket_connect_timeout"] = socket_connect_timeout
    if socket_timeout is not None:
        common["socket_timeout"] = socket_timeout
    if retry is not None:
        common["retry"] = retry
    if retry_on_error is not None:
        common["retry_on_error"] = retry_on_error
    if health_check_interval is not None:
        common["health_check_interval"] = health_check_interval
    if redis_config.get("tls"):
        common["ssl"] = True

    if cluster:
        nodes = _resolve_nodes(redis_config)
        if not nodes:
            raise ValueError(
                "REDIS_MODE=cluster requires REDIS_NODES "
                "(comma-separated host:port list)."
            )
        startup_nodes = [ClusterNode(host, port) for host, port in nodes]
        return RedisCluster(startup_nodes=startup_nodes, **common)

    standalone_kwargs = {
        "host": redis_config.get("host", "localhost"),
        "port": int(redis_config.get("port", 6379)),
        "db": int(redis_config.get("db", RedisConfig.REDIS_DB.value)),
        **common,
    }
    if single_connection_client:
        standalone_kwargs["single_connection_client"] = True
    return redis.Redis(**standalone_kwargs)


async def cluster_aware_publish(
    client: RedisClient,
    channel: str,
    message: Any,
) -> int:
    """Publish a message that reaches subscribers on any node.

    redis-py 5.2.1's async `RedisCluster` does NOT expose a `.publish()`
    method — direct calls raise AttributeError. We fall through to
    `execute_command('PUBLISH', ...)`, which the cluster client routes to a
    primary; that primary then propagates the message across the cluster bus
    so subscribers connected to any node receive it. Standalone clients use
    the regular `.publish()` method.
    """
    if isinstance(client, RedisCluster):
        return await client.execute_command("PUBLISH", channel, message)
    return await client.publish(channel, message)  # type: ignore[union-attr]


def build_pubsub_subscriber(redis_config: Any, **kwargs: Any) -> redis.Redis:
    """Build a *standalone* async Redis client for SUBSCRIBE.

    `redis_config` may be a dict or a pydantic model (normalized internally).

    In cluster mode, async `RedisCluster` does not expose `.pubsub()`. The
    workaround is to connect a regular `redis.asyncio.Redis` to any single
    cluster node — Redis cluster bus propagates PUBLISH across all nodes so
    a subscriber on any one of them sees the message. In standalone mode this
    just builds the usual client.
    """
    redis_config = _as_config_dict(redis_config)
    if is_cluster_mode(redis_config):
        nodes = _resolve_nodes(redis_config)
        if not nodes:
            raise ValueError(
                "REDIS_MODE=cluster requires REDIS_NODES "
                "(comma-separated host:port list)."
            )
        host, port = nodes[0]
        sub_kwargs: dict = {"host": host, "port": port, **kwargs}
        password = redis_config.get("password")
        if password:
            sub_kwargs["password"] = password
        username = redis_config.get("username")
        if username:
            sub_kwargs["username"] = username
        if redis_config.get("tls"):
            sub_kwargs["ssl"] = True
        return redis.Redis(**sub_kwargs)
    # Standalone path: reuse the regular factory.
    client = build_redis_client(redis_config, **kwargs)
    assert isinstance(client, redis.Redis)
    return client


async def cluster_aware_scan_iter(
    client: RedisClient,
    match: Optional[str] = None,
    count: int = 100,
) -> AsyncIterator[Any]:
    """Async generator over keys matching `match`. On cluster clients we
    explicitly target all primaries so partial-shard scans cannot regress
    silently across redis-py versions."""
    if isinstance(client, RedisCluster):
        # redis-py 5.x: RedisCluster.scan_iter has a `target_nodes` kwarg.
        # Default behaviour varies across patch releases — pin to PRIMARIES.
        async for key in client.scan_iter(  # type: ignore[attr-defined]
            match=match, count=count, target_nodes=RedisCluster.PRIMARIES
        ):
            yield key
        return
    async for key in client.scan_iter(match=match, count=count):  # type: ignore[attr-defined]
        yield key
