"""Classify a record the first time a search returns it.

With ``ENABLE_CLASSIFY_ON_FIRST_USE`` on, indexing makes a record searchable
and leaves classification (summary, categories, topics) NOT_STARTED, so records
nobody reads never cost a classification call. When a search returns such a
record, this asks indexing to run the classify stage for it: a ``reindexRecord``
event with ``stages=["classify"]`` and ``firstUse``, which redrives the stage
without forcing it. The search itself never waits for it or fails because of it.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.core.org_context import spawn_in_request
from app.services.featureflag.platform_settings import is_classify_on_first_use_enabled
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.messaging.interface.producer import IMessagingProducer

RECORD_EVENTS_TOPIC = "record-events"
# More than one search's worth: a result page names a few dozen records.
_MAX_PER_SEARCH = 50
# A record asked for here is NOT_STARTED until indexing picks the event up; this
# keeps the searches in between from asking again.
_ASKED_TTL_SECONDS = 600.0
_asked: dict[tuple[str, str], float] = {}
_producer: IMessagingProducer | None = None
_producer_lock = asyncio.Lock()
_tasks: set[asyncio.Task[None]] = set()


def unclassified(records: list[Any]) -> list[dict[str, Any]]:
    """The indexed records in ``records`` whose classification waits for first use."""
    found: list[dict[str, Any]] = []
    for doc in records:
        if not isinstance(doc, dict):
            continue
        if (
            doc.get("indexingStatus") == ProgressStatus.COMPLETED.value
            and doc.get("extractionStatus") == ProgressStatus.NOT_STARTED.value
            and doc.get("virtualRecordId")
            and doc.get("contentRev")
            and (doc.get("_key") or doc.get("id"))
        ):
            found.append(doc)
    return found


def first_use_event(doc: dict[str, Any], org_id: str) -> dict[str, Any]:
    return {
        "eventType": EventTypes.REINDEX_RECORD.value,
        "timestamp": get_epoch_timestamp_in_ms(),
        "payload": {
            "recordId": doc.get("_key") or doc.get("id"),
            "orgId": org_id,
            "virtualRecordId": doc.get("virtualRecordId"),
            "connectorId": doc.get("connectorId"),
            "connectorName": doc.get("connectorName"),
            "mimeType": doc.get("mimeType"),
            "stages": ["classify"],
            "stageRev": doc.get("contentRev"),
            "firstUse": True,
        },
    }


def _asked_key(doc: dict[str, Any]) -> tuple[str, str]:
    return str(doc.get("_key") or doc.get("id")), str(doc.get("contentRev"))


def _not_asked_recently(docs: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    for key in [k for k, at in _asked.items() if now - at > _ASKED_TTL_SECONDS]:
        del _asked[key]
    return [doc for doc in docs if _asked_key(doc) not in _asked]


async def _get_producer(config_service: ConfigurationService, logger: Logger) -> IMessagingProducer:
    global _producer  # noqa: PLW0603 (one producer per process, made on first need)
    async with _producer_lock:
        if _producer is None:
            from app.services.messaging.messaging_factory import MessagingFactory
            from app.services.messaging.utils import MessagingUtils

            config = await MessagingUtils.create_producer_config_from_service(
                config_service, "first_use_classify_producer"
            )
            producer = MessagingFactory.create_producer(logger=logger, config=config)
            await producer.initialize()
            _producer = producer
        return _producer


async def classify_on_first_use(
    results: dict[str, Any], org_id: str | None, config_service: ConfigurationService, logger: Logger
) -> int:
    """Ask indexing to classify the records in ``results``, a search of
    ``org_id``, that wait for first use. Returns how many were asked for; never
    raises."""
    try:
        docs = unclassified(results.get("records") or [])
        if not org_id or not docs or not await is_classify_on_first_use_enabled(config_service):
            return 0
        # Capped after the repeat filter, so records past the first page are asked for too.
        now = time.monotonic()
        docs = _not_asked_recently(docs, now)[:_MAX_PER_SEARCH]
        if not docs:
            return 0
        # Reserved together, before the first await, so a search running alongside
        # skips them; each is released if the broker refuses or never gets it.
        pending = [(doc, _asked_key(doc)) for doc in docs]
        for _doc, key in pending:
            _asked[key] = now
        sent = 0
        try:
            producer = await _get_producer(config_service, logger)
            while pending:
                doc, key = pending[0]
                accepted = await producer.send_message(
                    RECORD_EVENTS_TOPIC, first_use_event(doc, org_id), key=key[0]
                ) is not False
                pending.pop(0)
                if accepted:
                    sent += 1
                else:
                    _asked.pop(key, None)
        finally:
            for _doc, key in pending:
                _asked.pop(key, None)
        return sent
    except Exception as e:
        logger.warning("Could not ask for classification of searched records: %s", e)
        return 0


def classify_on_first_use_later(
    results: dict[str, Any], org_id: str | None, config_service: ConfigurationService, logger: Logger
) -> None:
    """``classify_on_first_use`` in the background, so the search answers first.
    It keeps the request's context: the flag is read for this search's org."""
    task = spawn_in_request(classify_on_first_use(results, org_id, config_service, logger))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
