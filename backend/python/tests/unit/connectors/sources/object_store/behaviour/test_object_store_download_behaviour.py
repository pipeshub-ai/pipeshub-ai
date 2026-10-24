"""Downloading a synced object through the S3 and MinIO connectors.

Sync stores the key exactly as the listing returns it (botocore decodes the
listing). A signed URL must name that same key, or the GET misses the object.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from object_store_behaviour_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    make_connector,
)

from app.connectors.core.base.connector.connector_service import BaseConnector

KEYS = ["plain.txt", "reports/100%25.pdf", "a%20b.pdf", "a b+c.pdf", "50% off.txt"]


@pytest.fixture(params=["s3", "minio"])
def connector(
    request: pytest.FixtureRequest, store: FakeObjectStore, db: FakeRecordsDb,
    checkpoints: FakeCheckpointStore, config: FakeConfigService,
) -> BaseConnector:
    return make_connector(request.param, store, db, checkpoints, config)


class TestSignedUrl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", KEYS)
    async def test_the_signed_url_fetches_the_synced_object(self, connector, store, db, key) -> None:
        store.put(key)
        await connector.run_sync()
        record = db.by_path()[f"bucket-1/{key}"]

        url = await connector.get_signed_url(record)

        fetched = connector.data_source.object_at(url)
        assert fetched is not None and fetched.key == key

    @pytest.mark.asyncio
    async def test_the_signed_url_lives_five_minutes(self, connector, store, db) -> None:
        """Its only caller fetches it at once, on the server; S3 checks expiry when the GET starts."""
        store.put("a.txt")
        await connector.run_sync()
        sign = connector.data_source.generate_presigned_url = AsyncMock(wraps=connector.data_source.generate_presigned_url)

        await connector.get_signed_url(db.by_path()["bucket-1/a.txt"])

        assert sign.await_args.kwargs["ExpiresIn"] == 300


def source_answers(status: int, body: str) -> MagicMock:
    """An aiohttp session whose GET of the signed URL answers ``status``."""
    response = AsyncMock()
    response.status = status
    response.headers = {}
    response.text = AsyncMock(return_value=body)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)
    session = AsyncMock()
    session.get = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestStreamErrors:
    """Presigning is local, so a denied or missing object fails on the GET; its status reaches the user."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("source_status", "body", "status", "says"), [
        (403, "<Code>AccessDenied</Code>", 403, "denied"),
        (404, "<Code>NoSuchKey</Code>", 404, "no longer exists"),
        (401, "<Code>InvalidAccessKeyId</Code>", 409, "Reconnect"),
    ])
    async def test_the_source_status_reaches_the_stream_route(
        self, connector, store, db, source_status, body, status, says,
    ) -> None:
        from app.connectors.api.router import _invoke_connector_stream

        store.put("a.txt")
        await connector.run_sync()
        record = await db.get_file_record_by_id(db.by_path()["bucket-1/a.txt"].id)

        with patch("app.utils.streaming.aiohttp.ClientSession", return_value=source_answers(source_status, body)):
            with pytest.raises(HTTPException) as raised:
                await _invoke_connector_stream(connector, record)

        assert raised.value.status_code == status
        assert says in raised.value.detail and connector.display_name in raised.value.detail
