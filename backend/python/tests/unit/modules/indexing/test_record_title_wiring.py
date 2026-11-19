"""How the record title consumer is built and started by the indexing service."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import record_title_consumer as mod
from app.services.messaging.config import ConsumerType, MessageBrokerType, Topic

LOGGER = logging.getLogger("record-title-wiring-test")


def _container() -> MagicMock:
    container = MagicMock()
    container._graph_provider = MagicMock(name="graph")
    container.entity_vector_store = AsyncMock(return_value=MagicMock(name="store"))
    return container


class TestBuild:
    async def test_it_is_a_plain_consumer_of_its_own_topic_and_group(self) -> None:
        container, retry, worker = _container(), MagicMock(name="retry"), MagicMock(name="worker")
        with (
            patch("app.services.messaging.utils.MessagingUtils.create_consumer_config", new_callable=AsyncMock,
                  return_value={"cfg": 1}) as config,
            patch("app.services.messaging.messaging_factory.MessagingFactory.create_consumer") as create,
        ):
            built = await mod.build_record_title_consumer(
                container, broker_type=MessageBrokerType.KAFKA, retry_manager=retry, worker=worker, logger=LOGGER,
            )
        config.assert_awaited_once_with(
            container, "record_title_client", "record_title_group", [Topic.RECORD_TITLE_EVENTS.value],
        )
        kwargs = create.call_args.kwargs
        assert kwargs["consumer_type"] is ConsumerType.SIMPLE
        assert kwargs["retry_manager"] is retry
        assert kwargs["config"] == {"cfg": 1}
        # None of the record consumer's scheduling or admission machinery.
        for name in ("governor", "concurrency_manager", "fair_scheduler_config", "stage_admission", "worker",
                     "connector_off_filter", "disposition_sink", "producer"):
            assert name not in kwargs
        assert built.consumer is create.return_value
        assert built.writer.graph is container._graph_provider

    async def test_the_writer_reads_the_store_lazily_and_runs_on_the_worker_loop(self) -> None:
        container, worker = _container(), MagicMock(name="worker")
        with (
            patch("app.services.messaging.utils.MessagingUtils.create_consumer_config", new_callable=AsyncMock),
            patch("app.services.messaging.messaging_factory.MessagingFactory.create_consumer"),
        ):
            built = await mod.build_record_title_consumer(
                container, broker_type=MessageBrokerType.REDIS, retry_manager=None, worker=worker, logger=LOGGER,
            )
        container.entity_vector_store.assert_not_awaited()
        assert await built.writer._get_store() is await container.entity_vector_store()
        assert built.writer._worker is worker

    async def test_it_falls_back_to_resolving_the_graph_provider(self) -> None:
        container = MagicMock()
        container._graph_provider = None
        container.graph_provider = AsyncMock(return_value="graph")
        with (
            patch("app.services.messaging.utils.MessagingUtils.create_consumer_config", new_callable=AsyncMock),
            patch("app.services.messaging.messaging_factory.MessagingFactory.create_consumer"),
        ):
            built = await mod.build_record_title_consumer(
                container, broker_type=MessageBrokerType.POSTGRES, retry_manager=None, worker=None, logger=LOGGER,
            )
        assert built.writer.graph == "graph"


class TestStartInIndexingService:
    """``_start_record_title_consumer`` in ``app.indexing_main``."""

    @staticmethod
    def _built() -> MagicMock:
        built = MagicMock()
        built.start = AsyncMock()
        built.writer.aclose = AsyncMock()
        return built

    async def test_the_topic_is_ensured_and_the_consumer_joins_the_list(self) -> None:
        from app.indexing_main import _start_record_title_consumer

        admin, consumers, built = MagicMock(ensure_topics_exist=AsyncMock()), [], self._built()
        container = MagicMock()
        with patch("app.indexing_main.build_record_title_consumer", new_callable=AsyncMock, return_value=built):
            await _start_record_title_consumer(
                container, admin, consumers,
                broker_type=MessageBrokerType.KAFKA, retry_manager=None, worker=MagicMock(), logger=LOGGER,
            )
        admin.ensure_topics_exist.assert_awaited_once_with(["record-title-events"])
        built.start.assert_awaited_once()
        assert consumers == [("record_title", built, None)]

    async def test_a_missing_topic_is_retried_in_the_background_and_never_raised(self) -> None:
        from app.indexing_main import _start_record_title_consumer

        admin = MagicMock(ensure_topics_exist=AsyncMock(side_effect=[RuntimeError("denied"), None]))
        consumers, built = [], self._built()
        container = MagicMock()
        with (
            patch("app.indexing_main.build_record_title_consumer", new_callable=AsyncMock, return_value=built),
            patch("app.indexing_main._TOPIC_RETRY_FIRST_S", 0.0),
        ):
            await _start_record_title_consumer(
                container, admin, consumers,
                broker_type=MessageBrokerType.KAFKA, retry_manager=None, worker=MagicMock(), logger=LOGGER,
            )
            assert consumers == []
            await container.record_title_startup_task
        assert consumers == [("record_title", built, None)]

    async def test_a_failing_title_consumer_does_not_stop_the_others(self) -> None:
        from app.indexing_main import _start_record_title_consumer

        admin = MagicMock(ensure_topics_exist=AsyncMock())
        record = ("record", MagicMock(), MagicMock())
        consumers = [record]
        built = self._built()
        built.start.side_effect = RuntimeError("broker blip")
        container = MagicMock()
        with (
            patch("app.indexing_main.build_record_title_consumer", new_callable=AsyncMock, return_value=built),
            patch("app.indexing_main._TOPIC_RETRY_FIRST_S", 3600.0),
        ):
            await _start_record_title_consumer(
                container, admin, consumers,
                broker_type=MessageBrokerType.KAFKA, retry_manager=None, worker=MagicMock(), logger=LOGGER,
            )
            task = container.record_title_startup_task
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert consumers == [record]
        built.writer.aclose.assert_awaited()

    async def test_shutdown_cancels_a_title_start_still_waiting_for_its_topic(self) -> None:
        from app.indexing_main import stop_kafka_consumers

        container = MagicMock()
        container.kafka_consumers = []
        container.worker_loop = None
        waiting = asyncio.create_task(asyncio.sleep(3600))
        container.record_title_startup_task = waiting
        await stop_kafka_consumers(container)
        assert waiting.cancelled()
