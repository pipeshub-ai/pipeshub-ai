"""What the R2 connector adds to the shared S3 sync: its settings, links, tokens and R2's own refusals.

The sync loop itself is covered for S3 and MinIO by the other files here. These run it once
through the R2 class, so a wrong override or a missing registration shows.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from object_store_behaviour_fakes import (
    BUCKET,
    CONNECTOR_ID,
    FakeCheckpointStore,
    FakeConfigService,
    FakeObjectStore,
    FakeRecordsDb,
    FakeS3DataSource,
    Response,
)

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.cloudflare_r2.connector import CloudflareR2Connector
from app.sources.external.cloudflare_r2.cloudflare_r2 import CloudflareR2DataSource

ACCOUNT = "0123456789abcdef0123456789abcdef"
ACCESS_KEY = "access-key-id-for-the-test"
SECRET_KEY = "secret-key-that-must-stay-out-of-logs"
BUCKETS_URL = f"https://dash.cloudflare.com/{ACCOUNT}/r2/default/buckets"
GOOD_AUTH = {"accountId": ACCOUNT, "accessKey": ACCESS_KEY, "secretKey": SECRET_KEY}


class R2ConfigService(FakeConfigService):
    def __init__(self, auth: dict[str, str]) -> None:
        super().__init__()
        self.auth = auth

    async def get_config(self, path: str, default: object = None, **kwargs: object) -> object:
        config = await super().get_config(path, default, **kwargs)
        return {**config, "auth": self.auth} if path.endswith("/config") else config


def build(db: FakeRecordsDb, checkpoints: FakeCheckpointStore, config: object, scope: str = "team") -> CloudflareR2Connector:
    return CloudflareR2Connector(
        logging.getLogger("test.objstore.r2"), db, checkpoints, config, CONNECTOR_ID, scope, "creator-1"
    )


@pytest.fixture
def source(store: FakeObjectStore) -> FakeS3DataSource:
    return FakeS3DataSource(store)


@pytest.fixture
def connector(
    db: FakeRecordsDb, checkpoints: FakeCheckpointStore, config: FakeConfigService, source: FakeS3DataSource,
) -> CloudflareR2Connector:
    """Syncs one bucket, as the other object store behaviour tests do."""
    r2 = build(db, checkpoints, config)
    r2.data_source = source
    r2.base_console_url = BUCKETS_URL
    r2.bucket_name = BUCKET
    r2.batch_size = 2
    r2.list_page_size = 2
    return r2


class TestSettings:
    @pytest.mark.asyncio
    async def test_saved_settings_build_the_client_and_the_links(self, db, checkpoints) -> None:
        auth = {**GOOD_AUTH, "accountId": f" {ACCOUNT.upper()} "}
        r2 = build(db, checkpoints, R2ConfigService(auth))

        assert await r2.init() is True

        assert isinstance(r2.data_source, CloudflareR2DataSource)
        assert r2.data_source._r2_client.get_endpoint_url() == f"https://{ACCOUNT}.r2.cloudflarestorage.com"
        assert r2.base_console_url == BUCKETS_URL

    @pytest.mark.asyncio
    async def test_a_jurisdiction_changes_the_endpoint_and_the_links(self, db, checkpoints) -> None:
        r2 = build(db, checkpoints, R2ConfigService({**GOOD_AUTH, "jurisdiction": "EU"}))

        await r2.init()

        assert r2.data_source._r2_client.get_endpoint_url() == f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"
        assert r2.base_console_url == f"https://dash.cloudflare.com/{ACCOUNT}/r2/eu/buckets"
        assert r2.bucket_name is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"accountId": "not-an-account"}, "32 hexadecimal"),
            ({"accountId": ""}, "32 hexadecimal"),
            ({"accessKey": ""}, "required"),
            ({"secretKey": ""}, "required"),
            ({"jurisdiction": "mars"}, "jurisdiction"),
        ],
    )
    async def test_refused_settings_tell_the_admin_why_without_the_secret(
        self, db, checkpoints, caplog, change, reason,
    ) -> None:
        r2 = build(db, checkpoints, R2ConfigService({**GOOD_AUTH, **change}))

        with caplog.at_level(logging.DEBUG), pytest.raises(ConnectorInitError, match=reason) as refused:
            await r2.init()

        assert r2.data_source is None
        assert SECRET_KEY not in str(refused.value)
        assert SECRET_KEY not in caplog.text

    @pytest.mark.asyncio
    async def test_a_connector_with_no_saved_config_is_refused(self, db, checkpoints) -> None:
        config = MagicMock()
        config.get_config = AsyncMock(return_value=None)

        with pytest.raises(ConnectorInitError, match="configuration"):
            await build(db, checkpoints, config).init()


    @pytest.mark.asyncio
    async def test_the_connector_keeps_the_processor_the_factory_initialized(self, db, checkpoints, config) -> None:
        r2 = await CloudflareR2Connector.create_connector(
            logging.getLogger("test.objstore.r2"), checkpoints, config, CONNECTOR_ID, "team", "creator-1",
            data_entities_processor=db, org_id="org-1",
        )

        assert r2.data_entities_processor is db


class TestSync:
    @pytest.mark.asyncio
    async def test_a_first_sync_writes_the_bucket_folders_and_files_with_dashboard_links(
        self, connector, store, db,
    ) -> None:
        store.put("a b#1.txt", "hello")
        store.put("docs/q&a.txt", "world")

        await connector.run_sync()

        records = db.by_path()
        file = records[f"{BUCKET}/a b#1.txt"]
        assert file.connector_name == Connectors.CLOUDFLARE_R2
        assert file.version == 0
        assert file.weburl == f"{BUCKETS_URL}/{BUCKET}/objects/a%20b%231.txt/details"
        assert records[f"{BUCKET}/docs/q&a.txt"].weburl == f"{BUCKETS_URL}/{BUCKET}/objects/docs%2Fq%26a.txt/details"
        assert records[f"{BUCKET}/docs"].weburl == f"{BUCKETS_URL}/{BUCKET}?prefix=docs/"
        assert db.record_groups[BUCKET].web_url == f"{BUCKETS_URL}/{BUCKET}"

    @pytest.mark.asyncio
    async def test_changes_between_syncs_reach_the_index(self, connector, store, db) -> None:
        store.put("keep.txt", "same")
        store.put("edit.txt", "before")
        store.put("gone.txt", "bye")
        await connector.run_sync()
        kept = db.by_path()[f"{BUCKET}/keep.txt"]
        edited_revision = db.by_path()[f"{BUCKET}/edit.txt"].external_revision_id
        db.written.clear()

        store.put("edit.txt", "after")
        store.delete("gone.txt")
        await connector.run_incremental_sync()

        records = db.by_path()
        assert f"{BUCKET}/gone.txt" not in records
        assert records[f"{BUCKET}/edit.txt"].external_revision_id != edited_revision
        assert records[f"{BUCKET}/keep.txt"].id == kept.id
        assert f"{BUCKET}/keep.txt" not in db.written

    @pytest.mark.asyncio
    async def test_downloading_does_not_ask_r2_for_a_bucket_location(self, connector, source, store, db) -> None:
        store.put("a.txt", "hello")
        source.get_bucket_location = AsyncMock(side_effect=AssertionError("R2 signs for auto"))
        await connector.run_sync()

        url = await connector.get_signed_url(db.by_path()[f"{BUCKET}/a.txt"])

        assert source.object_at(url).body == b"hello"
        source.get_bucket_location.assert_not_called()


class TestTokens:
    @pytest.mark.asyncio
    async def test_a_token_that_cannot_list_buckets_is_told_which_token_works(self, connector, source) -> None:
        connector.bucket_name = None
        source.list_buckets = AsyncMock(
            return_value=Response(False, error="AccessDenied: Access Denied", status_code=403)
        )

        with pytest.raises(ConnectorInitError) as refused:
            await connector.test_connection_and_access()

        assert "AccessDenied: Access Denied" in str(refused.value)
        assert "Admin Read only" in str(refused.value)
        assert "s3:" not in str(refused.value)

    @pytest.mark.asyncio
    async def test_a_token_without_access_to_the_bucket_is_not_told_about_aws_policies(
        self, connector, source,
    ) -> None:
        source.denied.add(BUCKET)

        with pytest.raises(ConnectorInitError) as refused:
            await connector.test_connection_and_access()

        assert "can list buckets and read objects" in str(refused.value)
        assert "arn:aws" not in str(refused.value)

    @pytest.mark.asyncio
    async def test_every_selected_bucket_the_token_cannot_read_is_named(self, connector, source, monkeypatch) -> None:
        connector.bucket_name = None
        source.add_bucket("other")
        source.denied.update({BUCKET, "other"})
        monkeypatch.setattr(
            "app.connectors.sources.s3.base_connector.included_names", lambda filters, key: [BUCKET, "other"]
        )

        with pytest.raises(ConnectorInitError) as refused:
            await connector.test_connection_and_access()

        assert f"bucket '{BUCKET}'" in str(refused.value)
        assert "bucket 'other'" in str(refused.value)
        assert "arn:aws" not in str(refused.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", ["SignatureDoesNotMatch", "InvalidAccessKeyId"])
    async def test_a_wrong_key_is_not_told_to_change_the_token_permissions(self, connector, source, code) -> None:
        """R2 answered SignatureDoesNotMatch with a 403 when a real run used a wrong secret."""
        connector.bucket_name = None
        source.list_buckets = AsyncMock(
            return_value=Response(False, error=f"{code}: check the Secret Key", status_code=403)
        )

        with pytest.raises(ConnectorInitError, match="check the Secret Key") as refused:
            await connector.test_connection_and_access()

        assert "Admin Read only" not in str(refused.value)
        assert "s3:" not in str(refused.value)

    @pytest.mark.asyncio
    async def test_a_failure_that_is_not_about_access_keeps_its_own_reason(self, connector, source) -> None:
        source.list_objects_v2 = AsyncMock(
            return_value=Response(False, error="InvalidArgument: Credential access key has length 64", status_code=400)
        )

        with pytest.raises(ConnectorInitError, match="length 64") as refused:
            await connector.test_connection_and_access()

        assert "Admin Read only" not in str(refused.value)


class TestBucketListingOnR2:
    """The real data source in front of a store that refuses what R2 refuses."""

    @pytest.fixture
    def r2(self, connector, source) -> CloudflareR2Connector:
        async def call(operation: str, **params: object) -> Response:
            if operation == "list_buckets" and {"MaxBuckets", "Prefix"} & params.keys():
                return Response(False, error="NotImplemented: ListBuckets search parameter max-buckets not implemented")
            return await getattr(source, operation)(**params)

        data_source = CloudflareR2DataSource(MagicMock())
        data_source._call = call
        connector.data_source = data_source
        connector.bucket_name = None
        source.add_bucket("other")
        return connector

    @pytest.mark.asyncio
    async def test_discovery_lists_every_bucket(self, r2) -> None:
        listed = await r2._list_all_buckets()

        assert listed.success, listed.error
        assert [b["Name"] for b in listed.data["Buckets"]] == [BUCKET, "other"]

    @pytest.mark.asyncio
    async def test_the_bucket_names_options_load_and_search(self, r2) -> None:
        everything = await r2.get_filter_options("buckets", limit=20)
        searched = await r2.get_filter_options("buckets", limit=20, search="oth")

        assert [o.id for o in everything.options] == [BUCKET, "other"]
        assert [o.id for o in searched.options] == ["other"]
