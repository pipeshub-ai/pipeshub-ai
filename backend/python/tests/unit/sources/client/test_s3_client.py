"""Unit tests for S3 client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.s3.s3 import (
    S3AccessKeyConfig,
    S3Client,
    S3RESTClientViaAccessKey,
    S3Response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_s3_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


@pytest.fixture
def rest_client():
    return S3RESTClientViaAccessKey(
        access_key_id="AKID",
        secret_access_key="SECRET",
        region_name="us-east-1",
        bucket_name="my-bucket",
    )


# ---------------------------------------------------------------------------
# S3Response
# ---------------------------------------------------------------------------


class TestS3Response:
    def test_success(self):
        resp = S3Response(success=True, data={"key": "val"})
        assert resp.success is True

    def test_error(self):
        resp = S3Response(success=False, error="oops")
        assert resp.error == "oops"

    def test_to_dict(self):
        resp = S3Response(success=True, data={"k": "v"}, message="ok")
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"k": "v"}

    def test_to_json(self):
        resp = S3Response(success=True)
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_defaults(self):
        resp = S3Response(success=True)
        assert resp.data is None
        assert resp.error is None


# ---------------------------------------------------------------------------
# S3RESTClientViaAccessKey
# ---------------------------------------------------------------------------


class TestS3RESTClientViaAccessKey:
    def test_init(self, rest_client):
        assert rest_client.access_key_id == "AKID"
        assert rest_client.secret_access_key == "SECRET"
        assert rest_client.region_name == "us-east-1"
        assert rest_client.bucket_name == "my-bucket"
        assert rest_client.session is None

    @pytest.mark.asyncio
    async def test_create_session(self, rest_client: S3RESTClientViaAccessKey) -> None:
        session = rest_client.create_session()
        assert session.get_config_variable("region") == "us-east-1"
        credentials = await session.get_credentials()
        assert credentials.access_key == "AKID"
        assert credentials.secret_key == "SECRET"

    @pytest.mark.asyncio
    async def test_session_builds_an_s3_client_for_the_region(self, rest_client: S3RESTClientViaAccessKey) -> None:
        async with rest_client.get_session().create_client("s3") as s3:
            assert s3.meta.region_name == "us-east-1"
            assert s3.meta.service_model.service_name == "s3"

    @patch("app.sources.client.s3.s3.AioSession")
    def test_empty_keys_leave_the_default_credential_chain(self, mock_session_cls: MagicMock) -> None:
        S3RESTClientViaAccessKey("", "", "eu-west-1").create_session()
        mock_session_cls.return_value.set_credentials.assert_not_called()
        mock_session_cls.return_value.set_config_variable.assert_called_once_with("region", "eu-west-1")

    def test_get_session(self, rest_client):
        session = rest_client.get_session()
        assert session is not None
        # Second call returns cached
        session2 = rest_client.get_session()
        assert session is session2

    @pytest.mark.asyncio
    @patch("app.sources.client.s3.s3.AioSession")
    async def test_get_s3_client(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session.create_client.return_value = MagicMock()
        mock_session_cls.return_value = mock_session
        client = S3RESTClientViaAccessKey("AKID", "SECRET", "us-east-1")
        result = await client.get_s3_client()
        mock_session.create_client.assert_called_once_with("s3")

    def test_get_bucket_name(self, rest_client):
        assert rest_client.get_bucket_name() == "my-bucket"

    def test_get_bucket_name_none(self):
        client = S3RESTClientViaAccessKey("AKID", "SECRET", "us-east-1")
        assert client.get_bucket_name() is None

    def test_set_bucket_name(self, rest_client):
        rest_client.set_bucket_name("new-bucket")
        assert rest_client.get_bucket_name() == "new-bucket"

    def test_get_region_name(self, rest_client):
        assert rest_client.get_region_name() == "us-east-1"

    def test_get_credentials(self, rest_client):
        creds = rest_client.get_credentials()
        assert creds["aws_access_key_id"] == "AKID"
        assert creds["aws_secret_access_key"] == "SECRET"
        assert creds["region_name"] == "us-east-1"


# ---------------------------------------------------------------------------
# S3AccessKeyConfig
# ---------------------------------------------------------------------------


class TestS3AccessKeyConfig:
    def test_create_client(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET", region_name="us-west-2"
        )
        client = cfg.create_client()
        assert isinstance(client, S3RESTClientViaAccessKey)
        assert client.region_name == "us-west-2"

    def test_create_client_with_bucket(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET",
            region_name="us-west-2", bucket_name="bkt"
        )
        client = cfg.create_client()
        assert client.bucket_name == "bkt"

    def test_to_dict(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET", region_name="us-west-2"
        )
        d = cfg.to_dict()
        assert d["access_key_id"] == "AKID"
        assert d["ssl"] is True

    def test_defaults(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET", region_name="us-east-1"
        )
        assert cfg.bucket_name is None
        assert cfg.ssl is True


# ---------------------------------------------------------------------------
# S3Client
# ---------------------------------------------------------------------------


class TestS3Client:
    def test_init(self, rest_client):
        client = S3Client(rest_client)
        assert client.get_client() is rest_client

    def test_get_bucket_name(self, rest_client):
        client = S3Client(rest_client)
        assert client.get_bucket_name() == "my-bucket"

    def test_set_bucket_name(self, rest_client):
        client = S3Client(rest_client)
        client.set_bucket_name("new")
        assert client.get_bucket_name() == "new"

    def test_get_credentials(self, rest_client):
        client = S3Client(rest_client)
        creds = client.get_credentials()
        assert creds["aws_access_key_id"] == "AKID"

    def test_get_region_name(self, rest_client):
        client = S3Client(rest_client)
        assert client.get_region_name() == "us-east-1"

    def test_get_session(self, rest_client):
        client = S3Client(rest_client)
        session = client.get_session()
        assert session is not None

    def test_build_with_config(self):
        cfg = S3AccessKeyConfig(
            access_key_id="AKID", secret_access_key="SECRET", region_name="us-east-1"
        )
        client = S3Client.build_with_config(cfg)
        assert isinstance(client, S3Client)

    @pytest.mark.asyncio
    async def test_build_from_services_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "ACCESS_KEY",
                    "accessKey": "AKID",
                    "secretKey": "SECRET",
                    "region": "us-west-2",
                    "bucket": "bkt",
                }
            }
        )
        client = await S3Client.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, S3Client)

    @pytest.mark.asyncio
    async def test_build_from_services_default_region(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "accessKey": "AKID",
                    "secretKey": "SECRET",
                }
            }
        )
        client = await S3Client.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, S3Client)

    @pytest.mark.asyncio
    async def test_build_from_services_missing_keys(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "ACCESS_KEY"}}
        )
        with pytest.raises(ValueError, match="Access key ID and secret"):
            await S3Client.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_invalid_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "UNSUPPORTED"}}
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await S3Client.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await S3Client.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await S3Client._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await S3Client._get_connector_config(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, logger, mock_config_service):
        """Cover the exception path in _get_connector_config."""
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get S3 connector configuration"):
            await S3Client._get_connector_config(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    @patch("app.sources.client.s3.s3.AioSession")
    async def test_get_s3_client_via_wrapper(self, mock_session_cls, rest_client):
        """Cover S3Client.get_s3_client (line 137)."""
        mock_session = MagicMock()
        mock_session.create_client.return_value = MagicMock()
        mock_session_cls.return_value = mock_session
        client = S3Client(rest_client)
        result = await client.get_s3_client()
        mock_session.create_client.assert_called_once_with("s3")

    @pytest.mark.asyncio
    async def test_build_from_services_none_config_via_patched(self, logger, mock_config_service):
        """Cover the `not config` branch at line 190 by patching to return None."""
        with patch.object(
            S3Client, "_get_connector_config", new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="Failed to get S3 connector configuration"):
                await S3Client.build_from_services(
                    logger, mock_config_service, "inst-1"
                )


# ---------------------------------------------------------------------------
# IAM role (STS AssumeRole) auth
# ---------------------------------------------------------------------------


class _FakeSTS:
    """STS client stand-in: hands out numbered temporary credentials."""

    def __init__(self, expiration) -> None:
        self.expiration = expiration
        self.calls = []

    async def __aenter__(self) -> "_FakeSTS":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def assume_role(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        n = len(self.calls)
        return {
            "Credentials": {
                "AccessKeyId": f"ASIA{n}",
                "SecretAccessKey": f"TMPSECRET{n}",
                "SessionToken": f"TOKEN{n}",
                "Expiration": self.expiration,
            }
        }


def _patch_sts(fake_sts) -> tuple:
    """Route only the base-key session (the STS caller) to the fake; keep real sessions otherwise."""
    from app.sources.client.s3 import s3 as s3_module

    real_create = s3_module.create_aio_session

    def factory(access_key_id, secret_access_key, region_name) -> object:
        if not access_key_id:
            return real_create(access_key_id, secret_access_key, region_name)
        base = MagicMock()
        base.base_args = (access_key_id, secret_access_key, region_name)
        base.create_client.return_value = fake_sts
        factory.base_sessions.append(base)
        return base

    factory.base_sessions = []
    return patch("app.sources.client.s3.s3.create_aio_session", side_effect=factory), factory


class TestS3AssumeRole:
    ROLE_CONFIG = {
        "auth": {
            "authType": "IAM_ROLE",
            "accessKey": "AKIDBASE",
            "secretKey": "BASESECRET",
            "roleArn": "arn:aws:iam::123456789012:role/pipeshub-reader",
            "externalId": "ext-123",
            "region": "eu-west-1",
        }
    }

    @pytest.mark.asyncio
    async def test_build_from_services_assumes_role_and_uses_temporary_credentials(
        self, logger, mock_config_service
    ) -> None:
        from datetime import datetime, timedelta, timezone

        mock_config_service.get_config = AsyncMock(return_value=self.ROLE_CONFIG)
        fake_sts = _FakeSTS(datetime.now(timezone.utc) + timedelta(hours=1))
        patcher, factory = _patch_sts(fake_sts)
        with patcher:
            client = await S3Client.build_from_services(logger, mock_config_service, "conn-42")
            session = client.get_session()
            frozen = await (await session.get_credentials()).get_frozen_credentials()

        assert fake_sts.calls == [{
            "RoleArn": "arn:aws:iam::123456789012:role/pipeshub-reader",
            "RoleSessionName": "pipeshub-s3-conn-42",
            "ExternalId": "ext-123",
        }]
        # STS is called with the base key only; S3 calls sign with the temporary credentials.
        assert factory.base_sessions[0].base_args[0] == "AKIDBASE"
        assert factory.base_sessions[0].create_client.call_args.args == ("sts",)
        assert (frozen.access_key, frozen.secret_key, frozen.token) == ("ASIA1", "TMPSECRET1", "TOKEN1")
        assert session.get_config_variable("region") == "eu-west-1"

    @pytest.mark.asyncio
    async def test_assume_role_without_external_id_omits_it(self, logger, mock_config_service) -> None:
        from datetime import datetime, timedelta, timezone

        auth = {k: v for k, v in self.ROLE_CONFIG["auth"].items() if k != "externalId"}
        mock_config_service.get_config = AsyncMock(return_value={"auth": auth})
        fake_sts = _FakeSTS(datetime.now(timezone.utc) + timedelta(hours=1))
        patcher, _ = _patch_sts(fake_sts)
        with patcher:
            client = await S3Client.build_from_services(logger, mock_config_service, "conn-42")
            await (await client.get_session().get_credentials()).get_frozen_credentials()

        assert "ExternalId" not in fake_sts.calls[0]

    @pytest.mark.asyncio
    async def test_credentials_near_expiry_are_refreshed_on_next_call(
        self, logger, mock_config_service
    ) -> None:
        from datetime import datetime, timedelta, timezone

        t0 = datetime.now(timezone.utc)
        mock_config_service.get_config = AsyncMock(return_value=self.ROLE_CONFIG)
        fake_sts = _FakeSTS(t0 + timedelta(hours=1))
        patcher, _ = _patch_sts(fake_sts)
        with patcher:
            client = await S3Client.build_from_services(logger, mock_config_service, "conn-42")
            creds = await client.get_session().get_credentials()
            creds._time_fetcher = lambda: t0
            first = await creds.get_frozen_credentials()
            again = await creds.get_frozen_credentials()
            # 5 minutes before expiry is inside botocore's mandatory refresh window.
            creds._time_fetcher = lambda: t0 + timedelta(minutes=55)
            refreshed = await creds.get_frozen_credentials()

        assert first.access_key == again.access_key == "ASIA1"
        assert refreshed.access_key == "ASIA2"
        assert len(fake_sts.calls) == 2

    @pytest.mark.asyncio
    async def test_build_from_services_iam_role_requires_role_arn(self, logger, mock_config_service) -> None:
        auth = {k: v for k, v in self.ROLE_CONFIG["auth"].items() if k != "roleArn"}
        mock_config_service.get_config = AsyncMock(return_value={"auth": auth})
        with pytest.raises(ValueError, match="Role ARN"):
            await S3Client.build_from_services(logger, mock_config_service, "conn-42")

    @pytest.mark.asyncio
    async def test_build_from_services_iam_role_requires_base_key(self, logger, mock_config_service) -> None:
        auth = {k: v for k, v in self.ROLE_CONFIG["auth"].items() if k != "secretKey"}
        mock_config_service.get_config = AsyncMock(return_value={"auth": auth})
        with pytest.raises(ValueError, match="Access key ID and secret"):
            await S3Client.build_from_services(logger, mock_config_service, "conn-42")
