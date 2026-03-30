import asyncio
import json
import os
import threading
from collections.abc import AsyncGenerator, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from logging import Logger
from typing import Any, Optional

from redis.asyncio import Redis

from app.services.messaging.config import RedisStreamsConfig
from app.services.messaging.interface.consumer import IMessagingConsumer

MAX_CONCURRENT_PARSING = int(os.getenv("MAX_CONCURRENT_PARSING", "5"))
MAX_CONCURRENT_INDEXING = int(os.getenv("MAX_CONCURRENT_INDEXING", "10"))
SHUTDOWN_TASK_TIMEOUT = float(os.getenv("SHUTDOWN_TASK_TIMEOUT", "240.0"))
MAX_PENDING_INDEXING_TASKS = int(
    os.getenv(
        "MAX_PENDING_INDEXING_TASKS",
        str(max(MAX_CONCURRENT_PARSING, MAX_CONCURRENT_INDEXING) * 4),
    )
)


class IndexingEvent:
    PARSING_COMPLETE = "parsing_complete"
    INDEXING_COMPLETE = "indexing_complete"


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
        self.message_handler: Optional[Callable] = None
        self._active_futures: set[Future[Any]] = set()
        self._futures_lock = threading.Lock()
        self._backpressure_active = False

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
                        f"Created consumer group {self.config.group_id} for stream {topic}"
                    )
                except Exception as e:
                    if "BUSYGROUP" in str(e):
                        self.logger.debug(
                            f"Consumer group {self.config.group_id} already exists for {topic}"
                        )
                    else:
                        raise

            self.logger.info(
                "Successfully initialized Redis Streams consumer for indexing"
            )
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            await self.stop()
            raise

    def _start_worker_thread(self) -> None:
        def run_worker_loop() -> None:
            self.worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.worker_loop)
            self.parsing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_PARSING)
            self.indexing_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INDEXING)
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

    async def cleanup(self) -> None:
        try:
            self._stop_worker_thread()
            if self.redis:
                await self.redis.close()
                self.logger.info("Redis Streams consumer stopped")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    async def start(  # type: ignore[override]
        self,
        message_handler: Callable[
            [dict[str, Any]], AsyncGenerator[dict[str, Any], None]
        ],
    ) -> None:
        try:
            self.running = True
            self.message_handler = message_handler
            self.main_loop = asyncio.get_running_loop()

            if not self.redis:
                await self.initialize()

            self.consume_task = asyncio.create_task(self._consume_loop())
            self.logger.info(
                f"Started Redis Streams consumer task with parsing_slots={MAX_CONCURRENT_PARSING}, "
                f"indexing_slots={MAX_CONCURRENT_INDEXING}"
            )
        except Exception as e:
            self.logger.error(f"Failed to start Redis Streams consumer: {str(e)}")
            raise

    async def stop(  # type: ignore[override]
        self,
        message_handler: Optional[Callable] = None,
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
                self.logger.error(f"Error stopping Redis Streams consumer: {e}")

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
            f"Waiting for {len(futures_to_wait)} active tasks to complete"
        )
        for future in futures_to_wait:
            try:
                future.result(timeout=SHUTDOWN_TASK_TIMEOUT)
            except TimeoutError:
                future.cancel()
            except Exception as e:
                self.logger.warning(f"Task errored during shutdown: {e}")

    def _get_active_task_count(self) -> int:
        with self._futures_lock:
            return len(self._active_futures)

    async def _consume_loop(self) -> None:
        try:
            self.logger.info("Starting Redis Streams consumer loop")
            while self.running:
                try:
                    active_count = self._get_active_task_count()
                    if active_count >= MAX_PENDING_INDEXING_TASKS:
                        if not self._backpressure_active:
                            self.logger.warning(
                                f"Backpressure engaged: {active_count} active tasks"
                            )
                            self._backpressure_active = True
                        await asyncio.sleep(0.5)
                        continue
                    elif self._backpressure_active:
                        self.logger.info(
                            f"Backpressure cleared: {active_count}/{MAX_PENDING_INDEXING_TASKS}"
                        )
                        self._backpressure_active = False

                    streams = {topic: ">" for topic in self.config.topics}
                    results = await self.redis.xreadgroup(  # type: ignore
                        groupname=self.config.group_id,
                        consumername=self.config.client_id,
                        streams=streams,
                        count=1,
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
                                    f"Received message: stream={stream_name}, id={message_id}"
                                )
                                await self._start_processing_task(
                                    stream_name, message_id, fields
                                )
                            except Exception as e:
                                self.logger.error(
                                    f"Error processing individual message: {e}"
                                )
                                continue

                except asyncio.CancelledError:
                    self.logger.info("Redis Streams consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error(f"Error in consume_messages loop: {e}")
                    if self.running:
                        await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Fatal error in consume_messages: {e}")
        finally:
            active_count = self._get_active_task_count()
            self.logger.info(
                f"Consume loop exited. Active tasks remaining: {active_count}"
            )

    def _parse_message(
        self, message_id: str, fields: dict[str, str]
    ) -> dict[str, Any] | None:
        if "value" not in fields:
            self.logger.debug(
                f"Skipping message {message_id} without value field (likely init message)"
            )
            return None

        try:
            value_str = fields["value"]
            parsed = json.loads(value_str)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            return parsed
        except json.JSONDecodeError as e:
            self.logger.error(
                f"JSON parsing failed for message {message_id}: {str(e)}"
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

        def on_future_done(f: Future[Any]) -> None:
            with self._futures_lock:
                self._active_futures.discard(f)
            try:
                _ = f.result()
            except Exception as exc:
                self.logger.error(f"Task completed with unhandled exception: {exc}")

        future.add_done_callback(on_future_done)

    async def _process_message_wrapper(
        self, stream_name: str, message_id: str, fields: dict[str, str]
    ) -> bool:
        parsing_held = False
        indexing_held = False

        if not self.parsing_semaphore or not self.indexing_semaphore:
            self.logger.error(f"Semaphores not initialized for {message_id}")
            return False

        try:
            await self.parsing_semaphore.acquire()
            parsing_held = True

            await self.indexing_semaphore.acquire()
            indexing_held = True

            parsed_message = self._parse_message(message_id, fields)
            if parsed_message is None:
                self.logger.warning(
                    f"Failed to parse message {message_id}, skipping"
                )
                return False

            if self.message_handler:
                async for event in self.message_handler(parsed_message):
                    event_type = event.get("event")
                    if (
                        event_type == IndexingEvent.PARSING_COMPLETE
                        and parsing_held
                        and self.parsing_semaphore
                    ):
                        self.parsing_semaphore.release()
                        parsing_held = False
                    elif (
                        event_type == IndexingEvent.INDEXING_COMPLETE
                        and indexing_held
                        and self.indexing_semaphore
                    ):
                        self.indexing_semaphore.release()
                        indexing_held = False

                # Acknowledge on the main loop where self.redis was created
                if self.redis and self.main_loop:
                    future = asyncio.run_coroutine_threadsafe(
                        self.redis.xack(stream_name, self.config.group_id, message_id),  # type: ignore
                        self.main_loop,
                    )
                    future.result(timeout=10)
            else:
                self.logger.error(f"No message handler available for {message_id}")
                return False

            return True

        except Exception as e:
            self.logger.error(
                f"Error in process_message_wrapper for {message_id}: {e}"
            )
            return False
        finally:
            if parsing_held and self.parsing_semaphore:
                self.parsing_semaphore.release()
            if indexing_held and self.indexing_semaphore:
                self.indexing_semaphore.release()
