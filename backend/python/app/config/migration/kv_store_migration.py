import os
from dataclasses import dataclass, field
from typing import List, Optional

import redis.asyncio as aioredis  # type: ignore

from app.utils.logger import create_logger

logger = create_logger("kv_store_migration")


@dataclass
class EtcdConfig:
    """Configuration for etcd connection."""

    host: str
    port: int
    timeout: float = 5.0
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class RedisConfig:
    """Configuration for Redis connection."""

    host: str
    port: int
    password: Optional[str] = None
    db: int = 0
    key_prefix: str = "kv:"
    timeout: float = 10.0


@dataclass
class MigrationConfig:
    """Configuration for migration."""

    etcd: EtcdConfig
    redis: RedisConfig


@dataclass
class MigrationResult:
    """Result of migration operation."""

    success: bool = False
    migrated_keys: List[str] = field(default_factory=list)
    failed_keys: List[str] = field(default_factory=list)
    skipped_keys: List[str] = field(default_factory=list)
    error: Optional[str] = None


class KVStoreMigrationService:
    """
    Service to migrate configuration data from etcd to Redis.
    This is used when transitioning from etcd to Redis as the KV store backend.
    """

    def __init__(self, config: MigrationConfig) -> None:
        self.config = config
        self._etcd_client = None
        self._redis_client = None

    async def is_etcd_available(self) -> bool:
        """Check if etcd is available and has data."""
        try:
            import etcd3  # type: ignore

            client = etcd3.client(
                host=self.config.etcd.host,
                port=self.config.etcd.port,
                timeout=self.config.etcd.timeout,
            )

            # Try to get status
            status = client.status()
            client.close()

            logger.info("etcd is available. Version: %s", status.version)
            return True
        except Exception as e:
            logger.warning("etcd is not available: %s", str(e))
            return False

    async def has_redis_data(self) -> bool:
        """Check if Redis already has configuration data."""
        try:
            redis_client = aioredis.Redis(
                host=self.config.redis.host,
                port=self.config.redis.port,
                password=self.config.redis.password,
                db=self.config.redis.db,
                socket_connect_timeout=self.config.redis.timeout,
            )

            pattern = f"{self.config.redis.key_prefix}*"
            keys = []

            async for key in redis_client.scan_iter(match=pattern, count=10):
                keys.append(key)
                if len(keys) > 0:
                    break  # Just need to check if any keys exist

            await redis_client.close()

            has_data = len(keys) > 0
            logger.info(
                "Redis %s configuration data",
                "has" if has_data else "does not have",
            )
            return has_data
        except Exception as e:
            logger.error("Failed to check Redis data: %s", str(e))
            return False

    async def migrate(self) -> MigrationResult:
        """Migrate all data from etcd to Redis."""
        result = MigrationResult()

        try:
            logger.info("Starting etcd to Redis migration...")

            # Check if etcd is available
            etcd_available = await self.is_etcd_available()
            if not etcd_available:
                result.error = "etcd is not available. Cannot migrate data."
                logger.error(result.error)
                return result

            # Check if Redis already has data
            redis_has_data = await self.has_redis_data()
            if redis_has_data:
                logger.info(
                    "Redis already has configuration data. Skipping migration."
                )
                result.success = True
                result.skipped_keys.append("*")
                return result

            # Connect to both stores
            await self._connect()

            # Get all keys from etcd

            all_keys_values = list(self._etcd_client.get_all())
            logger.info("Found %d keys in etcd", len(all_keys_values))

            # Migrate each key
            for value, metadata in all_keys_values:
                key = metadata.key.decode("utf-8") if metadata.key else None
                if not key:
                    continue

                try:
                    if value is not None:
                        # Store in Redis with the same key (preserving encryption)
                        full_key = f"{self.config.redis.key_prefix}{key}"
                        await self._redis_client.set(full_key, value)

                        result.migrated_keys.append(key)
                        logger.debug("Migrated key: %s", key)
                    else:
                        result.skipped_keys.append(key)
                        logger.debug("Skipped key (null value): %s", key)
                except Exception as key_error:
                    result.failed_keys.append(key)
                    logger.error("Failed to migrate key %s: %s", key, str(key_error))

            result.success = len(result.failed_keys) == 0
            logger.info(
                "Migration completed. Migrated: %d, Failed: %d, Skipped: %d",
                len(result.migrated_keys),
                len(result.failed_keys),
                len(result.skipped_keys),
            )

            return result
        except Exception as e:
            result.error = str(e)
            logger.error("Migration failed: %s", str(e))
            return result
        finally:
            await self._disconnect()

    async def _connect(self) -> None:
        """Connect to both etcd and Redis."""
        import etcd3  # type: ignore

        # Connect to etcd (synchronous client)
        self._etcd_client = etcd3.client(
            host=self.config.etcd.host,
            port=self.config.etcd.port,
            timeout=self.config.etcd.timeout,
        )

        # Connect to Redis (async client)
        self._redis_client = aioredis.Redis(
            host=self.config.redis.host,
            port=self.config.redis.port,
            password=self.config.redis.password,
            db=self.config.redis.db,
            socket_connect_timeout=self.config.redis.timeout,
        )

    async def _disconnect(self) -> None:
        """Disconnect from both stores."""
        if self._etcd_client:
            self._etcd_client.close()
            self._etcd_client = None

        if self._redis_client:
            await self._redis_client.close()
            self._redis_client = None


async def check_and_migrate_if_needed(
    config: Optional[MigrationConfig] = None,
) -> Optional[MigrationResult]:
    """
    Check if migration is needed and perform it if necessary.
    This should be called during application startup when using Redis as KV store.

    Args:
        config: Migration configuration. If None, uses environment variables.

    Returns:
        MigrationResult if migration was attempted, None if no migration needed.
    """
    # Build config from environment if not provided
    if config is None:
        etcd_url = os.getenv("ETCD_URL", "http://localhost:2379")
        if "://" in etcd_url:
            etcd_url = etcd_url.split("://")[1]
        etcd_parts = etcd_url.split(":")
        etcd_host = etcd_parts[0]
        etcd_port = int(etcd_parts[1]) if len(etcd_parts) > 1 else 2379

        config = MigrationConfig(
            etcd=EtcdConfig(
                host=etcd_host,
                port=etcd_port,
                timeout=float(os.getenv("ETCD_TIMEOUT", "5.0")),
            ),
            redis=RedisConfig(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                db=int(os.getenv("REDIS_DB", "0")),
                key_prefix=os.getenv("REDIS_KV_PREFIX", "kv:"),
            ),
        )

    migration_service = KVStoreMigrationService(config)

    # Check if Redis already has data
    redis_has_data = await migration_service.has_redis_data()
    if redis_has_data:
        logger.info("Redis already has configuration data. No migration needed.")
        return None

    # Check if etcd is available for migration
    etcd_available = await migration_service.is_etcd_available()
    if etcd_available:
        logger.info("etcd is available. Starting migration to Redis...")
        return await migration_service.migrate()

    # Neither Redis has data nor etcd is available - this is an error state
    logger.error(
        "CONFIGURATION ERROR: No configuration data found in Redis and etcd is not available. "
        "Please either: (1) Start etcd and run migration, or (2) Reconfigure the application."
    )

    return MigrationResult(
        success=False,
        error=(
            "Configuration data not found. etcd is not available for migration. "
            "Please reconfigure the application or restore etcd data."
        ),
    )
