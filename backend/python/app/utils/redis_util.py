import os
import ssl
import socket
from dataclasses import dataclass
from typing import List, Optional, Union

from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster, ClusterNode
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff


# Default Redis DB
DEFAULT_REDIS_DB = 0

def address_remap(address) -> tuple[str, int]:
    """Custom DNS resolution callback for debugging"""
    print(f"🔍 Resolving DNS for {address}")
    try:
        # address is a tuple (host, port)
        host, port = address

        # Resolve hostname to IP addresses
        results = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if results:
            ip = results[0][4][0]
            print(f"✅ Resolved {host} -> {ip}")
            return (ip, port)  # Return resolved IP with port
    except Exception as e:
        print(f"❌ DNS resolution failed for {address}: {e}")

    return address  # Fallback to original

@dataclass
class RedisConnectionConfig:
    """Configuration for Redis connection (standalone or cluster)."""
    host: str = 'localhost'
    port: int = 6379
    username: Optional[str] = None
    password: Optional[str] = None
    db: int = 0
    tls: bool = True
    skip_full_coverage_check: bool = True  # For MemoryDB/ElastiCache
    cluster_mode: bool = False
    cluster_nodes: Optional[List[dict]] = None  # List of {"host": str, "port": int}
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 10.0
    decode_responses: bool = True
    encoding: str = "utf-8"
    max_retries: int = 3
    retry_on_timeout: bool = True


def get_redis_config_from_env() -> dict:
    """
    Read Redis configuration directly from environment variables.
    
    Environment variables:
        - REDIS_HOST: Redis host (default: localhost)
        - REDIS_PORT: Redis port (default: 6379)
        - REDIS_USERNAME: Redis username (optional)
        - REDIS_PASSWORD: Redis password (optional)
        - REDIS_DB: Redis database number (default: 0)
        - REDIS_CLUSTER_MODE: Enable cluster mode (default: false)
        - REDIS_TLS: Enable TLS (default: false)
        - REDIS_TIMEOUT: Socket timeout in ms (default: 10000)
    
    Returns:
        dict: Redis configuration dictionary
    """
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD", "")
    redis_username = os.getenv("REDIS_USERNAME", "")
    redis_db = int(os.getenv("REDIS_DB", "0"))
    cluster_mode = os.getenv("REDIS_CLUSTER_MODE", "").lower() == "true"
    tls = os.getenv("REDIS_TLS", "").lower() == "true"
    timeout_ms = int(os.getenv("REDIS_TIMEOUT", "10000"))
    
    return {
        "host": redis_host,
        "port": redis_port,
        "password": redis_password if redis_password and redis_password.strip() else None,
        "username": redis_username if redis_username and redis_username.strip() else None,
        "db": redis_db,
        "clusterMode": cluster_mode,
        "tls": tls,
        "skipFullCoverageCheck": True,  # Required for AWS MemoryDB/ElastiCache
        "socketTimeout": timeout_ms / 1000,  # Convert to seconds
        "socketConnectTimeout": timeout_ms / 1000,
    }


def parse_redis_config(redis_config: dict) -> RedisConnectionConfig:
    """Parse Redis configuration dictionary into RedisConnectionConfig."""
    return RedisConnectionConfig(
        host=redis_config.get('host', 'localhost'),
        port=redis_config.get('port', 6379),
        username=redis_config.get('username'),
        password=redis_config.get('password'),
        db=redis_config.get('db', DEFAULT_REDIS_DB),
        tls=redis_config.get('tls', False),
        skip_full_coverage_check=redis_config.get('skipFullCoverageCheck', True),
        cluster_mode=redis_config.get('clusterMode', False),
        cluster_nodes=redis_config.get('clusterNodes'),
        socket_timeout=redis_config.get('socketTimeout', 5.0),
        socket_connect_timeout=redis_config.get('socketConnectTimeout', 10.0),
        decode_responses=redis_config.get('decodeResponses', True),
        encoding=redis_config.get('encoding', 'utf-8'),
        max_retries=redis_config.get('maxRetries', 3),
        retry_on_timeout=redis_config.get('retryOnTimeout', True),
    )


def build_redis_url(redis_config: dict) -> str:
    """Builds a Redis connection URL from a configuration dictionary."""
    host = redis_config.get('host', 'localhost')
    port = redis_config.get('port', 6379)
    username = redis_config.get('username')
    password = redis_config.get('password')
    db = redis_config.get('db', DEFAULT_REDIS_DB)
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


