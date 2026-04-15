import asyncio
import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from logging import Logger
from typing import Optional, override

from redis.asyncio import Redis

from app.services.messaging.config import (
    IndexingEvent,
    IndexingMessageHandler,
    RedisStreamsConfig,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.interface.consumer import IMessagingConsumer

_BUSYGROUP_ERROR = "BUSYGROUP"
_MESSAGE_VALUE_FIELD = "value"


class IndexingRedisStreamsConsumer(IMessagingConsumer):
    """Redis Streams consumer with dual-semaphore control for indexing pipeline.

    Mirrors IndexingKafkaConsumer behavior but uses Redis Streams instead of Kafka.
    """

    def __init__(self, logger: Logger, config: RedisStreamsConfig) -> None:
        self.logger = logger
        self.config = config
        self.redis: Optional[Redis] = None
        self.running = False
        self.consume_task: Optional[asyncio.Task] = None
        self.worker_executor: Optional[ThreadPoolExecutor] = None
        self.worker_loop: Optional[asyncio.AbstractEventLoop] = None
        self.worker_loop_ready = threading.Event()
        self.main_loop: Optional[asyncio.AbstractEventLoop] = None
        self.parsing_semaphore: Optional[asyncio.Semaphore] = None
        self.indexing_semaphore: Optional[asyncio.Semaphore] = None
        self.message_handler: Optional[IndexingMessageHandler] = None
        self._active_futures: set[Future[bool]] = set()
        self._futures_lock = threading.Lock()
        self._backpressure_active = False


    @override
    async def initialize(self) -> None:
        try:
            self._start_worker_thread()

            if not self.worker_loop_ready.wait(timeout=60.0):
                raise RuntimeError("Worker thread event loop not initialized in time")

            if not self.worker_loop or not self.worker_loop.is_running():
                raise RuntimeError("Worker thread event loop failed to start")

            self.redis = Redis(
                host=self.config.host,
                port=self.config.port,
                password=self.config.password,
                db=self.config.db,
                decode_responses=True,
            )
            await self.redis.ping()

            for topic in self.config.topics:
                try:
                    await self.redis.xgroup_create(  # type: ignore
                        topic,
                        self.config.group_id,
                        id="0",
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
                            "Consumer group %s already exists for %s",
                            self.config.group_id,
                            topic,
                        )
                    else:
                        raise

            self.logger.info(
                "Successfully initialized Redis Streams consumer for indexing"
            )
        except Exception as e:
            self.logger.error("Failed to create consumer: %s", e)
            await self.stop()
            raise

    def _start_worker_thread(self) -> None:
        def run_worker_loop() -> None:
            self.worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.worker_loop)
            self.parsing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_parsing)
            self.indexing_semaphore = asyncio.Semaphore(messaging_env.max_concurrent_indexing)
            self.logger.info(
                "Worker thread event loop started with semaphores initialized"
            )
            self.worker_loop_ready.set()
            try:
                self.worker_loop.run_forever()
            finally:
                pending = asyncio.all_tasks(self.worker_loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self.worker_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                self.worker_loop.close()
                self.logger.info("Worker thread event loop closed")

        self.worker_loop_ready.clear()
        self.worker_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="indexing-worker"
        )
        self.worker_executor.submit(run_worker_loop)

    @override
    async def cleanup(self) -> None:
        try:
            self._stop_worker_thread()
            if self.redis:
                await self.redis.close()
                self.logger.info("Redis Streams consumer stopped")
        except Exception as e:
            self.logger.error("Error during cleanup: %s", e)

    async def start(  # type: ignore[override]
        self,
        message_handler: IndexingMessageHandler,
    ) -> None:
        try:
            self.running = True
            self.message_handler = message_handler
            self.main_loop = asyncio.get_running_loop()

            if not self.redis:
                await self.initialize()

            self.consume_task = asyncio.create_task(self._consume_loop())
            self.logger.info(
                "Started Redis Streams consumer task with parsing_slots=%d, indexing_slots=%d",
                messaging_env.max_concurrent_parsing,
                messaging_env.max_concurrent_indexing,
            )
        except Exception as e:
            self.logger.error("Failed to start Redis Streams consumer: %s", e)
            raise

    async def stop(  # type: ignore[override]
        self,
        message_handler: Optional[IndexingMessageHandler] = None,
    ) -> None:
        self.logger.info("Stopping Redis Streams consumer...")
        self.running = False

        if self.consume_task:
            self.consume_task.cancel()
            try:
                await self.consume_task
            except asyncio.CancelledError:
                self.logger.debug("Consume task cancelled")

        self._stop_worker_thread()

        if self.redis:
            try:
                await self.redis.close()
                self.logger.info("Redis Streams consumer stopped")
            except Exception as e:
                self.logger.error("Error stopping Redis Streams consumer: %s", e)

    @override
    def is_running(self) -> bool:
        return self.running

    def _stop_worker_thread(self) -> None:
        self._wait_for_active_futures()
        if self.worker_loop and self.worker_loop.is_running():
            self.worker_loop.call_soon_threadsafe(self.worker_loop.stop)
        if self.worker_executor:
            self.worker_executor.shutdown(wait=True)
            self.worker_executor = None
            self.worker_loop = None
        with self._futures_lock:
            self._active_futures.clear()

    def _wait_for_active_futures(self) -> None:
        with self._futures_lock:
            futures_to_wait = list(self._active_futures)
        if not futures_to_wait:
            return

        self.logger.info(
            "Waiting for %d active tasks to complete", len(futures_to_wait)
        )
        for future in futures_to_wait:
            try:
                future.result(timeout=messaging_env.shutdown_task_timeout)
            except TimeoutError:
                future.cancel()
            except Exception as e:
                self.logger.warning("Task errored during shutdown: %s", e)

    def _get_active_task_count(self) -> int:
        with self._futures_lock:
            return len(self._active_futures)

    async def _exceeds_max_retries(self, topic: str, message_id: str) -> bool:
        """Check delivery count via XPENDING and ACK poison messages.

        Returns True (and ACKs the message) when the delivery count exceeds
        ``MAX_DELIVERY_ATTEMPTS``, effectively dead-lettering the message so
        it no longer blocks the PEL on every restart.
        """
        max_attempts = messaging_env.max_delivery_attempts
        try:
            details = await self.redis.xpending_range(  # type: ignore
                topic, self.config.group_id,
                min=message_id, max=message_id, count=1,
            )
            if details:
                times_delivered = details[0].get("times_delivered", 0)
                if times_delivered >= max_attempts:
                    await self.redis.xack(topic, self.config.group_id, message_id)  # type: ignore
                    self.logger.warning(
                        "Dead-lettered message %s on stream %s after %d delivery attempts (max %d)",
                        message_id, topic, times_delivered, max_attempts,
                    )
                    return True
        except Exception as e:
            self.logger.error(
                "Error checking delivery count for %s: %s", message_id, e,
            )
        return False

    async def _drain_pending(self) -> None:
        """Re-process messages left in the Pending Entries List (PEL).

        Phase 1: XAUTOCLAIM to steal idle messages from other (crashed) consumers.
        Phase 2: XREADGROUP with id "0" to recover messages already owned by THIS
        consumer (e.g. delivered before a crash/restart but never ACK-ed). Without
        Phase 2, on a same-client_id restart those messages would sit in the PEL
        forever — XAUTOCLAIM won't touch them (same consumer name) and XREADGROUP
        with ">" only delivers brand-new messages.
        """
        self.logger.info("Draining pending messages from PEL")

        for topic in self.config.topics:
            # Phase 1: claim idle messages from other (possibly crashed) consumers
            start_id = "0-0"
            while self.running:
                active_count = self._get_active_task_count()
                if active_count >= messaging_env.max_pending_indexing_tasks:
                    await asyncio.sleep(0.5)
                    continue
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
                        if not self.running:
                            return
                        try:
                            if await self._exceeds_max_retries(topic, message_id):
                                continue
                            self.logger.info(
                                "Recovering pending message: stream=%s, id=%s",
                                topic, message_id,
                            )
                            await self._start_processing_task(
                                topic, message_id, fields
                            )
                        except Exception as e:
                            self.logger.error(
                                "Error recovering pending message %s: %s",
                                message_id, e,
                            )
                    start_id = next_id
                    if next_id == b"0-0" or next_id == "0-0":
                        break
                except Exception as e:
                    self.logger.error("Error during XAUTOCLAIM on %s: %s", topic, e)
                    break

            # Phase 2: read messages already in THIS consumer's PEL.
            # Using id "0" tells Redis to redeliver everything in our own PEL —
            # the only way a same-client_id restart can resume in-flight work.
            while self.running:
                active_count = self._get_active_task_count()
                if active_count >= messaging_env.max_pending_indexing_tasks:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.config.client_id,
                        streams={topic: "0"},
                        count=self.config.batch_size,
                    )

                    if not results:
                        break

                    drained_any = False
                    for _stream_name, messages in results:
                        if not messages:
                            continue
                        for message_id, fields in messages:
                            if not self.running:
                                return
                            drained_any = True
                            try:
                                if await self._exceeds_max_retries(topic, message_id):
                                    continue
                                self.logger.info(
                                    "Recovering own pending message: stream=%s, id=%s",
                                    topic, message_id,
                                )
                                await self._start_processing_task(
                                    topic, message_id, fields
                                )
                            except Exception as e:
                                self.logger.error(
                                    "Error recovering own pending message %s: %s",
                                    message_id, e,
                                )

                    if not drained_any:
                        break
                except Exception as e:
                    self.logger.error(
                        "Error draining own PEL on %s: %s", topic, e,
                    )
                    break

        self.logger.info("PEL fully drained, switching to new messages")

    async def _consume_loop(self) -> None:
        try:
            self.logger.info("Starting Redis Streams consumer loop")
            await self._drain_pending()
            while self.running:
                try:
                    active_count = self._get_active_task_count()
                    if active_count >= messaging_env.max_pending_indexing_tasks:
                        if not self._backpressure_active:
                            self.logger.warning(
                                "Backpressure engaged: %d active tasks",
                                active_count,
                            )
                            self._backpressure_active = True
                        await asyncio.sleep(0.5)
                        continue
                    elif self._backpressure_active:
                        self.logger.info(
                            "Backpressure cleared: %d/%d",
                            active_count,
                            messaging_env.max_pending_indexing_tasks,
                        )
                        self._backpressure_active = False

                    streams = dict.fromkeys(self.config.topics, ">")
                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.config.client_id,
                        streams=streams,
                        count=self.config.batch_size,
                        block=self.config.block_ms,
                    )

                    if not results:
                        continue

                    for stream_name, messages in results:
                        for message_id, fields in messages:
                            if not self.running:
                                break
                            try:
                                self.logger.info(
                                    "Received message: stream=%s, id=%s",
                                    stream_name,
                                    message_id,
                                )
                                await self._start_processing_task(
                                    stream_name, message_id, fields
                                )
                            except Exception as e:
                                self.logger.error(
                                    "Error processing individual message: %s", e
                                )
                                continue

                except asyncio.CancelledError:
                    self.logger.info("Redis Streams consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error("Error in consume_messages loop: %s", e)
                    if self.running:
                        await asyncio.sleep(1)

        except Exception as e:
            self.logger.error("Fatal error in consume_messages: %s", e)
        finally:
            active_count = self._get_active_task_count()
            self.logger.info(
                "Consume loop exited. Active tasks remaining: %d", active_count
            )

    def _parse_message(
        self, message_id: str, fields: dict[str, str]
    ) -> StreamMessage | None:
        if _MESSAGE_VALUE_FIELD not in fields:
            self.logger.debug(
                "Skipping message %s without value field (likely init message)",
                message_id,
            )
            return None

        try:
            value_str = fields[_MESSAGE_VALUE_FIELD]
            raw = json.loads(value_str)
            if isinstance(raw, str):
                raw = json.loads(raw)
            return StreamMessage(**raw)
        except json.JSONDecodeError as e:
            self.logger.error(
                "JSON parsing failed for message %s: %s", message_id, e
            )
            return None

    async def _start_processing_task(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> None:
        if not self.worker_loop:
            self.logger.error("Worker loop not initialized, cannot process message")
            return
        if not self.running:
            return

        future = asyncio.run_coroutine_threadsafe(
            self._process_message_wrapper(stream_name, message_id, fields),
            self.worker_loop,
        )
        with self._futures_lock:
            self._active_futures.add(future)

        def on_future_done(f: Future[bool]) -> None:
            with self._futures_lock:
                self._active_futures.discard(f)
            try:
                _ = f.result()
            except Exception as exc:
                self.logger.error("Task completed with unhandled exception: %s", exc)

        future.add_done_callback(on_future_done)

    async def _process_message_wrapper(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> bool:
        parsing_held = False
        indexing_held = False

        if not self.parsing_semaphore or not self.indexing_semaphore:
            self.logger.error("Semaphores not initialized for %s", message_id)
            return False

        try:
            await self.parsing_semaphore.acquire()
            parsing_held = True

            await self.indexing_semaphore.acquire()
            indexing_held = True

            parsed_message = self._parse_message(message_id, fields)
            if parsed_message is None:
                self.logger.warning(
                    "Failed to parse message %s, skipping", message_id
                )
                return False

            if self.message_handler:
                async for event in self.message_handler(parsed_message):
                    if (
                        event.event == IndexingEvent.PARSING_COMPLETE
                        and parsing_held
                        and self.parsing_semaphore
                    ):
                        self.parsing_semaphore.release()
                        parsing_held = False
                    elif (
                        event.event == IndexingEvent.INDEXING_COMPLETE
                        and indexing_held
                        and self.indexing_semaphore
                    ):
                        self.indexing_semaphore.release()
                        indexing_held = False

                # Acknowledge on the main loop where self.redis was created
                if self.redis and self.main_loop and self.main_loop.is_running():
                    ack_future = asyncio.run_coroutine_threadsafe(
                        self.redis.xack(stream_name, self.config.group_id, message_id),  # type: ignore
                        self.main_loop,
                    )
                    try:
                        ack_future.result(timeout=5)
                    except TimeoutError:
                        self.logger.warning(
                            "Timed out waiting for xack on %s, will be re-delivered",
                            message_id,
                        )
                elif not self.running:
                    self.logger.debug(
                        "Skipping xack for %s during shutdown", message_id
                    )
            else:
                self.logger.error("No message handler available for %s", message_id)
                return False

            return True

        except Exception as e:
            self.logger.error(
                "Error in process_message_wrapper for %s: %s", message_id, e
            )
            return False
        finally:
            if parsing_held and self.parsing_semaphore:
                self.parsing_semaphore.release()
            if indexing_held and self.indexing_semaphore:
                self.indexing_semaphore.release()
