"""S3 and MinIO: the declared rate limit and retry budget are the ones applied, and bucket options page at the store.

The retry tests run real aiobotocore against a loopback server that answers as S3
does when it throttles (503 SlowDown), so they check botocore's own retry loop with
the budget the connector hands it, not a mock of it.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from object_store_behaviour_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    FakeS3DataSource,
    Response,
    make_connector,
)

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.sources.client.resilience import ResiliencePolicy

SLOW_DOWN = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<Error><Code>SlowDown</Code><Message>Please reduce your request rate.</Message></Error>"
)
EMPTY_LISTING = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    b"<Name>b</Name><Prefix></Prefix><KeyCount>0</KeyCount><MaxKeys>1000</MaxKeys>"
    b"<IsTruncated>false</IsTruncated></ListBucketResult>"
)


class ThrottlingStore:
    """Answers the first ``throttled`` requests with 503 SlowDown, then an empty listing."""

    def __init__(self, throttled: int) -> None:
        self.throttled = throttled
        self.requests = 0

    async def handle(self, request: web.Request) -> web.Response:
        self.requests += 1
        if self.requests <= self.throttled:
            return web.Response(status=503, body=SLOW_DOWN, content_type="application/xml")
        return web.Response(status=200, body=EMPTY_LISTING, content_type="application/xml")


@pytest.fixture
async def serve() -> AsyncIterator:
    servers: list[TestServer] = []

    async def start(store: ThrottlingStore) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", store.handle)
        server = TestServer(app, host="127.0.0.1")
        await server.start_server()
        servers.append(server)
        return f"http://127.0.0.1:{server.port}"

    yield start
    for server in servers:
        await server.close()


def minio_connector_on(endpoint: str, max_retries: int) -> BaseConnector:
    """A MinIO connector whose client talks to ``endpoint``, with ``max_retries`` as its declared budget."""
    from app.sources.client.minio.minio import MinIOClient, MinIORESTClientViaAccessKey
    from app.sources.external.minio.minio import MinIODataSource

    connector = make_connector("minio", FakeObjectStore(), FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService())
    # Small delays keep the test fast; the attempt count is what is checked.
    connector._resilience = ResiliencePolicy(rate_limit=50, max_retries=max_retries, base_delay=0.01, max_delay=0.05)
    connector._resilience_loaded = True
    client = MinIOClient(MinIORESTClientViaAccessKey("key", "secret", endpoint, use_ssl=False, verify_ssl=False))
    connector.data_source = MinIODataSource(connector._with_retry_budget(client))
    return connector


@pytest.mark.parametrize("kind", ["s3", "minio"])
def test_the_declared_budget_is_on_the_connector(kind: str) -> None:
    connector = make_connector(kind, FakeObjectStore(), FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService())

    assert connector.resilience is not None
    assert (connector.resilience.rate_limit, connector.resilience.max_retries) == (50, 3)


class TestThrottling:
    @pytest.mark.asyncio
    async def test_a_throttled_listing_is_retried_and_succeeds(self, serve) -> None:
        store = ThrottlingStore(throttled=2)
        connector = minio_connector_on(await serve(store), max_retries=3)

        response = await connector.data_source.list_objects_v2(Bucket="b")

        assert response.success, response.error
        assert store.requests == 3

    @pytest.mark.asyncio
    async def test_retries_stop_at_the_declared_budget_and_report_the_store_reason(self, serve) -> None:
        store = ThrottlingStore(throttled=100)
        connector = minio_connector_on(await serve(store), max_retries=2)

        response = await connector.data_source.list_objects_v2(Bucket="b")

        assert not response.success
        assert response.error.startswith("SlowDown") and response.status_code == 503
        assert store.requests == 3

    @pytest.mark.asyncio
    async def test_a_sync_finishes_under_throttling(self, serve) -> None:
        store = ThrottlingStore(throttled=2)
        connector = minio_connector_on(await serve(store), max_retries=3)
        connector.notify = AsyncMock()

        await connector.run_sync()

        assert store.requests >= 3
        assert connector.full_sync_coverage().complete
        assert connector.notify.await_count == 0


class TestBucketOptions:
    @staticmethod
    def connector_with(list_buckets: AsyncMock) -> BaseConnector:
        connector = make_connector("s3", FakeObjectStore(), FakeRecordsDb(), FakeCheckpointStore(), FakeConfigService())
        connector.data_source.list_buckets = list_buckets
        return connector

    @pytest.mark.asyncio
    async def test_aws_pages_and_searches_at_the_store(self) -> None:
        list_buckets = AsyncMock(return_value=Response(True, {"Buckets": [{"Name": "logs-a"}, {"Name": "logs-b"}], "ContinuationToken": "t2"}))
        connector = self.connector_with(list_buckets)

        first = await connector.get_filter_options("buckets", limit=2, search="logs")
        await connector.get_filter_options("buckets", limit=2, search="logs", cursor=first.cursor)

        assert [o.id for o in first.options] == ["logs-a", "logs-b"]
        assert first.has_more and first.cursor == "t2"
        assert list_buckets.await_args_list[0].kwargs == {"MaxBuckets": 2, "ContinuationToken": None, "Prefix": "logs"}
        assert list_buckets.await_args_list[1].kwargs["ContinuationToken"] == "t2"

    @pytest.mark.asyncio
    async def test_the_last_page_has_no_more(self) -> None:
        connector = self.connector_with(AsyncMock(return_value=Response(True, {"Buckets": [{"Name": "logs-c"}]})))

        page = await connector.get_filter_options("buckets", limit=2, search="logs", cursor="t2")

        assert [o.id for o in page.options] == ["logs-c"] and not page.has_more and page.cursor is None

    @pytest.mark.asyncio
    async def test_a_store_that_ignores_paging_is_paged_and_searched_here(self) -> None:
        """MinIO answers ListBuckets with every bucket, whatever MaxBuckets and Prefix say."""
        every = [{"Name": n} for n in ("a-1", "a-2", "a-3", "b-1")]
        connector = self.connector_with(AsyncMock(return_value=Response(True, {"Buckets": every})))

        first = await connector.get_filter_options("buckets", page=1, limit=2, search="a-")
        second = await connector.get_filter_options("buckets", page=2, limit=2, search="a-")

        assert [o.id for o in first.options] == ["a-1", "a-2"] and first.has_more and first.cursor is None
        assert [o.id for o in second.options] == ["a-3"] and not second.has_more


_paced_now: ContextVar[bool] = ContextVar("paced_now", default=False)


class RecordingPolicy(ResiliencePolicy):
    """Marks the calls made under ``guard()``; a context variable keeps concurrent buckets apart."""

    @asynccontextmanager
    async def guard(self) -> AsyncIterator[None]:
        token = _paced_now.set(True)
        try:
            yield
        finally:
            _paced_now.reset(token)


SOURCE_CALLS = ("list_buckets", "get_bucket_location", "list_objects_v2", "head_object")


class TestEveryRequestIsPaced:
    @staticmethod
    def recording(source: FakeS3DataSource) -> list[tuple[str, bool]]:
        calls: list[tuple[str, bool]] = []
        for name in SOURCE_CALLS:
            method = getattr(source, name)

            def wrap(method: Callable[..., Awaitable[Response]], name: str) -> Callable[..., Awaitable[Response]]:
                async def call(*args: object, **kwargs: object) -> Response:
                    calls.append((name, _paced_now.get()))
                    return await method(*args, **kwargs)
                return call

            setattr(source, name, wrap(method, name))
        return calls

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", ["s3", "minio"])
    async def test_sync_access_check_bucket_options_and_reindex_go_through_the_rate_limit(
        self, kind: str, store: FakeObjectStore, db: FakeRecordsDb,
    ) -> None:
        connector = make_connector(kind, store, db, FakeCheckpointStore(), FakeConfigService())
        connector.bucket_name = None
        connector._resilience = RecordingPolicy(rate_limit=50, max_retries=3)
        connector._resilience_loaded = True
        db.reindex_existing_records = AsyncMock()
        store.put("a.txt")
        calls = self.recording(connector.data_source)

        await connector.run_sync()
        assert await connector.test_connection_and_access()
        await connector.get_filter_options("buckets", limit=2)
        connector.bucket_regions.clear()
        await connector.reindex_records(list(db.records.values()))

        # MinIO has no bucket regions to look up.
        assert {name for name, _ in calls} >= {"list_buckets", "list_objects_v2", "head_object"}
        assert [name for name, paced in calls if not paced] == []