def _create_ssl_context(verify_ssl: bool = True) -> ssl.SSLContext:
    """
    Create SSL context for Redis TLS connections.
    
    Args:
        verify_ssl: Whether to verify SSL certificates. 
                   Set to False for AWS MemoryDB/ElastiCache if having cert issues.
    
    Returns:
        Configured SSL context.
    """
    if verify_ssl:
        # Standard SSL with certificate verification
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
    else:
        # Permissive SSL (like ioredis `tls: {}`)
        # Useful for AWS MemoryDB/ElastiCache
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
    
    return ssl_context


async def create_redis_client(
    redis_config: dict,
    decode_responses: bool = True,
    encoding: str = "utf-8",
) -> Union[Redis, RedisCluster]:
    """
    Create a Redis client based on configuration.
    
    Supports both standalone Redis and Redis Cluster modes.
    Works with AWS MemoryDB and ElastiCache.
    
    Args:
        redis_config: Configuration dictionary containing Redis connection details.
            Required fields:
                - host: Redis host
                - port: Redis port (default: 6379)
            Optional fields:
                - username: ACL username (default: 'default' for MemoryDB)
                - password: Redis password
                - tls: Enable TLS (default: False)
                - clusterMode: Enable cluster mode (default: False)
                - clusterNodes: List of cluster nodes [{"host": str, "port": int}]
                - skipFullCoverageCheck: Skip slot coverage check (default: True, needed for MemoryDB)
                - socketTimeout: Socket timeout in seconds (default: 5.0)
                - socketConnectTimeout: Connection timeout in seconds (default: 10.0)
                - maxRetries: Max retries per request (default: 3)
                - retryOnTimeout: Retry on timeout (default: True)
        decode_responses: Whether to decode responses to strings.
        encoding: Character encoding to use.
        
    Returns:
        Redis or RedisCluster client instance.
    """
    config = parse_redis_config(redis_config)
    cluster_mode = config.cluster_mode
    
    print("redis config is ", config)
    # Build SSL context if TLS is enabled
    # Use permissive SSL for AWS services (MemoryDB/ElastiCache)
    ssl_context = None
    if config.tls:
        # For AWS MemoryDB/ElastiCache, we typically don't need strict verification
        # as we're inside the VPC and using AWS's TLS termination
        ssl_context = _create_ssl_context(verify_ssl=False)
    
    # Configure retry with exponential backoff
    retry = Retry(
        ExponentialBackoff(cap=10, base=0.5),
        retries=config.max_retries,
    )
    
    if cluster_mode:
        # Redis Cluster mode (MemoryDB, ElastiCache Cluster)
        startup_nodes = []
        if config.cluster_nodes:
            for node in config.cluster_nodes:
                startup_nodes.append(ClusterNode(
                    host=node.get("host", config.host),
                    port=node.get("port", config.port),
                ))
        else:
            print("Adding startup_nodes from config", config)
            startup_nodes.append(ClusterNode(
                host=config.host,
                port=config.port,
            )) 
            print("Added startup_nodes", startup_nodes)
        
        # Get username - default to 'default' for MemoryDB ACL
        username = config.username
        if not username or not username.strip():
            username = "default"  # MemoryDB default ACL username
        
        # Create cluster client with AWS MemoryDB/ElastiCache compatible settings
        # Note: RedisCluster doesn't accept ssl_context directly, just ssl=True/False
        print ("creating redis client")
        client = RedisCluster(
            startup_nodes=startup_nodes,
            password=config.password if config.password and config.password.strip() else None,
            username=username,
            decode_responses=decode_responses,
            ssl=True,
            socket_timeout=config.socket_timeout,
            socket_connect_timeout=config.socket_connect_timeout,
            retry=retry,
            address_remap=address_remap,
        )
        
        print ("returning redis client")
        return client
    else:
        # Standalone Redis mode
        print("Building redis url")
        redis_url = build_redis_url(redis_config)
        print("Built redis url", redis_url)
        
        # Build connection kwargs
        connection_kwargs = {
            "encoding": encoding,
            "decode_responses": decode_responses,
            "socket_timeout": config.socket_timeout,
            "socket_connect_timeout": config.socket_connect_timeout,
            "retry": retry,
            "retry_on_timeout": config.retry_on_timeout,
        }
        
        # Add SSL context if TLS is enabled
        if config.tls:
            connection_kwargs["ssl_context"] = ssl_context
        
        client = Redis.from_url(redis_url, **connection_kwargs)
        
        return client


def is_cluster_mode(redis_config: dict) -> bool:
    """Check if Redis configuration specifies cluster mode."""
    return redis_config.get('clusterMode', False)

