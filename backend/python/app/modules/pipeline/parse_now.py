"""Parse a record on demand when someone reads it before it is indexed.

``fetch_full_record`` and the record-content routes answer "not indexed yet" for a
record whose content nobody has parsed. With this, they ask indexing to parse it
now: a ``reindexRecord`` event with ``parseNow`` on the priority topic. Indexing
stores the parsed content and marks the record (``parseNowStatus``); the reader
polls that marker for a bounded time and then reads the stored content like any
other. Nothing here raises: whatever goes wrong, the reader gets today's answer.
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    FOLDER_MIME_TYPES,
    CollectionNames,
    EventTypes,
    ProgressStatus,
)
from app.modules.indexing.connector_off_events import (
    ConnectorState,
    connector_state,
    gating_connector_id,
)
from app.modules.pipeline.first_use import _get_producer
from app.services.graph_db.common.record_visibility import (
    is_live_record,
    is_readable_record,
)
from app.services.messaging.config import Topic
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.redis.connection_provider import IRedisConnectionProvider

# Only a record whose content nobody has parsed, or whose parse is stuck or
# failed, is worth asking for. The rest are final answers (password, file type,
# empty, needs a model) or a deliberate skip.
ELIGIBLE_STATUSES = frozenset({
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.QUEUED.value,
    ProgressStatus.IN_PROGRESS.value,
    ProgressStatus.AUTO_INDEX_OFF.value,
    ProgressStatus.FAILED.value,
})

# The marker's final outcomes, as the status the not-indexed note describes.
_TERMINAL_MARKERS = {
    "PASSWORD_PROTECTED": ProgressStatus.PASSWORD_PROTECTED.value,
    "FILE_TYPE_NOT_SUPPORTED": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
    "EMPTY": ProgressStatus.EMPTY.value,
    "NEEDS_MULTIMODAL": ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
}

_DEFAULT_MAX_PER_CALL = 3
# The longest wait plus slack; a lost event frees the record after it.
_IN_FLIGHT_TTL_SECONDS = 90
_REDIS_TIMEOUT_SECONDS = 2.0
_PRODUCER_TIMEOUT_SECONDS = 5.0


def max_per_call() -> int:
    try:
        return max(1, int(os.getenv("PARSE_NOW_MAX_PER_CALL", "")))
    except ValueError:
        return _DEFAULT_MAX_PER_CALL


def terminal_marker_status(doc: dict[str, Any]) -> str | None:
    """The status to describe when the record's parse already ended for good at its
    current revision, else None. A marker of an older revision says nothing."""
    # A marker written before contentRev exists has no rev; None matches None.
    if doc.get("parseNowRev") == doc.get("contentRev"):
        return _TERMINAL_MARKERS.get(doc.get("parseNowStatus"))
    return None


def is_folder_record(doc: dict[str, Any], fallback_mime: str | None = None) -> bool:
    """A folder has no content of its own to parse."""
    return doc.get("isFile") is False or (doc.get("mimeType") or fallback_mime) in FOLDER_MIME_TYPES


FOLDER = "FOLDER"
READY = "ready"


def parse_outcome(doc: dict[str, Any]) -> str | None:
    """How a record being parsed stands: ``READY`` once it can be read, the status to
    describe once its parse ended for good (or indexing left it in a final state),
    None while it is still going."""
    if is_readable_record(doc):
        return READY
    if (ended := terminal_marker_status(doc)) is not None:
        return ended
    # Ends this reader's wait only; assess() still lets a later read ask again.
    if doc.get("parseNowStatus") == "FAILED" and doc.get("parseNowRev") == doc.get("contentRev"):
        return ProgressStatus.FAILED.value
    status = doc.get("indexingStatus") or "unknown"
    return None if status in ELIGIBLE_STATUSES else status


async def optional_redis(config_service: Any, logger: Any) -> Any:  # noqa: ANN401
    """The shared Redis provider, or None when it is down: ParseNow then runs without dedup.

    Built from the service's own config_service: the containers' ``redis_provider`` is bound to the base
    container's config_service, which the query and connector containers replace.
    """
    try:
        from app.services.redis.config import RedisConnectionConfig
        from app.services.redis.connection_provider_factory import (
            get_prepared_redis_provider,
        )

        redis = await config_service.get_redis_config()
        return await get_prepared_redis_provider(
            RedisConnectionConfig.from_host_port(
                host=redis.host, port=redis.port, password=redis.password, db=redis.db, tls=redis.tls,
            )
        )
    except Exception:
        logger.warning("Redis unavailable; parse now runs without dedup", exc_info=True)
        return None


def parse_now_event(record_id: str, doc: dict[str, Any], org_id: str, user_id: str) -> dict[str, Any]:
    """``org_id`` is the caller's, never the graph row's."""
    return {
        "eventType": EventTypes.REINDEX_RECORD.value,
        "timestamp": get_epoch_timestamp_in_ms(),
        "payload": {
            "recordId": record_id,
            "orgId": org_id,
            "virtualRecordId": doc.get("virtualRecordId"),
            "connectorId": doc.get("connectorId"),
            "connectorName": doc.get("connectorName"),
            "mimeType": doc.get("mimeType"),
            "parseNow": True,
            "requestedByUserId": user_id,
            "contentRev": doc.get("contentRev"),
        },
    }


class ParseNow:
    """Asks indexing to parse records for one read. ``producer`` is the caller's
    when it has one; otherwise a lazy process-wide producer is made. Without a
    ``redis_provider`` (the container's) two readers may both send the event."""

    def __init__(
        self,
        config_service: ConfigurationService,
        logger: Logger,
        producer: IMessagingProducer | None = None,
        redis_provider: IRedisConnectionProvider | None = None,
    ) -> None:
        self.config_service = config_service
        self._logger = logger
        self._producer = producer
        self._redis_provider = redis_provider

    async def assess(self, graph_provider: IGraphDBProvider, doc: dict[str, Any]) -> tuple[bool, str]:
        """``(True, status)`` when the record should be parsed now; otherwise
        ``(False, status)`` with the status the not-indexed reply should describe."""
        status = doc.get("indexingStatus") or "unknown"
        if is_folder_record(doc):
            return False, FOLDER
        if status not in ELIGIBLE_STATUSES or not is_live_record(doc):
            return False, status
        if (ended := terminal_marker_status(doc)) is not None:
            return False, ended
        connector_id = gating_connector_id(doc)
        if connector_id is None:
            return True, status
        try:
            app = await graph_provider.get_document(
                connector_id, CollectionNames.APPS.value, raise_on_error=True,
            )
        except Exception:
            self._logger.warning("Connector %s unreadable; not parsing on demand", connector_id, exc_info=True)
            return False, status
        return connector_state(app) is ConnectorState.ACTIVE, status

    async def request(self, record_id: str, doc: dict[str, Any], org_id: str, user_id: str) -> bool:
        """True when the record is being parsed (this call sent the event, or another
        reader's is still in flight); False when it could not be asked for."""
        sent = False
        claim: tuple[Any, str] | bool | None = None
        try:
            claim = await self._claim(f"parse-now:{org_id}:{record_id}")
            if claim is False:
                return True
            # Bounded: a dead broker must not stall the read.
            async with asyncio.timeout(_PRODUCER_TIMEOUT_SECONDS):
                producer = self._producer or await _get_producer(self.config_service, self._logger)
                sent = await producer.send_message(
                    Topic.RECORD_EVENTS_PRIORITY.value, parse_now_event(record_id, doc, org_id, user_id), key=record_id,
                ) is not False
        except Exception:
            self._logger.warning("Could not ask indexing to parse %s", record_id, exc_info=True)
        finally:
            if not sent and claim:
                await self._release(*claim)
        return sent

    async def _claim(self, key: str) -> tuple[Any, str] | bool | None:
        """``(client, key)`` when this call is the one to send the event, False when
        another reader's is in flight, None when Redis cannot say (send anyway)."""
        provider = self._redis_provider
        if provider is None:
            return None
        try:
            namespaced = self._namespaced(key)
            client = provider.get_client()
            async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
                taken = await client.set(namespaced, "1", nx=True, ex=_IN_FLIGHT_TTL_SECONDS)
            return (client, namespaced) if taken else False
        except Exception:
            self._logger.warning("Parse-now dedup unavailable; sending without it", exc_info=True)
            return None

    def _namespaced(self, key: str) -> str:
        ns = self._redis_provider.key_namespace
        return f"{ns}:{key}" if ns else key

    async def release(self, record_id: str, org_id: str) -> None:
        """Free the claim once a read's wait ended for good, so the next read can ask again."""
        if self._redis_provider is None:
            return
        try:
            await self._release(
                self._redis_provider.get_client(), self._namespaced(f"parse-now:{org_id}:{record_id}"),
            )
        except Exception:
            self._logger.warning("Could not release the parse-now claim for %s", record_id, exc_info=True)

    async def _release(self, client: Any, key: str) -> None:  # noqa: ANN401
        try:
            async with asyncio.timeout(_REDIS_TIMEOUT_SECONDS):
                await client.delete(key)
        except Exception:
            self._logger.warning("Could not release the parse-now claim %s", key, exc_info=True)
