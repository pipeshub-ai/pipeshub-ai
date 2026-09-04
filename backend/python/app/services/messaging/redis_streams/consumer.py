import asyncio
import json
from logging import Logger
from typing import TYPE_CHECKING, Optional

from app.services.messaging.config import (
    MessageHandler,
    RedisStreamsConfig,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.error_classifier import MessageErrorClassifier, MessageErrorType
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.redis_streams.stream_read_planner import StreamReadPlanner
from app.services.distributed.interface import IRetryTracker
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider
from app.utils.request_context import (
    context_from_envelope,
    reset_context,
    set_context,
)

if TYPE_CHECKING:
    from app.services.redis.connection_provider import IRedisConnectionProvider, RedisClient as Redis

MAX_CONCURRENT_TASKS = 5

_BUSYGROUP_ERROR = "BUSYGROUP"
_MESSAGE_VALUE_FIELD = "value"


class RedisStreamsConsumer(IMessagingConsumer):
    """Redis Streams implementation of messaging consumer.

    Uses RetryManager for failure-based retry counting when provided;
    falls back to Redis native times_delivered when not. Pending messages
    (failed retries) are processed only when no new messages arrive
    (idle-based retry).
    """

    def __init__(
        self,
        logger: Logger,
        config: RedisStreamsConfig,
        retry_manager: Optional[IRetryTracker] = None,
        provider: "Optional[IRedisConnectionProvider]" = None,
    ) -> None:
        self.logger = logger
        self.config = config
        self.retry_manager = retry_manager
        # Constructor accepts an explicit provider so callers sharing one
        # connection provider process-wide can inject it (R11); otherwise
        # one is looked up/created from this config, so behaviour for
        # existing callers is unchanged.
        self._provider: "IRedisConnectionProvider" = provider or get_redis_provider(
            RedisConnectionConfig.from_redis_config(config)
        )
        self._planner = StreamReadPlanner(self._provider)
        self.redis: Optional[Redis] = None
        self.running = False
        self.consume_task: Optional[asyncio.Task] = None
        self.message_handler: Optional[MessageHandler] = None
        self._consecutive_empty_polls = 0
        self._idle_threshold = 3  # Drain pending after N consecutive empty polls

    async def initialize(self) -> None:
        try:
            # Dedicated (non-shared) client: XREADGROUP BLOCK holds the
            # connection for up to `block_ms`, which would starve any other
            # caller of a pooled/shared client.
            self.redis = self._provider.create_client(
                ClientOptions(
                    decode_responses=True,
                    blocking=True,
                    # One connection is parked in XREADGROUP BLOCK for up to
                    # `block_ms` at a time; XACK/XAUTOCLAIM run on the same
                    # client and must not queue behind it. Sized off the same
                    # knob the lease/retry pools use rather than ClientOptions'
                    # conservative default.
                    max_connections=messaging_env.concurrency_redis_max_connections,
                )
            )
            await self.redis.ping()

            for topic in self.config.topics:
                try:
                    await self.redis.xgroup_create(  # type: ignore
                        topic,
                        self.config.group_id,
                        # "0" replays everything retained. A disposable group is new on
                        # every process start, so that would re-deliver the whole stream
                        # each time; it only cares about what happens from now on.
                        id="$" if self.config.ephemeral_group else "0",
                        mkstream=True,
                    )
                    self.logger.info(
                        "Created consumer group %s for stream %s",
                        self.config.group_id,
                        topic,
                    )
                except Exception as e:
                    if _BUSYGROUP_ERROR in str(e):
                        self.logger.debug(
                            "Consumer group %s already exists for stream %s",
                            self.config.group_id,
                            topic,
                        )
                    else:
                        raise

            self.logger.info("Successfully initialized Redis Streams consumer")
        except Exception as e:
            self.logger.error("Failed to create consumer: %s", e)
            raise

    async def cleanup(self) -> None:
        try:
            if self.redis:
                await self._destroy_ephemeral_group()
                await self.redis.aclose()
                # Null it so a second call (stop() after the loop's own finally) is a
                # no-op rather than issuing commands on a closed client.
                self.redis = None
                self.logger.info("Redis Streams consumer stopped")
        except Exception as e:
            self.logger.error("Error during cleanup: %s", e)

    async def start(
        self,
        message_handler: MessageHandler,
    ) -> None:
        try:
            self.running = True
            self.message_handler = message_handler

            if not self.redis:
                await self.initialize()

            self.consume_task = asyncio.create_task(self._consume_loop())
            self.logger.info("Started Redis Streams consumer task")
        except Exception as e:
            self.logger.error("Failed to start Redis Streams consumer: %s", e)
            raise

    async def stop(
        self,
        message_handler: Optional[MessageHandler] = None,
    ) -> None:
        self.running = False

        if self.consume_task:
            self.consume_task.cancel()
            try:
                await self.consume_task
            except asyncio.CancelledError:
                pass

        await self.cleanup()

    async def _destroy_ephemeral_group(self) -> None:
        """Drop this process's disposable group; Redis never expires groups itself.

        Stable groups are left alone. Their consumer name is also left alone -- both
        because XGROUP DELCONSUMER would discard that consumer's pending entries (the
        group's last-delivered-id has already moved past them, so nothing would ever
        redeliver them) and because a stable name is what lets a restarted process
        recover its own unacked messages immediately, via `_drain_pending`'s phase 2,
        instead of waiting out claim_min_idle_ms for XAUTOCLAIM.
        """
        if not (self.config.ephemeral_group and self.redis):
            return
        for topic in self.config.topics:
            try:
                await self.redis.xgroup_destroy(topic, self.config.group_id)  # type: ignore
            except Exception as exc:
                self.logger.warning(
                    "Could not destroy consumer group %s on %s: %s",
                    self.config.group_id, topic, exc,
                )

    def is_running(self) -> bool:
        return self.running

    async def _clear_retry_tracking(self, message_id: str) -> None:
        if not self.retry_manager:
            return
        try:
            await self.retry_manager.clear(message_id)
        except Exception as e:
            self.logger.error(
                "Failed to clear retry tracking for %s: %s", message_id, e
            )

    async def _get_retry_count(self, message_id: str) -> int:
        if not self.retry_manager:
            return 0
        return await self.retry_manager.get_count(message_id)

    async def _increment_retry_and_check(
        self, message_id: str
    ) -> tuple[int, bool]:
        if not self.retry_manager:
            return 0, False
        return await self.retry_manager.increment_and_check(
            message_id, messaging_env.max_delivery_attempts
        )

    async def _should_dead_letter(self, topic: str, message_id: str) -> bool:
        """Check if message should be dead-lettered based on delivery count.

        Returns True (and ACKs the message) when the delivery count exceeds
        ``MAX_DELIVERY_ATTEMPTS``, effectively dead-lettering the message so
        it no longer blocks the PEL.
        """
        max_attempts = messaging_env.max_delivery_attempts

        # Use RetryManager for failure-based retry counting if available
        if self.retry_manager is not None:
            failure_count = await self._get_retry_count(message_id)
            if failure_count >= max_attempts:
                await self.redis.xack(topic, self.config.group_id, message_id)  # type: ignore
                await self._clear_retry_tracking(message_id)
                self.logger.warning(
                    "Dead-lettered %s after %d transient failures (max %d) via RetryManager",
                    message_id,
                    failure_count,
                    max_attempts,
                )
                return True
            return False

        # Fallback to Redis native times_delivered if no RetryManager
        try:
            # XPENDING <stream> <group> <start> <end> <count> returns
            # [(message_id, consumer, idle_ms, times_delivered), ...]
            details = await self.redis.xpending_range(  # type: ignore
                topic,
                self.config.group_id,
                min=message_id,
                max=message_id,
                count=1,
            )
            if details:
                times_delivered = details[0].get("times_delivered", 0)
                if times_delivered >= max_attempts:
                    await self.redis.xack(topic, self.config.group_id, message_id)  # type: ignore
                    self.logger.warning(
                        "Dead-lettered message %s on stream %s after %d delivery attempts (max %d) via native PEL",
                        message_id,
                        topic,
                        times_delivered,
                        max_attempts,
                    )
                    return True
        except Exception as e:
            self.logger.error(
                "Error checking delivery count for %s: %s",
                message_id,
                e,
            )
        return False

    async def _finalize_message(
        self, stream_name: str, message_id: str, success: bool, is_terminal: bool
    ) -> None:
        """Centralized logic to acknowledge or prepare message for retry.

        Args:
            stream_name: Name of the Redis stream
            message_id: Message ID
            success: True if message processed successfully
            is_terminal: True if error is terminal (should ACK immediately)
        """
        if success or is_terminal:
            # Success or terminal error: clear retry tracking and ACK
            if self.retry_manager:
                await self._clear_retry_tracking(message_id)
            await self.redis.xack(stream_name, self.config.group_id, message_id)  # type: ignore
            self.logger.info(
                "Acknowledged message %s on stream %s (success=%s, terminal=%s)",
                message_id,
                stream_name,
                success,
                is_terminal,
            )
        else:
            # Transient error
            if self.retry_manager:
                # Use RetryManager to track failures and check if should dead-letter
                failure_count, should_dead_letter = await self._increment_retry_and_check(message_id)
                if should_dead_letter:
                    await self.redis.xack(stream_name, self.config.group_id, message_id)  # type: ignore
                    await self._clear_retry_tracking(message_id)
                    self.logger.warning(
                        "Dead-lettered %s after %d transient failures (max %d) via RetryManager",
                        message_id,
                        failure_count,
                        messaging_env.max_delivery_attempts,
                    )
                else:
                    self.logger.warning(
                        "Transient error for %s, will retry (%d/%d) via RetryManager",
                        message_id,
                        failure_count,
                        messaging_env.max_delivery_attempts,
                    )
            else:
                # No RetryManager: leave message unacked for PEL-based retry
                self.logger.warning(
                    "Failed to process message %s, will retry via native PEL",
                    message_id,
                )

    async def _drain_pending(self) -> bool:
        """Re-process messages left in the Pending Entries List (PEL).

        Called when no new messages arrive (idle-based retry). Returns True
        if any pending messages were processed.

        Phase 1: XAUTOCLAIM to steal idle messages from other (crashed) consumers.
        Phase 2: XREADGROUP with id "0" to recover messages already owned by THIS
        consumer (e.g. delivered before a crash/restart but never ACK-ed).
        """
        processed_any = False

        for topic in self.config.topics:
            # Phase 1: claim idle messages from other (possibly crashed) consumers
            start_id = "0-0"
            while self.running:
                try:
                    result = await self.redis.xautoclaim(  # type: ignore
                        topic,
                        self.config.group_id,
                        self.config.client_id,
                        min_idle_time=self.config.claim_min_idle_ms,
                        start_id=start_id,
                        count=10,
                    )
                    next_id, claimed, _deleted = result
                    if not claimed:
                        break
                    for message_id, fields in claimed:
                        try:
                            if await self._should_dead_letter(topic, message_id):
                                processed_any = True
                                continue
                            processed_any = True
                            success, is_terminal = await self._process_message_with_classification(
                                topic, message_id, fields
                            )
                            await self._finalize_message(topic, message_id, success, is_terminal)
                        except Exception as e:
                            self.logger.error(
                                "Error recovering pending message %s: %s",
                                message_id,
                                e,
                            )
                    start_id = next_id
                    if next_id == b"0-0" or next_id == "0-0":
                        break
                except Exception as e:
                    self.logger.error("Error during XAUTOCLAIM on %s: %s", topic, e)
                    break

            # Phase 2: read messages already in THIS consumer's PEL
            last_pending_id = "0"
            while self.running:
                try:
                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.config.client_id,
                        streams={topic: last_pending_id},
                        count=self.config.batch_size,
                    )

                    if not results:
                        break

                    drained_any = False
                    for _stream_name, messages in results:
                        if not messages:
                            continue
                        for message_id, fields in messages:
                            drained_any = True
                            last_pending_id = message_id
                            try:
                                if await self._should_dead_letter(topic, message_id):
                                    processed_any = True
                                    continue
                                processed_any = True
                                success, is_terminal = await self._process_message_with_classification(
                                    topic, message_id, fields
                                )
                                await self._finalize_message(topic, message_id, success, is_terminal)
                            except Exception as e:
                                self.logger.error(
                                    "Error recovering own pending message %s: %s",
                                    message_id,
                                    e,
                                )

                    if not drained_any:
                        break
                except Exception as e:
                    self.logger.error(
                        "Error draining own PEL on %s: %s",
                        topic,
                        e,
                    )
                    break

        if processed_any:
            self.logger.info("Processed pending messages from PEL")
        return processed_any

    async def _read_new_messages(self) -> list:
        """One ``XREADGROUP`` per hash-slot group of subscribed topics (R1).

        On standalone every topic is in one group, so this is exactly the
        single call this replaced, with the full ``BLOCK`` budget. On a
        Redis Cluster / MemoryDB, topics can span slots (lane streams
        especially), so each group gets its own call with a fair share of
        the overall block budget -- an empty poll still costs at most
        ``block_ms`` total, not once per group.

        Each group's call is isolated with :func:`asyncio.wait_for` and its
        own try/except: while one slot's node is mid-failover/reconnect, a
        ``ClusterDownError`` (or a client that hangs retrying past its
        stated ``BLOCK`` budget) must not stop *other*, perfectly healthy
        groups from being polled -- seen live on a 3-master cluster where a
        restarted node's group blocked for tens of seconds while the other
        two groups' streams sat fully caught-up and unread. A group that
        fails alongside others that succeed does not lose those successful
        reads: once ``XREADGROUP >`` claims a message it will never be
        handed out again by ``>``, only by an own-PEL read, so silently
        discarding a mixed batch here would strand the successful groups'
        messages until the next idle-triggered `_drain_pending` pass (or
        forever, if new messages keep arriving often enough that the idle
        threshold is never reached). The error from the failing group is
        only re-raised -- so the caller's existing backoff kicks in -- when
        no group produced anything at all.
        """
        groups = self._planner.group(self.config.topics)
        if not groups:
            # Sleep, don't return straight into the caller's `continue`: with no
            # topics subscribed this path does no I/O at all, so returning
            # immediately spins the consume loop at 100% CPU. The inline
            # XREADGROUP this replaced blocked for `block_ms` in the same
            # state, and the Node consumer keeps an explicit idle sleep here.
            await asyncio.sleep(self.config.block_ms / 1000.0)
            return []

        per_group_block_ms = (
            self.config.block_ms
            if len(groups) == 1
            else max(1, self.config.block_ms // len(groups))
        )
        # Generous upper bound over the BLOCK budget so a genuinely wedged
        # node can't hold up other groups' turn in this poll; the group is
        # simply retried on the next call to `_read_new_messages`.
        per_group_timeout_seconds = (per_group_block_ms / 1000.0) + 5.0

        combined: list = []
        first_error: Optional[Exception] = None
        for group in groups:
            streams = dict.fromkeys(group, ">")
            try:
                read = self.redis.xreadgroup(  # type: ignore
                    groupname=self.config.group_id,
                    consumername=self.config.client_id,
                    streams=streams,
                    count=self.config.batch_size,
                    block=per_group_block_ms,
                )
                # The deadline exists only to stop one wedged slot starving the
                # *other* groups of their turn. With a single group -- every
                # standalone deployment, since key_slot() is 0 for all keys --
                # there is no other group to protect, and wrapping the call
                # only invents a failure: under a saturated event loop the
                # timeout fires on a perfectly healthy BLOCK, the read is
                # abandoned, and the caller busy-loops on the error path.
                results = (
                    await read
                    if len(groups) == 1
                    else await asyncio.wait_for(read, timeout=per_group_timeout_seconds)
                )
            except Exception as e:
                # `type(e).__name__` because the message alone is often empty:
                # `str(asyncio.TimeoutError())` is '', which logged as a bare
                # "XREADGROUP failed for slot group X (1 streams): " with
                # nothing to diagnose from.
                self.logger.warning(
                    "XREADGROUP failed for slot group %s (%d streams): %s: %s",
                    group[0],
                    len(group),
                    type(e).__name__,
                    e,
                )
                first_error = first_error or e
                continue
            if results:
                combined.extend(results)
        if first_error is not None and not combined:
            raise first_error
        return combined

    async def _consume_loop(self) -> None:
        """Main consumption loop with idle-based pending drain.

        New messages are processed first. When no new messages arrive for
        several consecutive polls (idle), pending messages from the PEL
        are processed (retry of failed messages).
        """
        try:
            self.logger.info("Starting Redis Streams consumer loop")
            # Initial drain on startup
            await self._drain_pending()
            while self.running:
                try:
                    results = await self._read_new_messages()

                    if not results:
                        # No new messages - increment idle counter
                        self._consecutive_empty_polls += 1
                        if self._consecutive_empty_polls >= self._idle_threshold:
                            # Idle: process pending messages
                            await self._drain_pending()
                            self._consecutive_empty_polls = 0
                        continue

                    # Reset idle counter when new messages arrive
                    self._consecutive_empty_polls = 0

                    for stream_name, messages in results:
                        for message_id, fields in messages:
                            try:
                                self.logger.debug(
                                    "Received message: stream=%s, id=%s",
                                    stream_name,
                                    message_id,
                                )
                                success, is_terminal = await self._process_message_with_classification(
                                    stream_name, message_id, fields
                                )
                                await self._finalize_message(stream_name, message_id, success, is_terminal)
                            except Exception as e:
                                self.logger.error(
                                    "Error processing individual message: %s", e
                                )
                                # Treat as transient error for retry
                                await self._finalize_message(stream_name, message_id, False, False)
                                continue

                except asyncio.CancelledError:
                    self.logger.info("Redis Streams consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error(
                        "Error in consume_messages loop: %s: %s", type(e).__name__, e
                    )
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(
                "Fatal error in consume_messages: %s: %s", type(e).__name__, e
            )
        finally:
            await self.cleanup()

    async def _process_message_with_classification(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> tuple[bool, bool]:
        """Process message and return (success, is_terminal_error).

        Returns:
            Tuple of (success, is_terminal_error):
            - success: True if message processed successfully
            - is_terminal_error: True if error is terminal (should ACK immediately)
        """
        try:
            if _MESSAGE_VALUE_FIELD not in fields:
                self.logger.debug(
                    "Skipping message %s without value field (likely init message)",
                    message_id,
                )
                return True, False

            value_str = fields[_MESSAGE_VALUE_FIELD]
            try:
                raw = json.loads(value_str)
                if isinstance(raw, str):
                    raw = json.loads(raw)
            except json.JSONDecodeError as e:
                self.logger.error(
                    "JSON parsing failed for message %s: %s", message_id, e
                )
                # JSON decode error is terminal
                return False, True

            if not self.message_handler:
                self.logger.error("No message handler set for %s", message_id)
                return False, True

            if raw is None:
                self.logger.error(
                    "Parsed message is None for %s, skipping", message_id
                )
                return False, True

            parsed_message = StreamMessage(**raw)

            # Carry the producer's trace id into consumer-side logs.
            envelope = raw if isinstance(raw, dict) else {}
            ctx = context_from_envelope(envelope)
            token = set_context(ctx.root_id)
            try:
                result = await self.message_handler(parsed_message)
                return result, False
            except Exception as e:
                self.logger.error(
                    "Error in message handler for %s: %s",
                    message_id,
                    e,
                    exc_info=True,
                )

                # Classify the exception
                error_type = MessageErrorClassifier.classify_by_exception(e)
                is_terminal = error_type == MessageErrorType.TERMINAL

                if is_terminal:
                    self.logger.warning(
                        "Terminal error in handler for %s: %s",
                        message_id,
                        type(e).__name__,
                    )
                else:
                    self.logger.warning(
                        "Transient error in handler for %s: %s, will retry",
                        message_id,
                        type(e).__name__,
                    )

                return False, is_terminal
            finally:
                reset_context(token)

        except Exception as e:
            self.logger.error(
                "Unexpected error processing message %s: %s",
                message_id,
                e,
                exc_info=True,
            )

            # Classify the exception
            error_type = MessageErrorClassifier.classify_by_exception(e)
            is_terminal = error_type == MessageErrorType.TERMINAL

            return False, is_terminal
