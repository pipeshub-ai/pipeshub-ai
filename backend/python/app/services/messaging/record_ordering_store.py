"""Redis-backed per-record ordering state for indexing events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from app.services.messaging.redis_client import RedisClientRegistry

RECORD_ORDER_BIT_FIELD = "_record_order_bit"

if TYPE_CHECKING:
    from logging import Logger

    from app.services.messaging.config import RedisConfig
    from app.services.redis.connection_provider import RedisClient


_INITIALIZE_SCRIPT = """
redis.call('HSETNX', KEYS[1], 'bit', ARGV[1])
return redis.call('HGET', KEYS[1], 'bit')
"""

_FLIP_AFTER_FAILURE_SCRIPT = """
local current = redis.call('HGET', KEYS[1], 'bit')

if not current then
    return {-1, -2}
end

if current ~= '0' and current ~= '1' then
    return {-1, -3}
end

local failure_field = 'failure:' .. ARGV[2]
if redis.call('HEXISTS', KEYS[1], failure_field) == 1 then
    return {tonumber(current), 0}
end

if current ~= ARGV[1] then
    return {tonumber(current), -1}
end

local next_bit = 1 - tonumber(current)
redis.call('HSET', KEYS[1], 'bit', next_bit, failure_field, '1')
return {next_bit, 1}
"""


class RecordOrderingStore:
    """Stores a record's ordering bit and makes failure flips idempotent.

    Failure tokens are retained in the record's Redis hash so a repeated
    failure notification cannot flip the bit twice. This class deliberately
    does not expire ordering state; safe cleanup depends on knowing that no
    queued or in-flight event can still refer to the record.
    """

    KEY_PREFIX = "messaging:record-order"

    def __init__(
        self,
        logger: Logger,
        *,
        redis_client: RedisClient | None = None,
        redis_config: RedisConfig | None = None,
    ) -> None:
        if redis_client is None and redis_config is None:
            raise ValueError("Either redis_client or redis_config must be provided")

        self.logger = logger
        self._redis = redis_client
        self._redis_config = redis_config
        self._registry: RedisClientRegistry | None = None
        self._key_namespace = ""

    async def initialize(self) -> None:
        """Initialize loop-bound Redis clients and verify connectivity."""
        if self._redis is not None or self._registry is not None:
            return

        if self._redis_config is None:
            raise RuntimeError("Redis config is not available")

        from app.services.messaging.config import messaging_env

        self._registry = RedisClientRegistry(
            self.logger,
            self._redis_config,
            max_connections=messaging_env.concurrency_redis_max_connections,
            socket_timeout_seconds=messaging_env.concurrency_redis_timeout_seconds,
        )
        await self._registry.client().ping()
        self._key_namespace = self._registry.provider.key_namespace
        self.logger.info("RecordOrderingStore: Redis connection initialized")

    async def cleanup(self) -> None:
        """Close Redis clients owned by this store."""
        if self._registry is not None:
            registry = self._registry
            self._registry = None
            self._key_namespace = ""
            await registry.aclose()

    def _client(self) -> RedisClient:
        if self._redis is not None:
            return self._redis
        if self._registry is not None:
            return self._registry.client()
        raise RuntimeError("RecordOrderingStore is not initialized")

    def _build_key(self, record_id: str) -> str:
        if not record_id:
            raise ValueError("record_id must not be empty")

        namespace = f"{self._key_namespace}:" if self._key_namespace else ""
        return f"{namespace}{self.KEY_PREFIX}:{record_id}"

    @staticmethod
    def _parse_bit(value: object) -> bool:
        if isinstance(value, bytes):
            value = value.decode("ascii")

        if value == "0" or (type(value) is int and value == 0):
            return False
        if value == "1" or (type(value) is int and value == 1):
            return True

        raise ValueError(f"Invalid record ordering bit in Redis: {value!r}")

    async def get(self, record_id: str) -> bool | None:
        """Return the stored bit, or None if this record has no state yet."""
        value = await self._client().hget(self._build_key(record_id), "bit")
        if value is None:
            return None
        return self._parse_bit(value)

    async def get_or_initialize(
        self,
        record_id: str,
        initial_bit: bool = False,
    ) -> bool:
        """Return the stored bit, initializing it atomically if absent."""
        value = await self._client().eval(
            _INITIALIZE_SCRIPT,
            1,
            self._build_key(record_id),
            "1" if initial_bit else "0",
        )
        return self._parse_bit(value)

    async def defer_bit_if_mismatched(
        self, payload: dict[str, JsonValue]
    ) -> bool | None:
        """Return the current bit when the event is stale; seed legacy events."""
        record_id = payload.get("recordId")
        if not isinstance(record_id, str) or not record_id:
            return None

        current_bit = await self.get_or_initialize(record_id)
        if RECORD_ORDER_BIT_FIELD not in payload:
            payload[RECORD_ORDER_BIT_FIELD] = current_bit
            return None

        event_bit = payload[RECORD_ORDER_BIT_FIELD]
        if type(event_bit) is not bool:
            raise ValueError(
                f"{RECORD_ORDER_BIT_FIELD} must be a boolean, got {event_bit!r}"
            )
        return current_bit if event_bit != current_bit else None

    async def flip_event_after_failure(
        self, payload: dict[str, JsonValue], failure_token: str
    ) -> bool | None:
        """Flip a failed event's bit once and update it for its next delivery."""
        record_id = payload.get("recordId")
        if not isinstance(record_id, str) or not record_id:
            return None

        event_bit = payload.get(RECORD_ORDER_BIT_FIELD)
        if RECORD_ORDER_BIT_FIELD not in payload:
            event_bit = await self.get_or_initialize(record_id)
            payload[RECORD_ORDER_BIT_FIELD] = event_bit
        if type(event_bit) is not bool:
            raise ValueError(
                f"{RECORD_ORDER_BIT_FIELD} must be a boolean, got {event_bit!r}"
            )

        current_bit, _changed = await self.flip_after_failure(
            record_id,
            event_bit,
            failure_token,
        )
        payload[RECORD_ORDER_BIT_FIELD] = current_bit
        return current_bit

    async def flip_after_failure(
        self,
        record_id: str,
        expected_bit: bool,
        failure_token: str,
    ) -> tuple[bool, bool]:
        """Flip the stored bit once for a failure token.

        Returns ``(current_bit, changed)``. ``changed`` is False if this token
        was already applied or the stored bit no longer matches expected_bit.
        """
        if not failure_token:
            raise ValueError("failure_token must not be empty")

        result = await self._client().eval(
            _FLIP_AFTER_FAILURE_SCRIPT,
            1,
            self._build_key(record_id),
            "1" if expected_bit else "0",
            failure_token,
        )

        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise RuntimeError("Unexpected response from Redis ordering script")

        current_value, status_value = result
        status = int(status_value)

        if status == -2:
            raise RuntimeError(
                f"No ordering bit initialized for record {record_id!r}"
            )
        if status == -3:
            raise ValueError(
                f"Invalid ordering bit stored for record {record_id!r}"
            )
        if status not in (-1, 0, 1):
            raise RuntimeError(
                f"Unexpected status from Redis ordering script: {status}"
            )

        return self._parse_bit(current_value), status == 1