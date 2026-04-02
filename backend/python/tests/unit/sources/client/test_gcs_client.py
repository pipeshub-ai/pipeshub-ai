"""Unit tests for Google Cloud Storage client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.gcs.gcs import (
    GCSClient,
    GCSRESTClientViaServiceAccount,
    GCSResponse,
    GCSServiceAccountConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_gcs_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


@pytest.fixture
def sample_sa_info():
    return {
        "type": "service_account",
        "project_id": "test-project",
        "private_key_id": "key-id",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----\n",
        "client_email": "svc@test.iam.gserviceaccount.com",
        "client_id": "123",
    }


# ---------------------------------------------------------------------------
# GCSResponse
# ---------------------------------------------------------------------------


class TestGCSResponse:
    def test_success(self):
        resp = GCSResponse(success=True, data={"key": "val"})
        assert resp.success is True

    def test_error(self):
        resp = GCSResponse(success=False, error="oops")
        assert resp.error == "oops"

    def test_to_dict(self):
        resp = GCSResponse(success=True, data={"k": "v"}, message="ok")
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == {"k": "v"}

    def test_to_json(self):
        resp = GCSResponse(success=True)
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_defaults(self):
        resp = GCSResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# GCSRESTClientViaServiceAccount
# ---------------------------------------------------------------------------


class TestGCSRESTClientViaServiceAccount:
    def test_init(self, sample_sa_info):
        client = GCSRESTClientViaServiceAccount(sample_sa_info)
        assert client.project_id == "test-project"
        assert client.bucket_name is None

    def test_init_with_options(self, sample_sa_info):
        client = GCSRESTClientViaServiceAccount(
            sample_sa_info, project_id="override", bucket_name="my-bucket"
        )
        assert client.project_id == "override"
        assert client.bucket_name == "my-bucket"

    @patch("app.sources.client.gcs.gcs.service_account.Credentials.from_service_account_info")
    def test_get_credentials(self, mock_from_sa, sample_sa_info):
        mock_cred = MagicMock()
        mock_from_sa.return_value = mock_cred
        client = GCSRESTClientViaServiceAccount(sample_sa_info)
        result = client._get_credentials()
        assert result is mock_cred
        # Second call returns cached
        result2 = client._get_credentials()
        assert result is result2
        mock_from_sa.assert_called_once()

    @patch("app.sources.client.gcs.gcs.storage.Client")
    @patch("app.sources.client.gcs.gcs.service_account.Credentials.from_service_account_info")
    def test_get_client(self, mock_from_sa, mock_storage_client, sample_sa_info):
        mock_from_sa.return_value = MagicMock()
        mock_storage_client.return_value = MagicMock()
        client = GCSRESTClientViaServiceAccount(sample_sa_info)
        result = client.get_client()
        assert result is not None
        # Second call returns cached
        result2 = client.get_client()
        assert result is result2

    def test_get_bucket_name(self, sample_sa_info):
        client = GCSRESTClientViaServiceAccount(sample_sa_info, bucket_name="bkt")
        assert client.get_bucket_name() == "bkt"

    def test_get_bucket_name_none(self, sample_sa_info):
        client = GCSRESTClientViaServiceAccount(sample_sa_info)
        assert client.get_bucket_name() is None

    def test_set_bucket_name(self, sample_sa_info):
        client = GCSRESTClientViaServiceAccount(sample_sa_info)
        client.set_bucket_name("new-bucket")
        assert client.get_bucket_name() == "new-bucket"

    def test_get_project_id(self, sample_sa_info):
        client = GCSRESTClientViaServiceAccount(sample_sa_info)
        assert client.get_project_id() == "test-project"

    def test_get_credentials_info(self, sample_sa_info):
        client = GCSRESTClientViaServiceAccount(sample_sa_info)
        info = client.get_credentials_info()
        assert info["project_id"] == "test-project"
        assert info["client_email"] == "svc@test.iam.gserviceaccount.com"
        assert "private_key" not in info


# ---------------------------------------------------------------------------
# GCSServiceAccountConfig
# ---------------------------------------------------------------------------


class TestGCSServiceAccountConfig:
    def test_create_client(self, sample_sa_info):
        cfg = GCSServiceAccountConfig(service_account_info=sample_sa_info)
        client = cfg.create_client()
        assert isinstance(client, GCSRESTClientViaServiceAccount)

    def test_create_client_with_options(self, sample_sa_info):
        cfg = GCSServiceAccountConfig(
            service_account_info=sample_sa_info,
            project_id="override",
            bucket_name="bkt",
        )
        client = cfg.create_client()
        assert client.project_id == "override"
        assert client.bucket_name == "bkt"

    def test_to_dict(self, sample_sa_info):
        cfg = GCSServiceAccountConfig(
            service_account_info=sample_sa_info, bucket_name="bkt"
        )
        d = cfg.to_dict()
        assert d["bucket_name"] == "bkt"
        assert d["client_email"] == "svc@test.iam.gserviceaccount.com"


# ---------------------------------------------------------------------------
# GCSClient
# ---------------------------------------------------------------------------


class TestGCSClient:
    def test_init(self, sample_sa_info):
        rest = GCSRESTClientViaServiceAccount(sample_sa_info)
        client = GCSClient(rest)
        assert client.get_client() is rest

    @patch("app.sources.client.gcs.gcs.storage.Client")
    @patch("app.sources.client.gcs.gcs.service_account.Credentials.from_service_account_info")
    def test_get_storage_client(self, mock_from_sa, mock_storage_client, sample_sa_info):
        mock_from_sa.return_value = MagicMock()
        mock_storage_client.return_value = MagicMock()
        rest = GCSRESTClientViaServiceAccount(sample_sa_info)
        client = GCSClient(rest)
        result = client.get_storage_client()
        assert result is not None

    def test_get_bucket_name(self, sample_sa_info):
        rest = GCSRESTClientViaServiceAccount(sample_sa_info, bucket_name="bkt")
        client = GCSClient(rest)
        assert client.get_bucket_name() == "bkt"

    def test_set_bucket_name(self, sample_sa_info):
        rest = GCSRESTClientViaServiceAccount(sample_sa_info)
        client = GCSClient(rest)
        client.set_bucket_name("new")
        assert client.get_bucket_name() == "new"

    def test_get_project_id(self, sample_sa_info):
        rest = GCSRESTClientViaServiceAccount(sample_sa_info)
        client = GCSClient(rest)
        assert client.get_project_id() == "test-project"

    def test_get_credentials_info(self, sample_sa_info):
        rest = GCSRESTClientViaServiceAccount(sample_sa_info)
        client = GCSClient(rest)
        info = client.get_credentials_info()
        assert "project_id" in info

    def test_build_with_config(self, sample_sa_info):
        cfg = GCSServiceAccountConfig(service_account_info=sample_sa_info)
        client = GCSClient.build_with_config(cfg)
        assert isinstance(client, GCSClient)

    @pytest.mark.asyncio
    async def test_build_from_services_dict(self, logger, mock_config_service, sample_sa_info):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "serviceAccountJson": sample_sa_info,
                    "bucket": "my-bucket",
                }
            }
        )
        client = await GCSClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, GCSClient)

    @pytest.mark.asyncio
    async def test_build_from_services_json_string(self, logger, mock_config_service, sample_sa_info):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "serviceAccountJson": json.dumps(sample_sa_info),
                }
            }
        )
        client = await GCSClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, GCSClient)

    @pytest.mark.asyncio
    async def test_build_from_services_missing_sa_json(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {}}
        )
        with pytest.raises(ValueError, match="Service account JSON is required"):
            await GCSClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_invalid_json_string(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"serviceAccountJson": "not-json"}}
        )
        with pytest.raises(ValueError, match="Invalid service account JSON"):
            await GCSClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_required_fields(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"serviceAccountJson": {"type": "service_account"}}}
        )
        with pytest.raises(ValueError, match="missing required fields"):
            await GCSClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await GCSClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await GCSClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await GCSClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, logger, mock_config_service):
        """Cover the exception path in _get_connector_config."""
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get GCS connector configuration"):
            await GCSClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_none_config_via_patched(self, logger, mock_config_service):
        """Cover the `not config` branch at line 195 by patching to return None."""
        with patch.object(
            GCSClient, "_get_connector_config", new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="Failed to get GCS connector configuration"):
                await GCSClient.build_from_services(
                    logger, mock_config_service, "inst-1"
                )

    @pytest.mark.asyncio
    async def test_build_from_services_project_id_from_auth(self, logger, mock_config_service, sample_sa_info):
        """Cover projectId override from auth_config (line 231 projectId branch)."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "serviceAccountJson": sample_sa_info,
                    "projectId": "override-project",
                    "bucket": "my-bucket",
                }
            }
        )
        client = await GCSClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert client.get_project_id() == "override-project"

    @pytest.mark.asyncio
    async def test_build_from_services_project_id_fallback_to_sa(self, logger, mock_config_service, sample_sa_info):
        """Cover projectId fallback to service_account_info when auth has no projectId."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "serviceAccountJson": sample_sa_info,
                }
            }
        )
        client = await GCSClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert client.get_project_id() == "test-project"
