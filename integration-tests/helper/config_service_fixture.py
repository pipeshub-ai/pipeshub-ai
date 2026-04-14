"""
ConfigurationService Fixture for Integration Tests

Provides a real ConfigurationService backed by Redis for integration tests.
"""
import json
import logging
import os
from typing import Callable

import pytest

# Set protobuf implementation to Python to avoid etcd3/protobuf version conflicts
# This is only needed because backend has etcd3 dependency even though we only use Redis
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from app.config.configuration_service import ConfigurationService
from app.config.providers.redis.redis_store import RedisDistributedKeyValueStore

logger = logging.getLogger("test-config-service")


def create_test_config_service() -> ConfigurationService:
    """
    Create a ConfigurationService for integration tests using Redis.
    
    Requirements:
    - SECRET_KEY environment variable must be set
    - Redis must be running (REDIS_HOST, REDIS_PORT)
    - KV_STORE_TYPE should be set to 'redis'
    
    Returns:
        ConfigurationService instance backed by Redis
        
    Raises:
        ValueError: If required environment variables are not set
        ConnectionError: If Redis connection fails
    """
    # Verify required environment variables
    if not os.getenv("SECRET_KEY"):
        raise ValueError("SECRET_KEY environment variable is required for ConfigurationService")
    
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_password = os.getenv("REDIS_PASSWORD", "")
    redis_db = int(os.getenv("REDIS_DB", "0"))
    
    logger.info(f"Creating ConfigurationService with Redis at {redis_host}:{redis_port}")
    
    # Create serializer/deserializer for Redis store
    def serialize(value) -> bytes:
        """Serialize value to bytes for Redis storage."""
        if value is None:
            return b""
        return json.dumps(value, default=str).encode("utf-8")
    
    def deserialize(value: bytes):
        """Deserialize bytes from Redis storage."""
        if not value:
            return None
        try:
            decoded = value.decode("utf-8")
            return json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.error(f"Failed to deserialize value: {e}")
            return None
    
    # Create Redis-backed KeyValueStore
    redis_store = RedisDistributedKeyValueStore(
        serializer=serialize,
        deserializer=deserialize,
        host=redis_host,
        port=redis_port,
        password=redis_password if redis_password else None,
        db=redis_db,
        key_prefix="pipeshub:test:kv:",  # Use test-specific prefix to avoid conflicts
    )
    
    # Create ConfigurationService
    config_service = ConfigurationService(
        logger=logger,
        key_value_store=redis_store
    )
    
    logger.info("ConfigurationService created successfully")
    return config_service


@pytest.fixture(scope="session")
def config_service():
    """
    Session-scoped ConfigurationService for integration tests.
    
    This fixture provides a real ConfigurationService backed by Redis.
    It will be reused across all tests in the session.
    
    The ConfigurationService will use environment variable fallback if
    keys are not found in Redis (via _get_env_fallback method).
    
    Required environment variables:
    - SECRET_KEY: For encryption
    - REDIS_HOST: Redis server host (default: localhost)
    - REDIS_PORT: Redis server port (default: 6379)
    - REDIS_PASSWORD: Redis password (optional)
    
    For ArangoDB provider:
    - ARANGO_URL, ARANGO_USERNAME, ARANGO_PASSWORD, ARANGO_DB_NAME
    
    For Neo4j provider:
    - NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE
    """
    try:
        service = create_test_config_service()
        yield service
    except ValueError as e:
        pytest.skip(f"ConfigurationService requirements not met: {e}")
    except Exception as e:
        pytest.fail(f"Failed to create ConfigurationService: {e}")
